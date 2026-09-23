from __future__ import annotations

import json
import mimetypes
import traceback
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Database
from .gateway import ModelGateway
from .runtime import MissionService


class HarnessServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        settings: Settings,
        db: Database,
        gateway: ModelGateway,
        missions: MissionService,
    ):
        super().__init__(address, handler)
        self.settings = settings
        self.db = db
        self.gateway = gateway
        self.missions = missions


class APIHandler(BaseHTTPRequestHandler):
    server: HarnessServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[http] {self.address_string()} {format % args}")

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.startswith("/api/"):
                if not self._authorized():
                    return
                self._handle_api_get(parsed)
                return
            self._serve_static(parsed.path)
        except Exception as exc:
            self._error(exc)

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urllib.parse.urlparse(self.path)
            if not parsed.path.startswith("/api/"):
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            if not self._authorized():
                return
            payload = self._read_json()
            self._handle_api_post(parsed.path, payload)
        except Exception as exc:
            self._error(exc)

    def _authorized(self) -> bool:
        token = self.server.settings.access_token
        if not token:
            return True
        supplied = self.headers.get("X-Harness-Token", "")
        if supplied == token:
            return True
        self._json(HTTPStatus.UNAUTHORIZED, {"error": "Missing or invalid harness access token"})
        return False

    def _handle_api_get(self, parsed: urllib.parse.ParseResult) -> None:
        path = parsed.path.rstrip("/")
        if path == "/api/health":
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "version": "0.1.0",
                    "bind": self.server.settings.bind,
                    "database": str(self.server.settings.db_path),
                },
            )
            return
        if path == "/api/config":
            self._json(
                HTTPStatus.OK,
                {
                    "models": [item.public_dict() for item in self.server.settings.models.values()],
                    "profiles": [item.public_dict() for item in self.server.settings.profiles.values()],
                    "workflows": [
                        {"id": "solo", "label": "Solo agent"},
                        {"id": "plan_execute_verify", "label": "Plan → execute → verify"},
                    ],
                    "project_roots": [str(item) for item in self.server.settings.project_roots],
                    "daily_budget_usd": self.server.settings.daily_budget_usd,
                    "default_mission_budget_usd": self.server.settings.default_mission_budget_usd,
                    "auth_required": bool(self.server.settings.access_token),
                },
            )
            return
        if path == "/api/missions":
            self._json(HTTPStatus.OK, {"missions": self.server.missions.list_missions()})
            return
        if path.startswith("/api/missions/"):
            mission_id = path.split("/")[3]
            self._json(HTTPStatus.OK, self.server.missions.get_detail(mission_id))
            return
        if path == "/api/approvals":
            query = urllib.parse.parse_qs(parsed.query)
            status = query.get("status", [None])[0]
            self._json(HTTPStatus.OK, {"approvals": self.server.db.list_approvals(status)})
            return
        if path == "/api/usage":
            self._json(HTTPStatus.OK, self.server.db.usage_summary())
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown API route"})

    def _handle_api_post(self, path: str, payload: dict[str, Any]) -> None:
        clean = path.rstrip("/")
        if clean == "/api/missions":
            self._json(HTTPStatus.CREATED, self.server.missions.create_mission(payload))
            return
        if clean.startswith("/api/missions/"):
            parts = clean.split("/")
            if len(parts) == 5 and parts[4] == "cancel":
                self._json(HTTPStatus.OK, self.server.missions.cancel(parts[3]))
                return
            if len(parts) == 5 and parts[4] == "messages":
                self._json(
                    HTTPStatus.OK,
                    self.server.missions.add_message(parts[3], str(payload.get("content", ""))),
                )
                return
        if clean.startswith("/api/approvals/"):
            parts = clean.split("/")
            if len(parts) == 5 and parts[4] in {"approve", "deny"}:
                self._json(
                    HTTPStatus.OK,
                    self.server.missions.resolve_approval(parts[3], parts[4] == "approve"),
                )
                return
        if clean.startswith("/api/models/") and clean.endswith("/health"):
            model_id = clean.split("/")[3]
            model = self.server.settings.get_model(model_id)
            self._json(HTTPStatus.OK, {"model_id": model_id, **self.server.gateway.health(model)})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Unknown API route"})

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 5_000_000:
            raise ValueError("Request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _serve_static(self, request_path: str) -> None:
        static_root = Path(__file__).resolve().parent / "static"
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        target = (static_root / relative).resolve()
        if static_root not in target.parents and target != static_root:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not target.exists() or not target.is_file():
            target = static_root / "index.html"
        content = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, exc: Exception) -> None:
        if isinstance(exc, KeyError):
            status = HTTPStatus.NOT_FOUND
        elif isinstance(exc, (ValueError, PermissionError, json.JSONDecodeError)):
            status = HTTPStatus.BAD_REQUEST
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            traceback.print_exc()
        try:
            self._json(status, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass


def create_server(
    settings: Settings,
    db: Database,
    gateway: ModelGateway,
    missions: MissionService,
) -> HarnessServer:
    return HarnessServer(
        (settings.bind, settings.port),
        APIHandler,
        settings=settings,
        db=db,
        gateway=gateway,
        missions=missions,
    )

