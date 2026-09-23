import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from superlocal_harness.config import ModelProfile
from superlocal_harness.db import SCHEMA_V1, Database
from superlocal_harness.gateway import ModelGateway


class UsageSemanticsTests(unittest.TestCase):
    def test_existing_usage_rows_migrate_as_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "usage.sqlite"
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript(SCHEMA_V1)
                conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES(1, ?)", (datetime.now(timezone.utc).isoformat(),))
                conn.execute(
                    "INSERT INTO usage(mission_id, model_id, provider, success, created_at) VALUES(?,?,?,?,?)",
                    ("old", "old-cloud", "openai", 1, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
            db = Database(path)
            self.assertIsNone(db.usage_summary()["total"]["input_tokens"])
            self.assertIsNone(db.usage_summary()["total"]["cost_usd"])

    def test_missing_provider_usage_is_not_a_measured_zero(self):
        model = ModelProfile(
            id="test-cloud", label="Test", provider="openai", api_model="test",
            base_url="http://127.0.0.1:9/v1", input_per_million=1.0,
        )
        body = json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}).encode()

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return body

        with patch("urllib.request.urlopen", return_value=Reply()):
            response = ModelGateway(SimpleNamespace(request_timeout_seconds=3)).complete(model, [])
        self.assertFalse(response.usage_reported)

    def test_usage_summary_distinguishes_unknown_from_measured_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "usage.sqlite")
            db.create_mission({
                "id": "m", "title": "Test", "prompt": "Test", "project_path": tmp,
                "profile_id": "read_only", "workflow": "solo", "requested_model_id": "test-cloud",
                "status": "completed", "stage": "execution", "budget_usd": 1.0, "max_steps": 1,
            }, {})
            db.add_usage({
                "mission_id": "m", "model_id": "known", "provider": "mock",
                "usage_reported": True, "cost_known": True, "success": True,
            })
            self.assertEqual(db.usage_summary()["total"]["input_tokens"], 0)
            self.assertEqual(db.usage_summary()["total"]["cost_usd"], 0)
            db.add_usage({
                "mission_id": "m", "model_id": "unknown", "provider": "openai",
                "usage_reported": False, "cost_known": False, "success": True,
            })
            summary = db.usage_summary()
            self.assertIsNone(summary["total"]["input_tokens"])
            self.assertIsNone(summary["total"]["cost_usd"])
            self.assertIsNone(next(row for row in summary["by_model"] if row["model_id"] == "unknown")["cost_usd"])


if __name__ == "__main__":
    unittest.main()
