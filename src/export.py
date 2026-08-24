"""Export the events table to the JSON payload the dashboard embeds.

Aggregation (bubble sizing, cause breakdown, resilience ranking) happens
client-side in the dashboard JS so filters (cause/time/confidence) can
re-aggregate live without a server round-trip — this module just ships the
full, filtered-down event list plus a small meta block.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from . import db


def build_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = db.events_to_dicts(db.fetch_all_events(conn))

    causes_breakdown: dict[str, int] = {}
    countries = set()
    dates = []
    for r in rows:
        causes_breakdown[r["cause"]] = causes_breakdown.get(r["cause"], 0) + 1
        countries.add(r["country"])
        dates.append(r["timestamp_start"])

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events": rows,
        "meta": {
            "total_events": len(rows),
            "countries_count": len(countries),
            "causes_breakdown": causes_breakdown,
            "date_min": min(dates) if dates else None,
            "date_max": max(dates) if dates else None,
        },
    }
