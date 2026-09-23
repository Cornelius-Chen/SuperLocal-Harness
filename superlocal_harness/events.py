from __future__ import annotations

import hashlib
import json
from typing import Any

from .db import Database, utc_now


GENESIS_HASH = "0" * 64


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class EventStore:
    """Append-only, per-stream hash-chained event log."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def calculate_hash(
        stream_id: str,
        sequence: int,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        previous_hash: str,
        created_at: str,
    ) -> str:
        body = "|".join(
            [stream_id, str(sequence), event_type, actor, _canonical(payload), previous_hash, created_at]
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def append(
        self,
        stream_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        actor: str = "system",
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        created_at = utc_now()
        with self.db.transaction() as conn:
            previous = conn.execute(
                "SELECT sequence, event_hash FROM events WHERE stream_id = ? ORDER BY sequence DESC LIMIT 1",
                (stream_id,),
            ).fetchone()
            sequence = int(previous["sequence"]) + 1 if previous else 1
            previous_hash = previous["event_hash"] if previous else GENESIS_HASH
            event_hash = self.calculate_hash(
                stream_id, sequence, event_type, actor, payload, previous_hash, created_at
            )
            conn.execute(
                """
                INSERT INTO events(
                    stream_id, sequence, event_type, actor, payload_json,
                    previous_hash, event_hash, correlation_id, causation_id, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    stream_id, sequence, event_type, actor, _canonical(payload), previous_hash,
                    event_hash, correlation_id, causation_id, created_at,
                ),
            )
        return {
            "stream_id": stream_id,
            "sequence": sequence,
            "event_type": event_type,
            "actor": actor,
            "payload": payload,
            "previous_hash": previous_hash,
            "event_hash": event_hash,
            "created_at": created_at,
        }

    def list(self, stream_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            "SELECT * FROM events WHERE stream_id = ? ORDER BY sequence", (stream_id,)
        )
        for row in rows:
            row["payload"] = json.loads(row.pop("payload_json"))
        return rows

    def verify(self, stream_id: str) -> tuple[bool, str]:
        previous_hash = GENESIS_HASH
        expected_sequence = 1
        for event in self.list(stream_id):
            if event["sequence"] != expected_sequence:
                return False, f"sequence gap at {expected_sequence}"
            if event["previous_hash"] != previous_hash:
                return False, f"previous hash mismatch at {expected_sequence}"
            calculated = self.calculate_hash(
                event["stream_id"], event["sequence"], event["event_type"], event["actor"],
                event["payload"], event["previous_hash"], event["created_at"],
            )
            if calculated != event["event_hash"]:
                return False, f"event hash mismatch at {expected_sequence}"
            previous_hash = event["event_hash"]
            expected_sequence += 1
        return True, "ok"

    def last_hash(self, stream_id: str) -> str:
        row = self.db.fetch_one(
            "SELECT event_hash FROM events WHERE stream_id = ? ORDER BY sequence DESC LIMIT 1",
            (stream_id,),
        )
        return row["event_hash"] if row else GENESIS_HASH

