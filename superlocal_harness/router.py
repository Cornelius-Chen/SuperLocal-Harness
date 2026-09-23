from __future__ import annotations

from dataclasses import dataclass

from .config import ModelProfile, Settings


@dataclass(frozen=True)
class RouteDecision:
    requested_model_id: str
    primary_model_id: str
    candidates: tuple[str, ...]
    reason: str
    local_only: bool

    def public_dict(self) -> dict[str, object]:
        return {
            "requested_model_id": self.requested_model_id,
            "primary_model_id": self.primary_model_id,
            "candidates": list(self.candidates),
            "reason": self.reason,
            "local_only": self.local_only,
        }


class StaticRouter:
    """Visible, deterministic M0/M2 router. Learned routing can replace it only after evals."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def _configured(self, ids: list[str], local_only: bool) -> list[str]:
        result = []
        for model_id in ids:
            model = self.settings.models.get(model_id)
            if not model or model.provider == "router" or not model.configured:
                continue
            if local_only and model.locality not in {"local"}:
                continue
            if model_id not in result:
                result.append(model_id)
        return result

    def decide(
        self,
        requested_model_id: str,
        *,
        profile_id: str,
        role: str,
        prompt: str,
        local_only: bool = False,
    ) -> RouteDecision:
        if requested_model_id != "auto":
            model = self.settings.get_model(requested_model_id)
            if not model.configured:
                raise ValueError(
                    f"Model '{requested_model_id}' is not configured. "
                    f"Set {model.api_key_env or 'its local endpoint'} first."
                )
            if local_only and model.locality != "local":
                raise ValueError(f"Model '{requested_model_id}' violates local-only mode")
            return RouteDecision(
                requested_model_id=requested_model_id,
                primary_model_id=requested_model_id,
                candidates=(requested_model_id,),
                reason="Explicit user selection; no automatic provider switch.",
                local_only=local_only,
            )

        lowered = prompt.lower()
        hard_markers = (
            "architecture", "causal", "proof", "diagnose", "hard", "复杂", "推理",
            "架构", "因果", "盲测", "验证", "反证", "review",
        )
        if role in {"verifier", "planner"} or profile_id == "guanlan_blind" or any(
            marker in lowered for marker in hard_markers
        ):
            order = ["deepseek-reasoner", "qwen-coder-local", "deepseek-flash", "qwen-fast-local", "mock-local"]
            reason = "Hard reasoning/blind-research/verification route: strong model first, then bounded fallbacks."
        elif profile_id == "coding" or role == "executor":
            order = ["qwen-coder-local", "deepseek-flash", "deepseek-reasoner", "qwen-fast-local", "mock-local"]
            reason = "Coding route: local coder first for privacy and cost; cloud escalation remains visible."
        else:
            order = ["qwen-fast-local", "deepseek-flash", "qwen-coder-local", "deepseek-reasoner", "mock-local"]
            reason = "Routine route: smallest local worker first, then economical cloud, then escalation."

        candidates = self._configured(order, local_only)
        if not candidates:
            raise ValueError("No configured model satisfies this routing/privacy policy")
        return RouteDecision("auto", candidates[0], tuple(candidates), reason, local_only)

    def verifier_candidates(self, mission_model_id: str, local_only: bool) -> list[ModelProfile]:
        order = ["deepseek-reasoner", "qwen-coder-local", "deepseek-flash", "qwen-fast-local", "mock-local"]
        ids = self._configured(order, local_only)
        different = [item for item in ids if item != mission_model_id]
        # Prefer a different model, but retain the executor model as the final fallback.
        # Logical role separation still works when only one endpoint is available, while
        # the audit stream makes the reduced independence explicit.
        chosen = different + ([mission_model_id] if mission_model_id in ids else [])
        return [self.settings.get_model(item) for item in chosen]
