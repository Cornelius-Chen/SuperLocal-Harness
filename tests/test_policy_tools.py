import tempfile
import unittest
from pathlib import Path

from superlocal_harness.config import RuntimeProfile
from superlocal_harness.db import Database
from superlocal_harness.policy import PolicyAction, PolicyEngine
from superlocal_harness.tools import ToolContext, ToolExecutor


PROFILE = RuntimeProfile(
    id="test",
    label="Test",
    description="",
    allowed_tools=(
        "list_files", "read_file", "search_text", "update_state",
        "write_file", "apply_patch", "shell",
    ),
    denied_path_parts=("sealed", ".env"),
    mutating_tools=True,
)


class PolicyToolTests(unittest.TestCase):
    def test_escape_sealed_and_dangerous_actions_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            policy = PolicyEngine()
            with self.assertRaises(PermissionError):
                policy.validate_path(root, "../outside.txt", PROFILE)
            with self.assertRaises(PermissionError):
                policy.validate_path(root, "sealed/teacher.csv", PROFILE)
            decision = policy.decide("shell", {"command": "git reset --hard"}, profile=PROFILE, root=root)
            self.assertEqual(decision.action, PolicyAction.DENY)
            trading = policy.decide(
                "shell", {"command": "python place_order.py --broker ibkr"}, profile=PROFILE, root=root
            )
            self.assertEqual(trading.action, PolicyAction.DENY)
            write = policy.decide("write_file", {"path": "safe.txt"}, profile=PROFILE, root=root)
            self.assertEqual(write.action, PolicyAction.APPROVAL)
            sealed_patch = policy.decide(
                "apply_patch",
                {"patch": "--- a/sealed/result.txt\n+++ b/sealed/result.txt\n@@ -1 +1 @@\n-old\n+new\n"},
                profile=PROFILE,
                root=root,
            )
            self.assertEqual(sealed_patch.action, PolicyAction.DENY)

    def test_file_tools_are_scoped_and_state_is_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            db = Database(root / "test.db")
            # Minimal parent mission records for foreign keys.
            db.create_mission(
                {
                    "id": "mission", "title": "t", "prompt": "p", "project_path": str(root),
                    "profile_id": "test", "workflow": "solo", "requested_model_id": "mock-local",
                    "status": "queued", "stage": "execution", "budget_usd": 0, "max_steps": 2,
                },
                {"plan": []},
            )
            tools = ToolExecutor(db, PolicyEngine())
            context = ToolContext("mission", root, PROFILE)
            result = tools.execute("write_file", {"path": "notes/item.txt", "content": "alpha\nbeta"}, context)
            self.assertTrue(result["ok"])
            search = tools.execute("search_text", {"query": "beta", "path": "notes"}, context)
            self.assertEqual(search["matches"][0]["line"], 2)
            tools.execute("update_state", {"facts": ["verified"]}, context)
            self.assertEqual(db.get_state("mission")["facts"], ["verified"])


if __name__ == "__main__":
    unittest.main()
