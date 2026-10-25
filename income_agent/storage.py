from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_items (
    strategy TEXT NOT NULL,
    item_id TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (strategy, item_id)
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    strategy TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('cost', 'revenue')),
    amount REAL NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
"""

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
DONE = "done"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Action:
    id: int
    strategy: str
    kind: str
    title: str
    payload: dict[str, Any]
    status: str
    created_at: str
    decided_at: str | None


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # --- dedupe -----------------------------------------------------------
    def mark_seen(self, strategy: str, item_id: str) -> bool:
        """Returns True if the item was new."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO seen_items (strategy, item_id, seen_at) VALUES (?, ?, ?)",
            (strategy, item_id, now_iso()),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def is_seen(self, strategy: str, item_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM seen_items WHERE strategy = ? AND item_id = ?", (strategy, item_id)
        ).fetchone()
        return row is not None

    # --- approval queue ---------------------------------------------------
    def add_action(self, strategy: str, kind: str, title: str, payload: dict[str, Any],
                   status: str = PENDING) -> int:
        cur = self.conn.execute(
            "INSERT INTO actions (strategy, kind, title, payload, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (strategy, kind, title, json.dumps(payload, ensure_ascii=False), status, now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_action(self, action_id: int) -> Action | None:
        row = self.conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        return self._to_action(row) if row else None

    def list_actions(self, status: str | None = PENDING, limit: int = 100) -> list[Action]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM actions WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._to_action(r) for r in rows]

    def set_status(self, action_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE actions SET status = ?, decided_at = ? WHERE id = ?",
            (status, now_iso(), action_id),
        )
        self.conn.commit()

    def update_payload(self, action_id: int, payload: dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE actions SET payload = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), action_id),
        )
        self.conn.commit()

    def titles(self, strategy: str, kind: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT title FROM actions WHERE strategy = ? AND kind = ? AND status != ?",
            (strategy, kind, REJECTED),
        ).fetchall()
        return [r["title"] for r in rows]

    @staticmethod
    def _to_action(row: sqlite3.Row) -> Action:
        return Action(
            id=row["id"], strategy=row["strategy"], kind=row["kind"], title=row["title"],
            payload=json.loads(row["payload"]), status=row["status"],
            created_at=row["created_at"], decided_at=row["decided_at"],
        )

    # --- ledger -----------------------------------------------------------
    def record(self, strategy: str, kind: str, amount: float, note: str = "") -> None:
        self.conn.execute(
            "INSERT INTO ledger (ts, strategy, kind, amount, note) VALUES (?, ?, ?, ?, ?)",
            (now_iso(), strategy, kind, amount, note),
        )
        self.conn.commit()

    def total(self, kind: str, since: str | None = None, note_prefix: str | None = None) -> float:
        sql = "SELECT COALESCE(SUM(amount), 0) AS s FROM ledger WHERE kind = ?"
        params: list[Any] = [kind]
        if since:
            sql += " AND ts >= ?"
            params.append(since)
        if note_prefix:
            sql += " AND note LIKE ?"
            params.append(note_prefix + "%")
        return float(self.conn.execute(sql, params).fetchone()["s"])

    def summary_by_strategy(self) -> dict[str, dict[str, float]]:
        rows = self.conn.execute(
            "SELECT strategy, kind, SUM(amount) AS s FROM ledger GROUP BY strategy, kind"
        ).fetchall()
        out: dict[str, dict[str, float]] = {}
        for r in rows:
            out.setdefault(r["strategy"], {"cost": 0.0, "revenue": 0.0})[r["kind"]] = float(r["s"])
        return out
