"""SQLite persistence for the unified Event schema plus raw cause-event staging."""
from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    region_name TEXT,
    country TEXT NOT NULL,
    asn TEXT,
    timestamp_start TEXT NOT NULL,
    timestamp_end TEXT,
    duration_hours REAL,
    cause TEXT NOT NULL CHECK (cause IN ('disaster', 'conflict', 'shutdown', 'unexplained')),
    cause_subtype TEXT,
    source_type TEXT NOT NULL CHECK (source_type IN ('structured', 'semantic')),
    source_name TEXT NOT NULL,
    confidence TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    severity_score REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_country ON events(country);
CREATE INDEX IF NOT EXISTS idx_events_cause ON events(cause);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(timestamp_start);

-- Raw cause-labeled events pulled from GDACS / ACLED / #KeepItOn before joining.
-- Kept around for auditability and so re-running attribution doesn't require
-- re-fetching the sources.
CREATE TABLE IF NOT EXISTS cause_events (
    cause_event_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,              -- gdacs | acled | keepiton
    cause TEXT NOT NULL,               -- disaster | conflict | shutdown
    cause_subtype TEXT,
    country TEXT,
    lat REAL,
    lon REAL,
    event_date TEXT NOT NULL,
    title TEXT,
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_cause_events_country ON cause_events(country);
CREATE INDEX IF NOT EXISTS idx_cause_events_date ON cause_events(event_date);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    sources_ok TEXT,
    sources_failed TEXT,
    notes TEXT
);
"""


@contextlib.contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def upsert_events(conn: sqlite3.Connection, events: Iterable[dict[str, Any]]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for e in events:
        cause = e.get("cause", "unexplained")
        # An event with no attributed cause cannot be "high" or "medium"
        # confidence in that (nonexistent) cause — confidence tracks the
        # strength of the cause match, so "unexplained" is low by
        # definition. Enforced here, once, rather than trusting every
        # caller (ingestion connector, attribution join, semantic layer,
        # seed data) to get it right independently.
        confidence = "low" if cause == "unexplained" else e.get("confidence", "low")
        rows.append(
            (
                e["event_id"],
                e["lat"],
                e["lon"],
                e.get("region_name"),
                e["country"],
                e.get("asn"),
                e["timestamp_start"],
                e.get("timestamp_end"),
                e.get("duration_hours"),
                cause,
                e.get("cause_subtype"),
                e.get("source_type", "structured"),
                e["source_name"],
                confidence,
                e.get("severity_score", 0.0),
                now,
            )
        )
    conn.executemany(
        """
        INSERT INTO events (
            event_id, lat, lon, region_name, country, asn,
            timestamp_start, timestamp_end, duration_hours,
            cause, cause_subtype, source_type, source_name,
            confidence, severity_score, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(event_id) DO UPDATE SET
            lat=excluded.lat, lon=excluded.lon, region_name=excluded.region_name,
            country=excluded.country, asn=excluded.asn,
            timestamp_start=excluded.timestamp_start, timestamp_end=excluded.timestamp_end,
            duration_hours=excluded.duration_hours, cause=excluded.cause,
            cause_subtype=excluded.cause_subtype, source_type=excluded.source_type,
            source_name=excluded.source_name, confidence=excluded.confidence,
            severity_score=excluded.severity_score, updated_at=excluded.updated_at
        """,
        rows,
    )
    return len(rows)


def upsert_cause_events(conn: sqlite3.Connection, cause_events: Iterable[dict[str, Any]]) -> int:
    rows = []
    for c in cause_events:
        rows.append(
            (
                c["cause_event_id"],
                c["source"],
                c["cause"],
                c.get("cause_subtype"),
                c.get("country"),
                c.get("lat"),
                c.get("lon"),
                c["event_date"],
                c.get("title"),
                json.dumps(c.get("raw"), default=str) if c.get("raw") is not None else None,
            )
        )
    conn.executemany(
        """
        INSERT INTO cause_events (
            cause_event_id, source, cause, cause_subtype, country, lat, lon,
            event_date, title, raw_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(cause_event_id) DO UPDATE SET
            source=excluded.source, cause=excluded.cause, cause_subtype=excluded.cause_subtype,
            country=excluded.country, lat=excluded.lat, lon=excluded.lon,
            event_date=excluded.event_date, title=excluded.title, raw_json=excluded.raw_json
        """,
        rows,
    )
    return len(rows)


def fetch_unexplained_events(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM events WHERE cause = 'unexplained'").fetchall()


def fetch_all_events(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM events ORDER BY timestamp_start DESC").fetchall()


def fetch_cause_events_for_country(conn: sqlite3.Connection, country: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM cause_events WHERE country = ?", (country,)).fetchall()


def record_run(
    conn: sqlite3.Connection,
    started_at: str,
    finished_at: str,
    sources_ok: list[str],
    sources_failed: list[str],
    notes: str = "",
) -> None:
    conn.execute(
        "INSERT INTO pipeline_runs (started_at, finished_at, sources_ok, sources_failed, notes) "
        "VALUES (?,?,?,?,?)",
        (started_at, finished_at, ",".join(sources_ok), ",".join(sources_failed), notes),
    )


def events_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
