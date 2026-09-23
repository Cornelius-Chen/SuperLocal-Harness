"""Run one complete mission with the offline model and temporary local state."""

import os
import tempfile
from pathlib import Path

from superlocal_harness.__main__ import build_runtime
from superlocal_harness.config import Settings


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        os.environ["HARNESS_BIND"] = "127.0.0.1"
        os.environ["HARNESS_PROJECT_ROOTS"] = temp_dir
        os.environ["HARNESS_DATA_DIR"] = str(Path(temp_dir) / "data")
        os.environ["HARNESS_ENABLE_MOCK"] = "true"

        settings = Settings()
        db, _, missions = build_runtime(settings)
        created = missions.create_mission(
            {
                "project_path": temp_dir,
                "prompt": "Inspect this temporary project without changing files.",
                "model_id": "mock-local",
                "profile_id": "read_only",
                "workflow": "plan_execute_verify",
                "local_only": True,
                "budget_usd": 0,
            }
        )
        mission_id = created["mission"]["id"]
        missions.pool.shutdown(wait=True)
        detail = missions.get_detail(mission_id)
        roles = [message["name"] for message in detail["messages"] if message["name"]]

        print(f"Mission: {detail['mission']['status']} (offline demo model)")
        print(f"Roles: {' -> '.join(roles)}")
        print(f"Approval requests: {len(detail['approvals'])}")
        print(f"Event integrity: {'ok' if detail['integrity']['ok'] else 'failed'}")
        print(f"Stored events: {len(detail['events'])}")

        if detail["mission"]["status"] != "completed" or not detail["integrity"]["ok"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
