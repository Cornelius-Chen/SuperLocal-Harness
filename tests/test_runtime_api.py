import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from ironman_harness.__main__ import build_runtime
from ironman_harness.api import create_server
from ironman_harness.config import Settings
from ironman_harness.gateway import ModelResponse, ToolCall


ROOT = Path(__file__).resolve().parents[1]


class RuntimeAPITests(unittest.TestCase):
    def _settings(self, tmp: str) -> Settings:
        return Settings(ROOT)

    def test_offline_supervised_mission_completes_and_verifies(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "HARNESS_BIND": "127.0.0.1",
                "HARNESS_PORT": "0",
                "HARNESS_DATA_DIR": str(Path(tmp) / "data"),
                "HARNESS_PROJECT_ROOTS": tmp,
                "OLLAMA_BASE_URL": "http://127.0.0.1:9/v1",
            },
            clear=False,
        ):
            settings = self._settings(tmp)
            db, gateway, missions = build_runtime(settings)
            detail = missions.create_mission(
                {
                    "project_path": tmp,
                    "prompt": "Verify the offline harness lifecycle without editing files.",
                    "model_id": "mock-local",
                    "profile_id": "read_only",
                    "workflow": "plan_execute_verify",
                    "local_only": True,
                    "budget_usd": 0,
                }
            )
            mission_id = detail["mission"]["id"]
            for _ in range(100):
                detail = missions.get_detail(mission_id)
                if detail["mission"]["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.05)
            self.assertEqual(detail["mission"]["status"], "completed")
            self.assertTrue(detail["integrity"]["ok"])
            names = [item["name"] for item in detail["messages"]]
            self.assertIn("planner", names)
            self.assertIn("executor", names)
            self.assertIn("verifier", names)
            missions.pool.shutdown(wait=True)

    def test_http_health_and_config(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "HARNESS_BIND": "127.0.0.1",
                "HARNESS_PORT": "0",
                "HARNESS_DATA_DIR": str(Path(tmp) / "data"),
                "HARNESS_PROJECT_ROOTS": tmp,
            },
            clear=False,
        ):
            settings = self._settings(tmp)
            db, gateway, missions = build_runtime(settings)
            server = create_server(settings, db, gateway, missions)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
                    health = json.loads(response.read())
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=3) as response:
                    config = json.loads(response.read())
                self.assertTrue(health["ok"])
                self.assertTrue(any(item["id"] == "deepseek-flash" for item in config["models"]))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_mutation_waits_for_one_time_approval(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "HARNESS_BIND": "127.0.0.1",
                "HARNESS_PORT": "0",
                "HARNESS_DATA_DIR": str(Path(tmp) / "data"),
                "HARNESS_PROJECT_ROOTS": tmp,
            },
            clear=False,
        ):
            settings = self._settings(tmp)
            db, gateway, missions = build_runtime(settings)
            executor_calls = {"count": 0}

            def fake_complete(candidates, messages, *, tools=None, max_tokens=4096, role="executor"):
                if role == "planner":
                    response = ModelResponse("mock-local", "mock", "PLAN\n1. Write the approved test file.\n2. Verify it.")
                elif role == "verifier":
                    response = ModelResponse("mock-local", "mock", "VERDICT: PASS\nApproved file exists in the scoped project.")
                elif executor_calls["count"] == 0:
                    executor_calls["count"] += 1
                    response = ModelResponse(
                        "mock-local",
                        "mock",
                        "I propose one scoped write.",
                        tool_calls=[ToolCall("call_write", "write_file", {"path": "approved.txt", "content": "approved\n"})],
                    )
                else:
                    response = ModelResponse("mock-local", "mock", "The approved write completed and is ready for verification.")
                return response, []

            gateway.complete_with_fallback = fake_complete
            detail = missions.create_mission(
                {
                    "project_path": tmp,
                    "prompt": "Create approved.txt only after human approval.",
                    "model_id": "mock-local",
                    "profile_id": "coding",
                    "workflow": "plan_execute_verify",
                    "local_only": True,
                    "budget_usd": 0,
                }
            )
            mission_id = detail["mission"]["id"]
            for _ in range(100):
                detail = missions.get_detail(mission_id)
                if detail["mission"]["status"] == "waiting_approval":
                    break
                time.sleep(0.03)
            self.assertEqual(detail["mission"]["status"], "waiting_approval")
            self.assertFalse((Path(tmp) / "approved.txt").exists())
            pending = [item for item in detail["approvals"] if item["status"] == "pending"]
            self.assertEqual(len(pending), 1)
            missions.resolve_approval(pending[0]["id"], True)
            for _ in range(100):
                detail = missions.get_detail(mission_id)
                if detail["mission"]["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.03)
            self.assertEqual(detail["mission"]["status"], "completed")
            self.assertEqual((Path(tmp) / "approved.txt").read_text(), "approved\n")
            self.assertTrue(detail["integrity"]["ok"])
            missions.pool.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
