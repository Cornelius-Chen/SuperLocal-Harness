from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def load_dotenv(path: Path) -> None:
    """Load a minimal .env file without overriding the process environment."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            return os.environ.get(name, default if default is not None else "")

        return ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    return value


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ModelProfile:
    id: str
    label: str
    provider: str
    api_model: str
    base_url: str
    api_key_env: str | None = None
    locality: str = "cloud"
    tier: str = "economical"
    supports_tools: bool = True
    context_window: int = 65536
    input_per_million: float = 0.0
    output_per_million: float = 0.0
    roles: tuple[str, ...] = ()
    description: str = ""
    enabled: bool = True
    extra_headers: dict[str, str] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        if not self.enabled:
            return False
        if self.provider == "mock":
            return True
        if self.locality == "local":
            return True
        if not self.api_key_env:
            return True
        return bool(os.environ.get(self.api_key_env))

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "api_model": self.api_model,
            "locality": self.locality,
            "tier": self.tier,
            "supports_tools": self.supports_tools,
            "context_window": self.context_window,
            "roles": list(self.roles),
            "description": self.description,
            "configured": self.configured,
            "priced": self.input_per_million > 0 or self.output_per_million > 0,
        }


@dataclass(frozen=True)
class RuntimeProfile:
    id: str
    label: str
    description: str
    default_workflow: str = "solo"
    allowed_tools: tuple[str, ...] = ()
    denied_path_parts: tuple[str, ...] = ()
    mutating_tools: bool = True
    max_steps: int = 12
    max_context_chars: int = 120_000
    system_prompt: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "default_workflow": self.default_workflow,
            "mutating_tools": self.mutating_tools,
            "max_steps": self.max_steps,
        }


class Settings:
    def __init__(self, root: Path | None = None):
        self.root = (root or Path(__file__).resolve().parent.parent).resolve()
        load_dotenv(self.root / ".env")

        self.bind = os.environ.get("HARNESS_BIND", "127.0.0.1")
        self.port = int(os.environ.get("HARNESS_PORT", "8765"))
        self.access_token = os.environ.get("HARNESS_ACCESS_TOKEN", "")
        self.data_dir = Path(os.environ.get("HARNESS_DATA_DIR", str(self.root / "data"))).expanduser().resolve()
        self.db_path = self.data_dir / "harness.db"
        self.max_workers = max(1, min(int(os.environ.get("HARNESS_MAX_WORKERS", "2")), 4))
        self.daily_budget_usd = float(os.environ.get("HARNESS_DAILY_BUDGET_USD", "5"))
        self.default_mission_budget_usd = float(os.environ.get("HARNESS_MISSION_BUDGET_USD", "1"))
        self.request_timeout_seconds = int(os.environ.get("HARNESS_REQUEST_TIMEOUT_SECONDS", "180"))

        roots_raw = os.environ.get("HARNESS_PROJECT_ROOTS", "")
        if roots_raw:
            root_values = [part.strip() for part in roots_raw.split(";") if part.strip()]
        else:
            root_values = [str(self.root.parent)]
        self.project_roots = tuple(Path(part).expanduser().resolve() for part in root_values)

        if self.bind not in {"127.0.0.1", "localhost", "::1"} and not self.access_token:
            raise ValueError(
                "HARNESS_ACCESS_TOKEN is required when HARNESS_BIND is not loopback. "
                "This prevents accidental LAN/Tailscale exposure without authentication."
            )

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.models = self._load_models()
        self.profiles = self._load_profiles()

    def _load_json(self, path: Path) -> Any:
        return expand_env(json.loads(path.read_text(encoding="utf-8")))

    def _load_models(self) -> dict[str, ModelProfile]:
        raw = self._load_json(self.root / "config" / "models.json")
        result: dict[str, ModelProfile] = {}
        for item in raw["models"]:
            pricing = item.get("pricing", {})
            model = ModelProfile(
                id=item["id"],
                label=item["label"],
                provider=item.get("provider", "openai_compatible"),
                api_model=item["api_model"],
                base_url=item.get("base_url", ""),
                api_key_env=item.get("api_key_env") or None,
                locality=item.get("locality", "cloud"),
                tier=item.get("tier", "economical"),
                supports_tools=_as_bool(item.get("supports_tools"), True),
                context_window=int(item.get("context_window", 65536)),
                input_per_million=float(pricing.get("input_per_million", 0) or 0),
                output_per_million=float(pricing.get("output_per_million", 0) or 0),
                roles=tuple(item.get("roles", [])),
                description=item.get("description", ""),
                enabled=_as_bool(item.get("enabled"), True),
                extra_headers=item.get("extra_headers", {}),
            )
            if model.id in result:
                raise ValueError(f"Duplicate model id: {model.id}")
            result[model.id] = model
        return result

    def _load_profiles(self) -> dict[str, RuntimeProfile]:
        raw = self._load_json(self.root / "config" / "profiles.json")
        result: dict[str, RuntimeProfile] = {}
        for item in raw["profiles"]:
            profile = RuntimeProfile(
                id=item["id"],
                label=item["label"],
                description=item.get("description", ""),
                default_workflow=item.get("default_workflow", "solo"),
                allowed_tools=tuple(item.get("allowed_tools", [])),
                denied_path_parts=tuple(part.lower() for part in item.get("denied_path_parts", [])),
                mutating_tools=_as_bool(item.get("mutating_tools"), True),
                max_steps=int(item.get("max_steps", 12)),
                max_context_chars=int(item.get("max_context_chars", 120_000)),
                system_prompt=item.get("system_prompt", ""),
            )
            result[profile.id] = profile
        return result

    def get_model(self, model_id: str) -> ModelProfile:
        try:
            return self.models[model_id]
        except KeyError as exc:
            raise KeyError(f"Unknown model: {model_id}") from exc

    def get_profile(self, profile_id: str) -> RuntimeProfile:
        try:
            return self.profiles[profile_id]
        except KeyError as exc:
            raise KeyError(f"Unknown runtime profile: {profile_id}") from exc

    def validate_project_path(self, value: str) -> Path:
        path = Path(value).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise ValueError(f"Project directory does not exist: {path}")
        if not any(path == root or root in path.parents for root in self.project_roots):
            roots = ", ".join(str(item) for item in self.project_roots)
            raise ValueError(f"Project must be under an allowed root: {roots}")
        return path

