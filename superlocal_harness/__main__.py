from __future__ import annotations

import argparse
import json
import os
import sys

from .api import create_server
from .config import Settings
from .db import Database
from .events import EventStore
from .gateway import ModelGateway
from .policy import PolicyEngine
from .router import StaticRouter
from .runtime import MissionService
from .tools import ToolExecutor


def build_runtime(settings: Settings):
    db = Database(settings.db_path)
    events = EventStore(db)
    policy = PolicyEngine()
    gateway = ModelGateway(settings)
    router = StaticRouter(settings)
    tools = ToolExecutor(db, policy)
    missions = MissionService(settings, db, events, router, gateway, policy, tools)
    return db, gateway, missions


def main() -> int:
    parser = argparse.ArgumentParser(description="SuperLocal Harness local mission control")
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "doctor", "config"])
    parser.add_argument("--bind", help="Override HARNESS_BIND")
    parser.add_argument("--port", type=int, help="Override HARNESS_PORT")
    args = parser.parse_args()

    if args.bind:
        os.environ["HARNESS_BIND"] = args.bind
    if args.port:
        os.environ["HARNESS_PORT"] = str(args.port)

    try:
        settings = Settings()
        db, gateway, missions = build_runtime(settings)
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.command == "config":
        print(
            json.dumps(
                {
                    "bind": settings.bind,
                    "port": settings.port,
                    "db": str(settings.db_path),
                    "project_roots": [str(item) for item in settings.project_roots],
                    "models": [item.public_dict() for item in settings.models.values()],
                    "profiles": [item.public_dict() for item in settings.profiles.values()],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "doctor":
        print("SuperLocal Harness doctor")
        print(f"Database: {settings.db_path} [ok]")
        print("Models:")
        for model in settings.models.values():
            if model.provider == "router":
                continue
            if not model.configured:
                result = {"ok": False, "detail": f"missing {model.api_key_env}"}
            else:
                result = gateway.health(model)
            state = "ok" if result.get("ok") else "not ready"
            print(f"  - {model.label}: {state} ({result.get('detail', str(result.get('latency_ms', 0)) + ' ms')})")
        return 0

    missions.resume_incomplete()
    server = create_server(settings, db, gateway, missions)
    display_host = "127.0.0.1" if settings.bind in {"0.0.0.0", "::"} else settings.bind
    print("SuperLocal Harness v0.1")
    print(f"Open http://{display_host}:{settings.port}")
    print("Press Ctrl+C to stop. Mission state remains in SQLite and resumes on restart.")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping safely…")
    finally:
        server.shutdown()
        server.server_close()
        missions.pool.shutdown(wait=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
