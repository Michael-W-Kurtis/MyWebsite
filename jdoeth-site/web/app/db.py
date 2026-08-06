"""SQLite access layer.

Concurrency note: the web service is the only thing that writes. It runs two uvicorn
workers, so there are two writing processes, which WAL mode plus a 10s busy_timeout
handles correctly — SQLite serialises writers at the file level and a blocked writer
retries rather than failing. What SQLite does not tolerate is that file living on
NFS or EFS, where its locking guarantees do not hold. That constraint, not a traffic
number, is what decides when you move to Postgres. See the AWS notes in the README.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS visitors (
    ip           TEXT PRIMARY KEY,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    visit_count  INTEGER NOT NULL DEFAULT 0,
    last_path    TEXT,
    last_agent   TEXT
);

CREATE TABLE IF NOT EXISTS visits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ip         TEXT NOT NULL,
    path       TEXT NOT NULL,
    user_agent TEXT,
    ts         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(ts);
CREATE INDEX IF NOT EXISTS idx_visits_ip ON visits(ip);

CREATE TABLE IF NOT EXISTS sort_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ip           TEXT,
    source       TEXT NOT NULL,          -- 'portrait' or 'upload'
    command      TEXT NOT NULL,
    duration_ms  INTEGER,
    status       TEXT NOT NULL,          -- 'ok' | 'error' | 'timeout'
    ts           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_ts ON sort_jobs(ts);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=10000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Idempotent. The db container also applies this, whichever starts first wins."""
    with connect() as conn:
        conn.executescript(SCHEMA)


def record_visit(ip: str, path: str, user_agent: str | None) -> None:
    now = _now()
    agent = (user_agent or "")[:300]
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO visitors (ip, first_seen, last_seen, visit_count, last_path, last_agent)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(ip) DO UPDATE SET
                last_seen   = excluded.last_seen,
                visit_count = visitors.visit_count + 1,
                last_path   = excluded.last_path,
                last_agent  = excluded.last_agent
            """,
            (ip, now, now, path, agent),
        )
        conn.execute(
            "INSERT INTO visits (ip, path, user_agent, ts) VALUES (?, ?, ?, ?)",
            (ip, path, agent, now),
        )


def visitor_rows() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT ip, first_seen, last_seen, visit_count, last_path
            FROM visitors
            ORDER BY last_seen DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def summary() -> dict:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM visitors)               AS unique_ips,
                (SELECT COALESCE(SUM(visit_count), 0) FROM visitors) AS total_visits,
                (SELECT COUNT(*) FROM sort_jobs WHERE status = 'ok') AS sorts_completed,
                (SELECT MAX(last_seen) FROM visitors)         AS latest
            """
        ).fetchone()
    return dict(row)


def record_job(ip: str, source: str, command: str, duration_ms: int, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO sort_jobs (ip, source, command, duration_ms, status, ts)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (ip, source, command, duration_ms, status, _now()),
        )
