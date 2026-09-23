import tempfile
import unittest
from pathlib import Path

from ironman_harness.db import Database
from ironman_harness.events import EventStore


class EventStoreTests(unittest.TestCase):
    def test_hash_chain_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.db")
            events = EventStore(db)
            events.append("mission:test", "Created", {"value": 1}, actor="user")
            events.append("mission:test", "Advanced", {"value": 2}, actor="runtime")
            self.assertEqual(events.verify("mission:test"), (True, "ok"))
            db.execute(
                "UPDATE events SET payload_json = ? WHERE stream_id = ? AND sequence = 1",
                ('{"value":999}', "mission:test"),
            )
            ok, detail = events.verify("mission:test")
            self.assertFalse(ok)
            self.assertIn("hash mismatch", detail)


if __name__ == "__main__":
    unittest.main()

