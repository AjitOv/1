"""SQLite job store. One row per (date, channel, slot). Every pipeline stage is idempotent
against this table, so a crashed run can be re-run without double-rendering or double-uploading."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

STATUSES = ("planned", "scripted", "rendering", "rendered", "uploaded", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  date TEXT NOT NULL,
  channel TEXT NOT NULL,
  slot TEXT NOT NULL,
  publish_at TEXT NOT NULL,
  topic_idx INTEGER NOT NULL,
  format TEXT NOT NULL,
  script TEXT,
  heygen_video_id TEXT,
  video_path TEXT,
  youtube_video_id TEXT,
  status TEXT NOT NULL,
  error TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_date ON jobs(date);
CREATE INDEX IF NOT EXISTS jobs_channel ON jobs(channel);
"""


@dataclass
class Job:
    id: str
    date: str
    channel: str
    slot: str
    publish_at: str
    topic_idx: int
    format: str
    script: dict[str, Any] | None
    heygen_video_id: str | None
    video_path: str | None
    youtube_video_id: str | None
    status: str
    error: str | None
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Job":
        d = dict(row)
        d["script"] = json.loads(d["script"]) if d["script"] else None
        return cls(**d)


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def upsert_planned(self, job: Job) -> bool:
        """Insert a planned job. Returns False if the id already exists (no overwrite)."""
        with self._conn() as c:
            exists = c.execute("SELECT 1 FROM jobs WHERE id=?", (job.id,)).fetchone()
            if exists:
                return False
            c.execute(
                "INSERT INTO jobs (id,date,channel,slot,publish_at,topic_idx,format,script,status,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job.id, job.date, job.channel, job.slot, job.publish_at, job.topic_idx, job.format,
                 json.dumps(job.script) if job.script else None, job.status, self._now()),
            )
            return True

    def update(self, job_id: str, **fields: Any) -> None:
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"bad status {fields['status']!r}")
        if "script" in fields and fields["script"] is not None:
            fields["script"] = json.dumps(fields["script"])
        fields["updated_at"] = self._now()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def get(self, job_id: str) -> Job | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return Job.from_row(row) if row else None

    def for_date(self, date: str, channel: str | None = None, status: str | None = None) -> list[Job]:
        q, args = "SELECT * FROM jobs WHERE date=?", [date]
        if channel:
            q += " AND channel=?"; args.append(channel)
        if status:
            q += " AND status=?"; args.append(status)
        q += " ORDER BY channel, slot"
        with self._conn() as c:
            return [Job.from_row(r) for r in c.execute(q, args)]

    def used_pairs(self, channel: str) -> dict[tuple[int, str], str]:
        """(topic_idx, format) -> most recent date it was used on this channel."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT topic_idx, format, MAX(date) AS d FROM jobs WHERE channel=? AND status!='failed'"
                " GROUP BY topic_idx, format", (channel,)).fetchall()
        return {(r["topic_idx"], r["format"]): r["d"] for r in rows}

    def summary(self, date: str) -> dict[str, int]:
        with self._conn() as c:
            rows = c.execute("SELECT status, COUNT(*) n FROM jobs WHERE date=? GROUP BY status", (date,))
            return {r["status"]: r["n"] for r in rows}
