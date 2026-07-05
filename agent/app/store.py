"""SQLite persistence: incidents + append-only event log.

Design notes an interviewer will ask about:
- stdlib sqlite3 in WAL mode; every write goes through one asyncio.Lock.
  Writes here are tiny (~30 events per incident), so a single serialized
  writer is simpler and safely fast — no ORM, no aiosqlite dependency.
- events.global_seq (the AUTOINCREMENT rowid) doubles as the SSE `id:`,
  which is what makes reconnect replay via Last-Event-ID work across
  agent restarts.
"""

import asyncio
import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
  id              TEXT PRIMARY KEY,
  created_at      TEXT NOT NULL,
  resolved_at     TEXT,
  status          TEXT NOT NULL,
  service         TEXT,
  severity        TEXT,
  title           TEXT,
  fingerprint     TEXT,
  alert_json      TEXT,
  diagnosis_json  TEXT,
  impact_json     TEXT,
  postmortem_md   TEXT,
  postmortem_path TEXT
);
CREATE TABLE IF NOT EXISTS events (
  global_seq   INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id     TEXT NOT NULL,
  incident_id  TEXT NOT NULL,
  seq          INTEGER NOT NULL,
  ts           TEXT NOT NULL,
  type         TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_incident ON events(incident_id, seq);
"""

_JSON_FIELDS = {"alert_json", "diagnosis_json", "impact_json"}


class Store:
    def __init__(self, db_path: str):
        Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = asyncio.Lock()

    # -- incidents ---------------------------------------------------------

    async def create_incident(self, incident: dict) -> None:
        columns = ", ".join(incident)
        placeholders = ", ".join("?" for _ in incident)
        async with self._lock:
            self._conn.execute(
                f"INSERT INTO incidents ({columns}) VALUES ({placeholders})",
                list(incident.values()),
            )
            self._conn.commit()

    async def update_incident(self, incident_id: str, **fields) -> None:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        async with self._lock:
            self._conn.execute(
                f"UPDATE incidents SET {assignments} WHERE id = ?",
                [*fields.values(), incident_id],
            )
            self._conn.commit()

    def get_incident(self, incident_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()
        return self._hydrate(row) if row else None

    def find_unresolved_by_fingerprint(self, fingerprint: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM incidents WHERE fingerprint = ? AND resolved_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (fingerprint,),
        ).fetchone()
        return self._hydrate(row) if row else None

    def list_incidents(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, created_at, resolved_at, status, service, severity, title "
            "FROM incidents ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def list_unfinished(self) -> list[dict]:
        """Incidents in a non-terminal state — candidates for startup recovery."""
        rows = self._conn.execute(
            "SELECT id, status FROM incidents WHERE status != 'postmortem_published'"
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _hydrate(row: sqlite3.Row) -> dict:
        incident = dict(row)
        for field in _JSON_FIELDS:
            raw = incident.pop(field, None)
            incident[field.removesuffix("_json")] = json.loads(raw) if raw else None
        return incident

    # -- events ------------------------------------------------------------

    async def append_event(self, event_id: str, incident_id: str, ts: str,
                           type_: str, payload: dict) -> tuple[int, int]:
        """Insert one event; returns (seq, global_seq). Atomic under the write lock."""
        async with self._lock:
            (seq,) = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            cursor = self._conn.execute(
                "INSERT INTO events (event_id, incident_id, seq, ts, type, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, incident_id, seq, ts, type_, json.dumps(payload)),
            )
            self._conn.commit()
            return seq, cursor.lastrowid

    def events_for(self, incident_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE incident_id = ? ORDER BY global_seq",
            (incident_id,),
        ).fetchall()
        return [self._envelope(row) for row in rows]

    def max_global_seq(self) -> int:
        (value,) = self._conn.execute(
            "SELECT COALESCE(MAX(global_seq), 0) FROM events"
        ).fetchone()
        return value

    def events_after(self, global_seq: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE global_seq > ? ORDER BY global_seq",
            (global_seq,),
        ).fetchall()
        return [self._envelope(row) for row in rows]

    @staticmethod
    def _envelope(row: sqlite3.Row) -> dict:
        return {
            "event_id": row["event_id"],
            "incident_id": row["incident_id"],
            "seq": row["seq"],
            "global_seq": row["global_seq"],
            "ts": row["ts"],
            "type": row["type"],
            "payload": json.loads(row["payload_json"]),
        }
