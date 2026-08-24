"""Phase 2 — join outage events to cause-labeled events within a time/geo window.

For every outage still marked `unexplained`, look for GDACS/ACLED/#KeepItOn
records in the same country within ATTRIBUTION_WINDOW_HOURS (default 72h,
see config.py) of the outage's start time. The closest-in-time candidate
wins; ties break by source priority (a verified shutdown record is stronger
evidence than a time/geo-proximate disaster or conflict event). Confidence
tier is a direct function of how tight the time match is — this is a
correlation, not proof, per the project's "Correlation != causation"
principle: it's a documented heuristic, not a certainty claim.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from . import config, db

logger = logging.getLogger(__name__)

# Closest source wins on time; ties break in this order.
SOURCE_PRIORITY = {"keepiton": 0, "acled": 1, "gdacs": 2}


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _best_match(outage_start: datetime, candidates: list[sqlite3.Row]) -> sqlite3.Row | None:
    scored = []
    for c in candidates:
        try:
            cause_dt = _parse(c["event_date"])
        except ValueError:
            continue
        delta_hours = abs((outage_start - cause_dt).total_seconds()) / 3600
        if delta_hours > config.ATTRIBUTION_WINDOW_HOURS:
            continue
        scored.append((delta_hours, SOURCE_PRIORITY.get(c["source"], 9), c))
    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1]))
    return scored[0][2]


def _confidence_for_delta(delta_hours: float) -> str:
    if delta_hours <= 24:
        return "high"
    if delta_hours <= 48:
        return "medium"
    return "low"


def run(conn: sqlite3.Connection) -> dict[str, int]:
    unexplained = db.fetch_unexplained_events(conn)
    stats = {"matched": 0, "still_unexplained": 0}

    # Group cause_events by country to avoid re-querying per outage.
    countries = {row["country"] for row in unexplained}
    cause_events_by_country: dict[str, list[sqlite3.Row]] = {
        country: db.fetch_cause_events_for_country(conn, country) for country in countries
    }

    updates = []
    for outage in unexplained:
        candidates = cause_events_by_country.get(outage["country"], [])
        if not candidates:
            stats["still_unexplained"] += 1
            continue
        try:
            outage_start = _parse(outage["timestamp_start"])
        except ValueError:
            stats["still_unexplained"] += 1
            continue

        match = _best_match(outage_start, candidates)
        if match is None:
            stats["still_unexplained"] += 1
            continue

        delta_hours = abs((outage_start - _parse(match["event_date"])).total_seconds()) / 3600
        confidence = _confidence_for_delta(delta_hours)
        updates.append((match["cause"], match["cause_subtype"], confidence, outage["event_id"]))
        stats["matched"] += 1

    conn.executemany(
        "UPDATE events SET cause = ?, cause_subtype = ?, confidence = ? WHERE event_id = ?",
        updates,
    )
    logger.info("Attribution: matched %d, still unexplained %d", stats["matched"], stats["still_unexplained"])
    return stats
