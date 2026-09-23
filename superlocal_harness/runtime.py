from __future__ import annotations

import json
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .config import ModelProfile, RuntimeProfile, Settings
from .db import Database
from .events import EventStore
from .gateway import GatewayError, ModelGateway, ModelResponse, ToolCall
from .policy import PolicyAction, PolicyEngine
from .router import RouteDecision, StaticRouter
from .tools import ToolContext, ToolExecutor


CORE_SYSTEM_PROMPT = """
You are a bounded worker inside SuperLocal Harness. The durable mission record, policy engine,
event log, approval broker and verifier are authoritative; chat is not authoritative state.

Rules:
- Work only inside the supplied project root and runtime profile.
- Inspect before proposing edits. Prefer the smallest reversible step.
- Use update_state to preserve plans, facts, decisions, hypotheses, next actions and checks.
- Tool requests are proposals. The harness may deny them or pause for human approval.
- Never expose credentials, weaken approval policy, or try to bypass sealed paths.
- Never place financial trades, send public messages, or perform irreversible external actions.
- Do not claim success without relevant deterministic evidence.
- Stop when the goal is met, the budget/step limit is reached, or new information has stopped.
""".strip()


class MissionService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        events: EventStore,
        router: StaticRouter,
        gateway: ModelGateway,
        policy: PolicyEngine,
        tools: ToolExecutor,
    ):
        self.settings = settings
        self.db = db
        self.events = events
        self.router = router
        self.gateway = gateway
        self.policy = policy
        self.tools = tools
        self.pool = ThreadPoolExecutor(max_workers=settings.max_workers, thread_name_prefix="superlocal")
        self._active: set[str] = set()
        self._active_lock = threading.Lock()

    def resume_incomplete(self) -> None:
        missions = self.db.fetch_all(
            "SELECT id, status FROM missions WHERE status IN ('queued','running') ORDER BY created_at"
        )
        for mission in missions:
            if mission["status"] == "running":
                self.db.update_mission(mission["id"], status="queued")
                self.events.append(
                    f"mission:{mission['id']}", "MissionRecoveredAfterRestart", {}, actor="runtime"
                )
            self.submit(mission["id"])

    def create_mission(self, payload: dict[str, Any]) -> dict[str, Any]:
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("prompt is required")
        project = self.settings.validate_project_path(str(payload.get("project_path", "")))
        profile = self.settings.get_profile(str(payload.get("profile_id", "coding")))
        workflow = str(payload.get("workflow") or profile.default_workflow)
        if workflow not in {"solo", "plan_execute_verify"}:
            raise ValueError("workflow must be solo or plan_execute_verify")
        requested_model = str(payload.get("model_id", "auto"))
        self.settings.get_model(requested_model)
        mission_id = uuid.uuid4().hex
        title = str(payload.get("title") or prompt.splitlines()[0][:72]).strip() or "Untitled mission"
        budget = float(payload.get("budget_usd", self.settings.default_mission_budget_usd))
        if budget < 0:
            raise ValueError("budget_usd cannot be negative")
        local_only = bool(payload.get("local_only", False))
        stage = "planning" if workflow == "plan_execute_verify" else "execution"
        record = {
            "id": mission_id,
            "title": title,
            "prompt": prompt,
            "project_path": str(project),
            "profile_id": profile.id,
            "workflow": workflow,
            "requested_model_id": requested_model,
            "actual_model_id": None,
            "verifier_model_id": None,
            "local_only": local_only,
            "status": "queued",
            "stage": stage,
            "budget_usd": budget,
            "max_steps": int(payload.get("max_steps") or profile.max_steps),
        }
        state = {
            "goal": prompt,
            "plan": [],
            "facts": [],
            "decisions": [],
            "hypotheses": [],
            "open_questions": [],
            "next_actions": [],
            "acceptance_checks": [],
            "artifacts": [],
            "profile": profile.id,
            "workflow": workflow,
        }
        self.db.create_mission(record, state)
        self.db.add_message(mission_id, "user", prompt)
        event = self.events.append(
            f"mission:{mission_id}",
            "MissionCreated",
            {
                "title": title,
                "project_path": str(project),
                "profile_id": profile.id,
                "workflow": workflow,
                "requested_model_id": requested_model,
                "local_only": local_only,
                "budget_usd": budget,
            },
            actor="user",
            correlation_id=mission_id,
        )
        self.db.add_checkpoint(mission_id, stage, state, event["event_hash"])
        self.submit(mission_id)
        return self.get_detail(mission_id)

    def submit(self, mission_id: str) -> None:
        with self._active_lock:
            if mission_id in self._active:
                return
            self._active.add(mission_id)
        self.pool.submit(self._run_guarded, mission_id)

    def _run_guarded(self, mission_id: str) -> None:
        try:
            self._run(mission_id)
        except Exception as exc:
            mission = self.db.get_mission(mission_id)
            if mission and mission["status"] not in {"cancelled", "completed"}:
                self.db.update_mission(mission_id, status="failed", error=str(exc))
                self.events.append(
                    f"mission:{mission_id}", "MissionFailed", {"error": str(exc)}, actor="runtime"
                )
        finally:
            with self._active_lock:
                self._active.discard(mission_id)

    def _run(self, mission_id: str) -> None:
        while True:
            mission = self._require_mission(mission_id)
            if mission["status"] in {"cancelled", "completed", "failed", "needs_review", "budget_exhausted"}:
                return
            if mission["status"] == "waiting_approval":
                return
            if not self._budget_allows(mission):
                self.db.update_mission(mission_id, status="budget_exhausted")
                self.events.append(
                    f"mission:{mission_id}",
                    "BudgetExhausted",
                    {"mission_spent_usd": mission["spent_usd"], "mission_budget_usd": mission["budget_usd"]},
                    actor="runtime",
                )
                return
            self.db.update_mission(mission_id, status="running", error=None)
            if mission["stage"] == "planning":
                self._run_planning(mission)
                continue
            if mission["stage"] == "execution":
                outcome = self._run_execution(mission)
                if outcome == "continue":
                    continue
                return
            if mission["stage"] == "verification":
                outcome = self._run_verification(mission)
                if outcome == "continue":
                    continue
                return
            raise RuntimeError(f"Unknown mission stage: {mission['stage']}")

    def _run_planning(self, mission: dict[str, Any]) -> None:
        mission_id = mission["id"]
        profile = self.settings.get_profile(mission["profile_id"])
        messages = self._compile_messages(mission, profile, role="planner")
        messages.append(
            {
                "role": "user",
                "content": (
                    "Create a bounded execution plan. State acceptance checks, likely risks, what must remain sealed, "
                    "and the stop rule. Do not execute or claim completion yet."
                ),
            }
        )
        response = self._call(mission, profile, "planner", messages, tools=None)
        self.db.add_message(mission_id, "assistant", response.content, name="planner")
        state = self.db.get_state(mission_id)
        plan_lines = [
            re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
            for line in response.content.splitlines()
            if line.strip()
        ]
        state["plan"] = plan_lines[:30]
        self.db.put_state(mission_id, state)
        self.db.update_mission(mission_id, stage="execution", status="queued")
        self.events.append(
            f"mission:{mission_id}",
            "PlanAcceptedForExecution",
            {"planner_model_id": response.model_id, "plan_items": len(state["plan"])},
            actor="planner",
        )
        self._checkpoint(mission_id, "execution")

    def _run_execution(self, mission: dict[str, Any]) -> str:
        mission_id = mission["id"]
        profile = self.settings.get_profile(mission["profile_id"])
        root = Path(mission["project_path"])
        context = ToolContext(mission_id, root, profile)
        read_only = not profile.mutating_tools
        tool_schemas = self.tools.schemas(profile, read_only=read_only)

        current = self._require_mission(mission_id)
        if current["step_count"] >= current["max_steps"]:
            self.db.update_mission(mission_id, status="needs_review", error="Maximum agent steps reached")
            self.events.append(
                f"mission:{mission_id}", "CircuitBreakerOpened", {"reason": "max_steps"}, actor="runtime"
            )
            return "stop"

        messages = self._compile_messages(current, profile, role="executor")
        response = self._call(current, profile, "executor", messages, tools=tool_schemas)
        next_step = int(current["step_count"]) + 1
        self.db.update_mission(mission_id, step_count=next_step)
        tool_meta = [self._tool_call_payload(item) for item in response.tool_calls]
        self.db.add_message(
            mission_id,
            "assistant",
            response.content,
            name="executor",
            meta={"tool_calls": tool_meta, "model_id": response.model_id},
        )

        if not response.tool_calls:
            if current["workflow"] == "plan_execute_verify":
                self.db.update_mission(
                    mission_id, stage="verification", status="queued", result=response.content
                )
                self.events.append(
                    f"mission:{mission_id}",
                    "ExecutionDraftCompleted",
                    {"executor_model_id": response.model_id, "step_count": next_step},
                    actor="executor",
                )
                self._checkpoint(mission_id, "verification")
                return "continue"
            self.db.update_mission(mission_id, status="completed", stage="completed", result=response.content)
            self.events.append(
                f"mission:{mission_id}",
                "MissionCompleted",
                {"verified": False, "model_id": response.model_id},
                actor="runtime",
            )
            self._checkpoint(mission_id, "completed")
            return "stop"

        waiting = False
        for tool_call in response.tool_calls:
            decision = self.policy.decide(
                tool_call.name, tool_call.arguments, profile=profile, root=root
            )
            if decision.action == PolicyAction.DENY:
                result = {"ok": False, "denied": True, "reason": decision.reason}
                self._record_tool_result(mission_id, tool_call, result)
                self.events.append(
                    f"mission:{mission_id}",
                    "ToolDenied",
                    {"tool": tool_call.name, "reason": decision.reason, "risk": decision.risk},
                    actor="policy",
                )
                continue
            if decision.action == PolicyAction.APPROVAL:
                approval_id = uuid.uuid4().hex
                self.db.create_approval(
                    {
                        "id": approval_id,
                        "mission_id": mission_id,
                        "tool_call_id": tool_call.id,
                        "tool_name": tool_call.name,
                        "args": tool_call.arguments,
                        "reason": decision.reason,
                    }
                )
                self.events.append(
                    f"mission:{mission_id}",
                    "ApprovalRequested",
                    {
                        "approval_id": approval_id,
                        "tool": tool_call.name,
                        "risk": decision.risk,
                        "preview": self._safe_tool_preview(tool_call),
                    },
                    actor="policy",
                )
                waiting = True
                continue
            self._execute_and_record(tool_call, context)

        if waiting:
            self.db.update_mission(mission_id, status="waiting_approval")
            self._checkpoint(mission_id, "waiting_approval")
            return "stop"
        self.db.update_mission(mission_id, status="queued")
        self._checkpoint(mission_id, "execution")
        return "continue"

    def _run_verification(self, mission: dict[str, Any]) -> str:
        mission_id = mission["id"]
        profile = self.settings.get_profile(mission["profile_id"])
        root = Path(mission["project_path"])
        diff = self.tools.execute("git_diff", {}, ToolContext(mission_id, root, profile))
        state = self.db.get_state(mission_id)
        verification_packet = {
            "goal": mission["prompt"],
            "executor_result": mission.get("result") or "",
            "durable_state": state,
            "git_diff": diff,
            "required_format": "First line must be VERDICT: PASS or VERDICT: FAIL, followed by evidence and unresolved risks.",
        }
        messages = [
            {
                "role": "system",
                "content": (
                    CORE_SYSTEM_PROMPT
                    + "\n\nYou are the independent verifier. Do not make edits. Check the goal, evidence, diff, "
                    "acceptance checks, policy compliance and overclaims. The executor cannot approve itself."
                    + f"\n\nRuntime profile:\n{profile.system_prompt}"
                ),
            },
            {"role": "user", "content": json.dumps(verification_packet, ensure_ascii=False)},
        ]
        response = self._call_verifier(mission, profile, messages)
        self.db.add_message(mission_id, "assistant", response.content, name="verifier")
        passed = bool(re.search(r"^\s*VERDICT\s*:\s*PASS\b", response.content, re.IGNORECASE))
        self.events.append(
            f"mission:{mission_id}",
            "VerificationCompleted",
            {"passed": passed, "verifier_model_id": response.model_id},
            actor="verifier",
        )
        if passed:
            combined = (mission.get("result") or "") + "\n\n--- Verification ---\n" + response.content
            self.db.update_mission(mission_id, status="completed", stage="completed", result=combined)
            self.events.append(
                f"mission:{mission_id}",
                "MissionCompleted",
                {"verified": True, "verifier_model_id": response.model_id},
                actor="runtime",
            )
            self._checkpoint(mission_id, "completed")
            return "stop"

        if int(mission["repair_count"]) < 1:
            repair = int(mission["repair_count"]) + 1
            self.db.add_message(
                mission_id,
                "user",
                "Verifier feedback requires one bounded repair pass:\n" + response.content,
                name="verification_feedback",
            )
            self.db.update_mission(
                mission_id, stage="execution", status="queued", repair_count=repair
            )
            self.events.append(
                f"mission:{mission_id}",
                "RepairPassRequested",
                {"repair_count": repair},
                actor="verifier",
            )
            self._checkpoint(mission_id, "execution")
            return "continue"

        self.db.update_mission(
            mission_id,
            status="needs_review",
            error="Independent verifier did not pass the result after the bounded repair",
        )
        self._checkpoint(mission_id, "needs_review")
        return "stop"

    def _call(
        self,
        mission: dict[str, Any],
        profile: RuntimeProfile,
        role: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
    ) -> ModelResponse:
        decision = self.router.decide(
            mission["requested_model_id"],
            profile_id=profile.id,
            role=role,
            prompt=mission["prompt"],
            local_only=bool(mission["local_only"]),
        )
        candidates = [self.settings.get_model(item) for item in decision.candidates]
        self.events.append(
            f"mission:{mission['id']}", "ModelRouted", decision.public_dict(), actor="router"
        )
        try:
            response, failures = self.gateway.complete_with_fallback(
                candidates, messages, tools=tools, role=role
            )
        except GatewayError as exc:
            self._record_failed_call(mission["id"], candidates[0], str(exc))
            raise
        if failures:
            self.events.append(
                f"mission:{mission['id']}",
                "ModelFallbackUsed",
                {"selected_model_id": response.model_id, "failures": failures},
                actor="gateway",
            )
        self._record_successful_call(mission, response)
        if role == "executor":
            self.db.update_mission(mission["id"], actual_model_id=response.model_id)
        return response

    def _call_verifier(
        self,
        mission: dict[str, Any],
        profile: RuntimeProfile,
        messages: list[dict[str, Any]],
    ) -> ModelResponse:
        executor_model = mission.get("actual_model_id") or ""
        candidates = self.router.verifier_candidates(executor_model, bool(mission["local_only"]))
        if not candidates:
            raise GatewayError("No configured verifier model satisfies the current privacy policy")
        self.events.append(
            f"mission:{mission['id']}",
            "VerifierRouted",
            {
                "executor_model_id": executor_model,
                "candidates": [item.id for item in candidates],
                "structurally_independent": candidates[0].id != executor_model,
            },
            actor="router",
        )
        response, failures = self.gateway.complete_with_fallback(candidates, messages, role="verifier")
        if failures:
            self.events.append(
                f"mission:{mission['id']}",
                "ModelFallbackUsed",
                {"selected_model_id": response.model_id, "failures": failures},
                actor="gateway",
            )
        self._record_successful_call(mission, response)
        self.db.update_mission(mission["id"], verifier_model_id=response.model_id)
        return response

    def _record_successful_call(self, mission: dict[str, Any], response: ModelResponse) -> None:
        model = self.settings.get_model(response.model_id)
        cost = self.gateway.estimate_cost(model, response.input_tokens, response.output_tokens)
        price_known = model.locality == "local" or model.provider == "mock" or (model.input_per_million > 0 and model.output_per_million > 0)
        cost_known = response.usage_reported and price_known
        self.db.add_usage(
            {
                "mission_id": mission["id"],
                "model_id": response.model_id,
                "provider": response.provider,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "cost_usd": cost,
                "usage_reported": response.usage_reported,
                "cost_known": cost_known,
                "latency_ms": response.latency_ms,
                "success": True,
            }
        )
        refreshed = self._require_mission(mission["id"])
        self.db.update_mission(mission["id"], spent_usd=float(refreshed["spent_usd"]) + cost)
        self.events.append(
            f"mission:{mission['id']}",
            "ModelCallCompleted",
            {
                "model_id": response.model_id,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "cost_usd": cost,
                "usage_reported": response.usage_reported,
                "cost_known": cost_known,
                "latency_ms": response.latency_ms,
            },
            actor="gateway",
        )

    def _record_failed_call(self, mission_id: str, model: ModelProfile, error: str) -> None:
        self.db.add_usage(
            {
                "mission_id": mission_id,
                "model_id": model.id,
                "provider": model.provider,
                "success": False,
                "error": error,
            }
        )

    def _compile_messages(
        self, mission: dict[str, Any], profile: RuntimeProfile, *, role: str
    ) -> list[dict[str, Any]]:
        state = self.db.get_state(mission["id"])
        system = (
            CORE_SYSTEM_PROMPT
            + f"\n\nRole: {role}\nProject root: {mission['project_path']}\n"
            + f"Runtime profile: {profile.label}\n{profile.system_prompt}\n"
            + "\nDurable mission state (update it when material facts change):\n"
            + json.dumps(state, ensure_ascii=False, indent=2)
        )
        rows = self.db.get_messages(mission["id"])
        converted: list[dict[str, Any]] = []
        for row in rows:
            if row["role"] == "tool":
                converted.append(
                    {
                        "role": "tool",
                        "tool_call_id": row["meta"].get("tool_call_id", "unknown"),
                        "content": row["content"] or "",
                    }
                )
                continue
            item: dict[str, Any] = {"role": row["role"], "content": row["content"] or ""}
            calls = row["meta"].get("tool_calls")
            if row["role"] == "assistant" and calls:
                item["tool_calls"] = calls
            if row.get("name") and row["role"] == "assistant" and not calls:
                item["content"] = f"[{row['name']}]\n{item['content']}"
            converted.append(item)

        budget = max(20_000, profile.max_context_chars - len(system))
        chosen: list[dict[str, Any]] = []
        used = 0
        for item in reversed(converted):
            size = len(item.get("content", "")) + len(json.dumps(item.get("tool_calls", [])))
            if chosen and used + size > budget:
                break
            chosen.append(item)
            used += size
        chosen.reverse()
        if len(chosen) < len(converted):
            self.events.append(
                f"mission:{mission['id']}",
                "ContextCompiled",
                {"messages_included": len(chosen), "messages_omitted": len(converted) - len(chosen)},
                actor="context_compiler",
            )
            chosen.insert(
                0,
                {
                    "role": "system",
                    "content": "Earlier chat turns were omitted. The durable mission state above is authoritative.",
                },
            )
        return [{"role": "system", "content": system}, *chosen]

    def _execute_and_record(self, tool_call: ToolCall, context: ToolContext) -> None:
        try:
            result = self.tools.execute(tool_call.name, tool_call.arguments, context)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
        self._record_tool_result(context.mission_id, tool_call, result)
        self.events.append(
            f"mission:{context.mission_id}",
            "ToolCompleted" if result.get("ok") else "ToolFailed",
            {"tool": tool_call.name, "result": self._bounded_event_result(result)},
            actor="tool",
        )

    def _record_tool_result(
        self, mission_id: str, tool_call: ToolCall, result: dict[str, Any]
    ) -> None:
        self.db.add_message(
            mission_id,
            "tool",
            json.dumps(result, ensure_ascii=False),
            name=tool_call.name,
            meta={"tool_call_id": tool_call.id},
        )

    def resolve_approval(self, approval_id: str, approve: bool) -> dict[str, Any]:
        approval = self.db.get_approval(approval_id)
        if not approval:
            raise KeyError("Approval not found")
        if approval["status"] != "pending":
            raise ValueError("Approval is already resolved")
        mission = self._require_mission(approval["mission_id"])
        profile = self.settings.get_profile(mission["profile_id"])
        root = Path(mission["project_path"])
        tool_call = ToolCall(approval["tool_call_id"], approval["tool_name"], approval["args"])

        if approve:
            decision = self.policy.decide(
                tool_call.name, tool_call.arguments, profile=profile, root=root
            )
            if decision.action == PolicyAction.DENY:
                result = {"ok": False, "denied": True, "reason": decision.reason}
                status = "denied"
            else:
                try:
                    result = self.tools.execute(
                        tool_call.name, tool_call.arguments, ToolContext(mission["id"], root, profile)
                    )
                    status = "approved"
                except Exception as exc:
                    result = {"ok": False, "error": str(exc)}
                    status = "approved_failed"
        else:
            result = {"ok": False, "denied": True, "reason": "Human denied the proposed action"}
            status = "denied"

        self.db.resolve_approval(approval_id, status, result)
        self._record_tool_result(mission["id"], tool_call, result)
        self.events.append(
            f"mission:{mission['id']}",
            "ApprovalResolved",
            {"approval_id": approval_id, "status": status, "tool": tool_call.name},
            actor="user",
        )
        pending = self.db.fetch_one(
            "SELECT COUNT(*) count FROM approvals WHERE mission_id = ? AND status = 'pending'",
            (mission["id"],),
        )
        if not pending or int(pending["count"]) == 0:
            self.db.update_mission(mission["id"], status="queued")
            self.submit(mission["id"])
        return self.get_detail(mission["id"])

    def cancel(self, mission_id: str) -> dict[str, Any]:
        mission = self._require_mission(mission_id)
        if mission["status"] in {"completed", "failed", "cancelled"}:
            return self.get_detail(mission_id)
        self.db.update_mission(mission_id, status="cancelled")
        self.events.append(f"mission:{mission_id}", "MissionCancelled", {}, actor="user")
        return self.get_detail(mission_id)

    def add_message(self, mission_id: str, content: str) -> dict[str, Any]:
        mission = self._require_mission(mission_id)
        if not content.strip():
            raise ValueError("message is empty")
        if mission["status"] in {"running", "waiting_approval"}:
            raise ValueError("Wait for the active step or resolve approvals first")
        self.db.add_message(mission_id, "user", content.strip())
        self.db.update_mission(mission_id, status="queued", stage="execution", error=None)
        self.events.append(
            f"mission:{mission_id}", "UserMessageAdded", {"chars": len(content)}, actor="user"
        )
        self.submit(mission_id)
        return self.get_detail(mission_id)

    def get_detail(self, mission_id: str) -> dict[str, Any]:
        mission = self._require_mission(mission_id)
        mission["local_only"] = bool(mission["local_only"])
        integrity, integrity_detail = self.events.verify(f"mission:{mission_id}")
        return {
            "mission": mission,
            "state": self.db.get_state(mission_id),
            "messages": self.db.get_messages(mission_id),
            "events": self.events.list(f"mission:{mission_id}"),
            "approvals": [
                item for item in self.db.list_approvals() if item["mission_id"] == mission_id
            ],
            "integrity": {"ok": integrity, "detail": integrity_detail},
        }

    def list_missions(self) -> list[dict[str, Any]]:
        missions = self.db.list_missions()
        for mission in missions:
            mission["local_only"] = bool(mission["local_only"])
        return missions

    def _checkpoint(self, mission_id: str, stage: str) -> None:
        state = self.db.get_state(mission_id)
        event = self.events.append(
            f"mission:{mission_id}",
            "CheckpointCreated",
            {"stage": stage, "state_keys": sorted(state)},
            actor="runtime",
        )
        self.db.add_checkpoint(mission_id, stage, state, event["event_hash"])

    def _budget_allows(self, mission: dict[str, Any]) -> bool:
        mission_budget = float(mission["budget_usd"])
        if mission_budget > 0 and float(mission["spent_usd"]) >= mission_budget:
            return False
        daily = self.db.usage_summary().get("total", {}).get("cost_usd", 0) or 0
        return self.settings.daily_budget_usd <= 0 or float(daily) < self.settings.daily_budget_usd

    def _require_mission(self, mission_id: str) -> dict[str, Any]:
        mission = self.db.get_mission(mission_id)
        if not mission:
            raise KeyError("Mission not found")
        return mission

    @staticmethod
    def _tool_call_payload(item: ToolCall) -> dict[str, Any]:
        return {
            "id": item.id,
            "type": "function",
            "function": {"name": item.name, "arguments": json.dumps(item.arguments, ensure_ascii=False)},
        }

    @staticmethod
    def _safe_tool_preview(item: ToolCall) -> dict[str, Any]:
        arguments = dict(item.arguments)
        if "content" in arguments:
            content = str(arguments["content"])
            arguments["content"] = content[:1200] + ("…" if len(content) > 1200 else "")
        if "patch" in arguments:
            patch = str(arguments["patch"])
            arguments["patch"] = patch[:2400] + ("…" if len(patch) > 2400 else "")
        return {"name": item.name, "arguments": arguments}

    @staticmethod
    def _bounded_event_result(result: dict[str, Any]) -> dict[str, Any]:
        text = json.dumps(result, ensure_ascii=False)
        if len(text) <= 3000:
            return result
        return {"ok": result.get("ok", False), "summary": text[:3000] + "…"}
