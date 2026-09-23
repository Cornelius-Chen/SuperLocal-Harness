from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS missions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    project_path TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    workflow TEXT NOT NULL,
    requested_model_id TEXT NOT NULL,
    actual_model_id TEXT,
    verifier_model_id TEXT,
    local_only INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    budget_usd REAL NOT NULL,
    spent_usd REAL NOT NULL DEFAULT 0,
    max_steps INTEGER NOT NULL,
    step_count INTEGER NOT NULL DEFAULT 0,
    repair_count INTEGER NOT NULL DEFAULT 0,
    parent_id TEXT,
    result TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(parent_id) REFERENCES missions(id)
);

CREATE INDEX IF NOT EXISTS idx_missions_status ON missions(status, updated_at);

CREATE TABLE IF NOT EXISTS mission_state (
    mission_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(mission_id) REFERENCES missions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id TEXT NOT NULL,
    role TEXT NOT NULL,
    name TEXT,
    content TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(mission_id) REFERENCES missions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_mission ON messages(mission_id, id);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(stream_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_events_stream ON events(stream_id, sequence);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY(mission_id) REFERENCES missions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, created_at);

CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(mission_id) REFERENCES missions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_usage_created ON usage(created_at);

CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    state_json TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(mission_id) REFERENCES missions(id) ON DELETE CASCADE
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def migrate(self) -> None:
        with closing(self.connect()) as conn:
            conn.executescript(SCHEMA_V1)
            exists = conn.execute("SELECT 1 FROM schema_migrations WHERE version = 1").fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES(1, ?)",
                    (utc_now(),),
                )
            exists = conn.execute("SELECT 1 FROM schema_migrations WHERE version = 2").fetchone()
            if not exists:
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute("ALTER TABLE usage ADD COLUMN usage_reported INTEGER NOT NULL DEFAULT 0")
                    conn.execute("ALTER TABLE usage ADD COLUMN cost_known INTEGER NOT NULL DEFAULT 0")
                    conn.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES(2, ?)",
                        (utc_now(),),
                    )

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with closing(self.connect()) as conn:
            cursor = conn.execute(sql, params)
            return int(cursor.lastrowid or 0)

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with closing(self.connect()) as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with closing(self.connect()) as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def create_mission(self, record: dict[str, Any], initial_state: dict[str, Any]) -> None:
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO missions(
                    id, title, prompt, project_path, profile_id, workflow,
                    requested_model_id, actual_model_id, verifier_model_id, local_only,
                    status, stage, budget_usd, spent_usd, max_steps, step_count,
                    repair_count, parent_id, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record["id"], record["title"], record["prompt"], record["project_path"],
                    record["profile_id"], record["workflow"], record["requested_model_id"],
                    record.get("actual_model_id"), record.get("verifier_model_id"),
                    int(record.get("local_only", False)), record["status"], record["stage"],
                    record["budget_usd"], 0.0, record["max_steps"], 0, 0,
                    record.get("parent_id"), now, now,
                ),
            )
            conn.execute(
                "INSERT INTO mission_state(mission_id, state_json, updated_at) VALUES(?,?,?)",
                (record["id"], json.dumps(initial_state, ensure_ascii=False), now),
            )

    def update_mission(self, mission_id: str, **changes: Any) -> None:
        allowed = {
            "actual_model_id", "verifier_model_id", "status", "stage", "spent_usd",
            "step_count", "repair_count", "result", "error", "workflow",
        }
        invalid = set(changes) - allowed
        if invalid:
            raise ValueError(f"Invalid mission columns: {sorted(invalid)}")
        if not changes:
            return
        changes["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in changes)
        values = tuple(changes.values()) + (mission_id,)
        self.execute(f"UPDATE missions SET {assignments} WHERE id = ?", values)

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM missions WHERE id = ?", (mission_id,))

    def list_missions(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.fetch_all(
            "SELECT * FROM missions ORDER BY updated_at DESC LIMIT ?", (min(limit, 200),)
        )

    def add_message(
        self,
        mission_id: str,
        role: str,
        content: str | None,
        *,
        name: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> int:
        return self.execute(
            "INSERT INTO messages(mission_id, role, name, content, meta_json, created_at) VALUES(?,?,?,?,?,?)",
            (mission_id, role, name, content, json.dumps(meta or {}, ensure_ascii=False), utc_now()),
        )

    def get_messages(self, mission_id: str) -> list[dict[str, Any]]:
        rows = self.fetch_all("SELECT * FROM messages WHERE mission_id = ? ORDER BY id", (mission_id,))
        for row in rows:
            row["meta"] = json.loads(row.pop("meta_json") or "{}")
        return rows

    def get_state(self, mission_id: str) -> dict[str, Any]:
        row = self.fetch_one("SELECT state_json FROM mission_state WHERE mission_id = ?", (mission_id,))
        return json.loads(row["state_json"]) if row else {}

    def put_state(self, mission_id: str, state: dict[str, Any]) -> None:
        self.execute(
            "UPDATE mission_state SET state_json = ?, updated_at = ? WHERE mission_id = ?",
            (json.dumps(state, ensure_ascii=False), utc_now(), mission_id),
        )

    def create_approval(self, record: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO approvals(
                id, mission_id, tool_call_id, tool_name, args_json, status,
                reason, created_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                record["id"], record["mission_id"], record["tool_call_id"],
                record["tool_name"], json.dumps(record["args"], ensure_ascii=False),
                "pending", record["reason"], utc_now(),
            ),
        )

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = self.fetch_one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        if row:
            row["args"] = json.loads(row.pop("args_json"))
            row["result"] = json.loads(row.pop("result_json")) if row.get("result_json") else None
        return row

    def list_approvals(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.fetch_all(
                "SELECT * FROM approvals WHERE status = ? ORDER BY created_at", (status,)
            )
        else:
            rows = self.fetch_all("SELECT * FROM approvals ORDER BY created_at DESC LIMIT 200")
        for row in rows:
            row["args"] = json.loads(row.pop("args_json"))
            row["result"] = json.loads(row.pop("result_json")) if row.get("result_json") else None
        return rows

    def resolve_approval(self, approval_id: str, status: str, result: dict[str, Any]) -> None:
        self.execute(
            "UPDATE approvals SET status = ?, result_json = ?, resolved_at = ? WHERE id = ?",
            (status, json.dumps(result, ensure_ascii=False), utc_now(), approval_id),
        )

    def add_usage(self, record: dict[str, Any]) -> None:
        self.execute(
            """
            INSERT INTO usage(
                mission_id, model_id, provider, input_tokens, output_tokens,
                cost_usd, latency_ms, success, error, created_at, usage_reported, cost_known
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record["mission_id"], record["model_id"], record["provider"],
                record.get("input_tokens", 0), record.get("output_tokens", 0),
                record.get("cost_usd", 0.0), record.get("latency_ms", 0),
                int(record.get("success", True)), record.get("error"), utc_now(),
                int(record.get("usage_reported", False)), int(record.get("cost_known", False)),
            ),
        )

    def usage_summary(self) -> dict[str, Any]:
        today = datetime.now(timezone.utc).date().isoformat()
        total = self.fetch_one(
            """
            SELECT CASE WHEN SUM(CASE WHEN success = 1 AND usage_reported = 0 THEN 1 ELSE 0 END) > 0 THEN NULL ELSE COALESCE(SUM(input_tokens),0) END input_tokens,
                   CASE WHEN SUM(CASE WHEN success = 1 AND usage_reported = 0 THEN 1 ELSE 0 END) > 0 THEN NULL ELSE COALESCE(SUM(output_tokens),0) END output_tokens,
                   CASE WHEN SUM(CASE WHEN success = 1 AND cost_known = 0 THEN 1 ELSE 0 END) > 0 THEN NULL ELSE COALESCE(SUM(cost_usd),0) END cost_usd,
                   COUNT(*) calls
            FROM usage WHERE substr(created_at,1,10) = ?
            """,
            (today,),
        ) or {}
        by_model = self.fetch_all(
            """
            SELECT model_id, COUNT(*) calls,
                   CASE WHEN SUM(CASE WHEN success = 1 AND usage_reported = 0 THEN 1 ELSE 0 END) > 0 THEN NULL ELSE COALESCE(SUM(input_tokens),0) END input_tokens,
                   CASE WHEN SUM(CASE WHEN success = 1 AND usage_reported = 0 THEN 1 ELSE 0 END) > 0 THEN NULL ELSE COALESCE(SUM(output_tokens),0) END output_tokens,
                   CASE WHEN SUM(CASE WHEN success = 1 AND cost_known = 0 THEN 1 ELSE 0 END) > 0 THEN NULL ELSE COALESCE(SUM(cost_usd),0) END cost_usd
            FROM usage WHERE substr(created_at,1,10) = ?
            GROUP BY model_id ORDER BY calls DESC
            """,
            (today,),
        )
        return {"date": today, "total": total, "by_model": by_model}

    def add_checkpoint(self, mission_id: str, stage: str, state: dict[str, Any], event_hash: str) -> None:
        self.execute(
            "INSERT INTO checkpoints(mission_id, stage, state_json, event_hash, created_at) VALUES(?,?,?,?,?)",
            (mission_id, stage, json.dumps(state, ensure_ascii=False), event_hash, utc_now()),
        )
