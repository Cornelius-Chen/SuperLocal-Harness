from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

from .config import ModelProfile, Settings


class GatewayError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelResponse:
    model_id: str
    provider: str
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    raw_finish_reason: str | None = None


class ModelGateway:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def estimate_cost(model: ModelProfile, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * model.input_per_million / 1_000_000
            + output_tokens * model.output_per_million / 1_000_000
        )

    def complete(
        self,
        model: ModelProfile,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        role: str = "executor",
    ) -> ModelResponse:
        if model.provider == "mock":
            return self._mock(model, messages, role)
        if not model.configured:
            raise GatewayError(f"Model {model.id} is not configured")

        payload: dict[str, Any] = {
            "model": model.api_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools and model.supports_tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if model.api_key:
            headers["Authorization"] = f"Bearer {model.api_key}"
        headers.update(model.extra_headers)

        url = f"{model.base_url.rstrip('/')}/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.settings.request_timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:1200]
            raise GatewayError(f"{model.id} HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GatewayError(f"{model.id} endpoint unavailable: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        try:
            data = json.loads(raw)
            choice = data["choices"][0]
            message = choice.get("message", {})
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise GatewayError(f"Malformed response from {model.id}: {raw[:800]}") from exc

        tool_calls: list[ToolCall] = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            arguments = function.get("arguments") or "{}"
            try:
                parsed_args = json.loads(arguments) if isinstance(arguments, str) else arguments
            except json.JSONDecodeError:
                parsed_args = {"_malformed_arguments": arguments}
            tool_calls.append(
                ToolCall(
                    id=item.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                    name=function.get("name", ""),
                    arguments=parsed_args,
                )
            )
        usage = data.get("usage") or {}
        return ModelResponse(
            model_id=model.id,
            provider=model.provider,
            content=message.get("content") or "",
            tool_calls=tool_calls,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            latency_ms=latency_ms,
            raw_finish_reason=choice.get("finish_reason"),
        )

    def complete_with_fallback(
        self,
        candidates: list[ModelProfile],
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        role: str = "executor",
    ) -> tuple[ModelResponse, list[str]]:
        failures: list[str] = []
        for model in candidates:
            try:
                return self.complete(model, messages, tools=tools, max_tokens=max_tokens, role=role), failures
            except GatewayError as exc:
                failures.append(str(exc))
        raise GatewayError("All candidate models failed: " + " | ".join(failures))

    def health(self, model: ModelProfile) -> dict[str, Any]:
        if model.provider == "mock":
            return {"ok": True, "latency_ms": 0, "detail": "offline mock ready"}
        headers = {"Accept": "application/json"}
        if model.api_key:
            headers["Authorization"] = f"Bearer {model.api_key}"
        request = urllib.request.Request(
            f"{model.base_url.rstrip('/')}/models", headers=headers, method="GET"
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read(512)
            return {"ok": True, "latency_ms": int((time.perf_counter() - started) * 1000)}
        except Exception as exc:  # Health response should be diagnostic, not fatal.
            return {"ok": False, "latency_ms": int((time.perf_counter() - started) * 1000), "detail": str(exc)}

    def _mock(self, model: ModelProfile, messages: list[dict[str, Any]], role: str) -> ModelResponse:
        started = time.perf_counter()
        last = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        if role == "planner":
            content = (
                "PLAN\n1. Inspect the scoped project and constraints.\n"
                "2. Make the smallest reversible change or analysis.\n"
                "3. Run deterministic checks.\n4. Hand evidence to an independent verifier."
            )
        elif role == "verifier":
            content = "VERDICT: PASS\nThe offline demo confirms the verification stage and persistence path are working."
        else:
            content = (
                "Offline demo completed. No external model was called and no files were changed. "
                f"Mission input was: {last[:240]}"
            )
        estimated_in = max(1, sum(len(str(m.get("content", ""))) for m in messages) // 4)
        return ModelResponse(
            model_id=model.id,
            provider=model.provider,
            content=content,
            input_tokens=estimated_in,
            output_tokens=max(1, len(content) // 4),
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw_finish_reason="stop",
        )

