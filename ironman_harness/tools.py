from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import RuntimeProfile
from .db import Database
from .policy import PolicyEngine


MAX_TOOL_OUTPUT = 40_000
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache"}


def _trim(value: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n… [truncated {len(value) - limit} characters]"


def _sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class ToolContext:
    mission_id: str
    project_root: Path
    profile: RuntimeProfile


class ToolExecutor:
    def __init__(self, db: Database, policy: PolicyEngine):
        self.db = db
        self.policy = policy

    def schemas(self, profile: RuntimeProfile, *, read_only: bool = False) -> list[dict[str, Any]]:
        definitions = {
            "list_files": {
                "description": "List files within the scoped project. Results are bounded and never leave the project root.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative directory, default '.'"},
                        "glob": {"type": "string", "description": "Optional filename glob, e.g. '*.py'"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 1000}
                    }
                },
            },
            "read_file": {
                "description": "Read a UTF-8 text file inside the project with optional line window.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer", "minimum": 1},
                        "max_lines": {"type": "integer", "minimum": 1, "maximum": 2000}
                    },
                    "required": ["path"]
                },
            },
            "search_text": {
                "description": "Search project text files using a literal or regular expression.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string"},
                        "glob": {"type": "string"},
                        "regex": {"type": "boolean"},
                        "max_matches": {"type": "integer", "minimum": 1, "maximum": 200}
                    },
                    "required": ["query"]
                },
            },
            "git_status": {
                "description": "Show the current git status without modifying the repository.",
                "parameters": {"type": "object", "properties": {}},
            },
            "git_diff": {
                "description": "Show the current git diff without modifying the repository.",
                "parameters": {
                    "type": "object",
                    "properties": {"staged": {"type": "boolean"}}
                },
            },
            "update_state": {
                "description": "Persist durable mission state outside the chat context. Supply any of plan, facts, decisions, open_questions, next_actions, artifacts, hypotheses or acceptance_checks.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "plan": {"type": "array", "items": {"type": "string"}},
                        "facts": {"type": "array", "items": {"type": "string"}},
                        "decisions": {"type": "array", "items": {"type": "string"}},
                        "open_questions": {"type": "array", "items": {"type": "string"}},
                        "next_actions": {"type": "array", "items": {"type": "string"}},
                        "artifacts": {"type": "array", "items": {"type": "string"}},
                        "hypotheses": {"type": "array", "items": {"type": "string"}},
                        "acceptance_checks": {"type": "array", "items": {"type": "string"}}
                    }
                },
            },
            "write_file": {
                "description": "Create or replace one UTF-8 file. This pauses for human approval before execution.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["path", "content"]
                },
            },
            "apply_patch": {
                "description": "Apply a unified git diff after validation and human approval.",
                "parameters": {
                    "type": "object",
                    "properties": {"patch": {"type": "string"}},
                    "required": ["patch"]
                },
            },
            "shell": {
                "description": "Run a bounded shell/PowerShell command in the project. Always pauses for approval; destructive and broker/trading commands are blocked.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600}
                    },
                    "required": ["command"]
                },
            },
        }
        allowed = set(profile.allowed_tools)
        if read_only:
            allowed -= {"write_file", "apply_patch", "shell"}
        return [
            {"type": "function", "function": {"name": name, **definitions[name]}}
            for name in definitions
            if name in allowed
        ]

    def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        handlers: dict[str, Callable[[dict[str, Any], ToolContext], dict[str, Any]]] = {
            "list_files": self._list_files,
            "read_file": self._read_file,
            "search_text": self._search_text,
            "git_status": self._git_status,
            "git_diff": self._git_diff,
            "update_state": self._update_state,
            "write_file": self._write_file,
            "apply_patch": self._apply_patch,
            "shell": self._shell,
        }
        if name not in handlers:
            raise ValueError(f"Unknown tool: {name}")
        return handlers[name](arguments, context)

    def _path(self, arguments: dict[str, Any], context: ToolContext, default: str = ".") -> Path:
        return self.policy.validate_path(
            context.project_root, str(arguments.get("path", default)), context.profile
        )

    def _iter_files(self, base: Path, context: ToolContext):
        for current, dirnames, filenames in os.walk(base):
            dirnames[:] = [item for item in dirnames if item not in SKIP_DIRS]
            for filename in filenames:
                path = Path(current) / filename
                try:
                    self.policy.validate_path(context.project_root, str(path), context.profile)
                except PermissionError:
                    continue
                yield path

    def _list_files(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        base = self._path(args, context)
        pattern = str(args.get("glob", "*") or "*")
        limit = min(max(int(args.get("limit", 400)), 1), 1000)
        items: list[str] = []
        if base.is_file():
            items = [base.relative_to(context.project_root).as_posix()]
        else:
            for path in self._iter_files(base, context):
                relative = path.relative_to(context.project_root).as_posix()
                if fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(relative, pattern):
                    items.append(relative)
                    if len(items) >= limit:
                        break
        return {"ok": True, "files": sorted(items), "count": len(items), "truncated": len(items) >= limit}

    def _read_file(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = self._path(args, context)
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.stat().st_size > 2_000_000:
            raise ValueError("File exceeds the 2 MB direct-read limit; narrow the source first")
        data = path.read_bytes()
        if b"\x00" in data[:4096]:
            raise ValueError("Binary files are not readable through read_file")
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = max(int(args.get("start_line", 1)), 1)
        max_lines = min(max(int(args.get("max_lines", 500)), 1), 2000)
        selected = lines[start - 1:start - 1 + max_lines]
        rendered = "\n".join(f"{index}: {line}" for index, line in enumerate(selected, start=start))
        return {
            "ok": True,
            "path": path.relative_to(context.project_root).as_posix(),
            "content": _trim(rendered),
            "start_line": start,
            "end_line": start + len(selected) - 1,
            "total_lines": len(lines),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def _search_text(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        query = str(args.get("query", ""))
        if not query:
            raise ValueError("query is required")
        base = self._path(args, context)
        pattern = str(args.get("glob", "*") or "*")
        limit = min(max(int(args.get("max_matches", 80)), 1), 200)
        use_regex = bool(args.get("regex", False))
        matcher = re.compile(query, re.IGNORECASE) if use_regex else None
        matches: list[dict[str, Any]] = []
        paths = [base] if base.is_file() else self._iter_files(base, context)
        for path in paths:
            relative = path.relative_to(context.project_root).as_posix()
            if not (fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(relative, pattern)):
                continue
            try:
                if path.stat().st_size > 2_000_000:
                    continue
                raw = path.read_bytes()
                if b"\x00" in raw[:4096]:
                    continue
                lines = raw.decode("utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, 1):
                found = bool(matcher.search(line)) if matcher else query.casefold() in line.casefold()
                if found:
                    matches.append({"path": relative, "line": line_number, "text": line[:500]})
                    if len(matches) >= limit:
                        return {"ok": True, "matches": matches, "truncated": True}
        return {"ok": True, "matches": matches, "truncated": False}

    def _run_git(self, context: ToolContext, args: list[str]) -> dict[str, Any]:
        result = subprocess.run(
            ["git", *args], cwd=context.project_root, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
        )
        return {"ok": result.returncode == 0, "exit_code": result.returncode, "output": _trim(result.stdout)}

    def _git_status(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return self._run_git(context, ["status", "--short", "--branch"])

    def _git_diff(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        command = ["diff", "--no-ext-diff"]
        if args.get("staged"):
            command.append("--cached")
        return self._run_git(context, command)

    def _update_state(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        allowed = {
            "plan", "facts", "decisions", "open_questions", "next_actions",
            "artifacts", "hypotheses", "acceptance_checks",
        }
        state = self.db.get_state(context.mission_id)
        changed: list[str] = []
        for key, value in args.items():
            if key not in allowed:
                continue
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{key} must be a list of strings")
            state[key] = value
            changed.append(key)
        self.db.put_state(context.mission_id, state)
        return {"ok": True, "updated": changed, "state": state}

    def _write_file(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = self._path(args, context)
        content = str(args.get("content", ""))
        before = _sha256(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.harness-tmp")
        temporary.write_text(content, encoding="utf-8", newline="")
        os.replace(temporary, path)
        return {
            "ok": True,
            "path": path.relative_to(context.project_root).as_posix(),
            "before_sha256": before,
            "after_sha256": _sha256(path),
            "bytes": len(content.encode("utf-8")),
        }

    def _apply_patch(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        patch = str(args.get("patch", ""))
        if not patch.strip():
            raise ValueError("patch is required")
        if re.search(r"^(?:---|\+\+\+)\s+(?:[ab]/)?(?:\.\./|/)", patch, re.MULTILINE):
            raise PermissionError("Patch contains an unsafe absolute or parent path")
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".patch", delete=False, dir=context.project_root
        )
        try:
            with handle:
                handle.write(patch)
            check = subprocess.run(
                ["git", "apply", "--check", handle.name], cwd=context.project_root,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
            )
            if check.returncode != 0:
                return {"ok": False, "stage": "check", "output": _trim(check.stdout)}
            applied = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", handle.name], cwd=context.project_root,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
            )
            return {
                "ok": applied.returncode == 0,
                "stage": "apply",
                "exit_code": applied.returncode,
                "output": _trim(applied.stdout),
            }
        finally:
            try:
                os.unlink(handle.name)
            except OSError:
                pass

    def _shell(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        command = str(args.get("command", ""))
        timeout = min(max(int(args.get("timeout_seconds", 120)), 1), 600)
        if os.name == "nt":
            invocation = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            invocation = ["/bin/bash", "-lc", command]
        result = subprocess.run(
            invocation, cwd=context.project_root, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "output": _trim(result.stdout),
        }

