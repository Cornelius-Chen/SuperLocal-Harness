from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .config import RuntimeProfile


class PolicyAction(str, Enum):
    ALLOW = "allow"
    APPROVAL = "approval"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyDecision:
    action: PolicyAction
    reason: str
    risk: str


READ_ONLY_TOOLS = {
    "list_files", "read_file", "search_text", "git_status", "git_diff", "update_state"
}
MUTATING_TOOLS = {"write_file", "apply_patch", "shell"}

DANGEROUS_COMMAND_PATTERNS = [
    r"\brm\s+-[^\n]*r[^\n]*f\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[^\n]*f",
    r"\bformat(?:\.com)?\b",
    r"\bdiskpart\b",
    r"\bshutdown\b",
    r"\brestart-computer\b",
    r"\bremove-item\b[^\n]*(?:-recurse|-force)",
    r"\brmdir\b[^\n]*(?:/s|/q)",
    r"\bdel\b[^\n]*/s",
    r"\breg\s+delete\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
]

TRADING_SIDE_EFFECT_PATTERN = re.compile(
    r"\b(place[_ -]?order|submit[_ -]?order|buy[_ -]?order|sell[_ -]?order|"
    r"ibkr|twsapi|webull|alpaca|binance|broker[_ -]?order)\b",
    re.IGNORECASE,
)


class PolicyEngine:
    def validate_path(self, root: Path, raw_path: str, profile: RuntimeProfile) -> Path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            raise PermissionError(f"Path escapes project root: {raw_path}")
        relative = resolved.relative_to(root).as_posix().lower()
        for denied in profile.denied_path_parts:
            token = denied.replace("\\", "/").strip("/").lower()
            if token and (token in relative or token in [part.lower() for part in resolved.parts]):
                raise PermissionError(f"Path is sealed by profile '{profile.id}': {raw_path}")
        return resolved

    def decide(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        profile: RuntimeProfile,
        root: Path,
    ) -> PolicyDecision:
        if tool_name not in profile.allowed_tools:
            return PolicyDecision(PolicyAction.DENY, f"Tool is not enabled in profile '{profile.id}'", "blocked")

        for key in ("path", "file", "directory"):
            if key in arguments and isinstance(arguments[key], str):
                try:
                    self.validate_path(root, arguments[key], profile)
                except PermissionError as exc:
                    return PolicyDecision(PolicyAction.DENY, str(exc), "blocked")

        if tool_name in READ_ONLY_TOOLS:
            return PolicyDecision(PolicyAction.ALLOW, "Read/state-only action inside the scoped project", "low")

        if tool_name in MUTATING_TOOLS and not profile.mutating_tools:
            return PolicyDecision(PolicyAction.DENY, f"Profile '{profile.id}' is read-only", "blocked")

        if tool_name == "shell":
            command = str(arguments.get("command", ""))
            normalized_command = command.replace("\\", "/").lower()
            for denied in profile.denied_path_parts:
                token = denied.replace("\\", "/").strip("/").lower()
                if token and token in normalized_command:
                    return PolicyDecision(
                        PolicyAction.DENY,
                        f"Shell command references a path/token sealed by profile '{profile.id}'",
                        "critical",
                    )
            for pattern in DANGEROUS_COMMAND_PATTERNS:
                if re.search(pattern, command, flags=re.IGNORECASE):
                    return PolicyDecision(
                        PolicyAction.DENY,
                        "Destructive shell pattern is blocked; use a narrower recoverable operation",
                        "critical",
                    )
            if TRADING_SIDE_EFFECT_PATTERN.search(command):
                return PolicyDecision(
                    PolicyAction.DENY,
                    "Broker/trading side effects are outside this harness authority",
                    "critical",
                )
            return PolicyDecision(
                PolicyAction.APPROVAL,
                "Shell execution can change the machine or contact external services",
                "medium",
            )

        if tool_name == "apply_patch":
            patch = str(arguments.get("patch", ""))
            for match in re.finditer(r"^(?:---|\+\+\+)\s+(?:[ab]/)?([^\t\r\n ]+)", patch, re.MULTILINE):
                patch_path = match.group(1)
                if patch_path == "/dev/null":
                    continue
                try:
                    self.validate_path(root, patch_path, profile)
                except PermissionError as exc:
                    return PolicyDecision(PolicyAction.DENY, str(exc), "critical")
            return PolicyDecision(
                PolicyAction.APPROVAL,
                "Workspace mutation requires human approval and will be recorded in the audit stream",
                "medium",
            )

        if tool_name == "write_file":
            return PolicyDecision(
                PolicyAction.APPROVAL,
                "Workspace mutation requires human approval and will be recorded in the audit stream",
                "medium",
            )

        return PolicyDecision(PolicyAction.DENY, "Unknown action", "blocked")
