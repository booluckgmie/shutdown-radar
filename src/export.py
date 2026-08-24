"""Export the events table to the JSON payload the dashboard embeds.

Aggregation (bubble sizing, cause breakdown, resilience ranking) happens
client-side in the dashboard JS so filters (cause/region/time/confidence)
can re-aggregate live without a server round-trip — this module ships the
full event list (each enriched with derived region tags), a sources
catalog for the "Data Pipeline & Sources" panel, and a small meta block.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from . import config, db, geo, regions

# Static reference catalog of every source the pipeline knows how to use —
# independent of whether it produced any data this run. "configured" and
# "contributed" below are computed per run and merged in.
SOURCE_CATALOG = [
    {
        "name": "IODA", "phase": 1, "category": "Base outage signal",
        "realtime": "~hourly", "access": "Free, no key",
        "granularity": "Country / ASN", "key_required": False,
        "family_prefix": "IODA",
    },
    {
        "name": "Cloudflare Radar", "phase": 1, "category": "Base outage signal",
        "realtime": "Real-time", "access": "Free tier, API token",
        "granularity": "Country", "key_required": True,
        "family_prefix": "Cloudflare Radar",
    },
    {
        "name": "RIPE Atlas", "phase": 1, "category": "Base outage signal",
        "realtime": "Real-time", "access": "Free, no key",
        "granularity": "City / ISP", "key_required": False,
        "family_prefix": "RIPE Atlas",
    },
    {
        "name": "GDACS", "phase": 2, "category": "Disaster attribution",
        "realtime": "Real-time", "access": "Free, no key",
        "granularity": "Lat/lon", "key_required": False,
        "cause_source_key": "gdacs",
    },
    {
        "name": "ACLED", "phase": 2, "category": "Conflict attribution",
        "realtime": "Near real-time", "access": "Free, registration",
        "granularity": "Lat/lon", "key_required": True,
        "cause_source_key": "acled",
    },
    {
        "name": "#KeepItOn (Access Now)", "phase": 2, "category": "Shutdown attribution",
        "realtime": "Manually verified", "access": "Free — manual CSV export (no stable API)",
        "granularity": "Country/region", "key_required": True,
        "cause_source_key": "keepiton",
    },
    {
        "name": "Serper.dev + Groq", "phase": 3, "category": "Semantic gap-filling (news + LLM)",
        "realtime": "Real-time", "access": "Free tier, 2 API keys",
        "granularity": "Geocoded via Nominatim", "key_required": True,
        "family_prefix": "Serper+Groq",
    },
]


def _region_tags_for_event(row: dict[str, Any]) -> list[str]:
    code = geo.code_for_name(row.get("country"))
    return regions.regions_for(code)


def _sources_status(conn: sqlite3.Connection, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settings = config.SETTINGS
    key_flags = {
        "Cloudflare Radar": settings.has_cloudflare(),
        "ACLED": settings.has_acled(),
        "#KeepItOn (Access Now)": settings.has_keepiton(),
        "Serper.dev + Groq": settings.has_semantic(),
    }

    event_counts_by_family: dict[str, int] = {}
    for e in events:
        name = e.get("source_name") or ""
        for entry in SOURCE_CATALOG:
            prefix = entry.get("family_prefix")
            if prefix and name.startswith(prefix):
                event_counts_by_family[prefix] = event_counts_by_family.get(prefix, 0) + 1
                break

    cause_event_counts: dict[str, int] = {}
    try:
        rows = conn.execute("SELECT source, COUNT(*) AS n FROM cause_events GROUP BY source").fetchall()
        cause_event_counts = {r["source"]: r["n"] for r in rows}
    except sqlite3.OperationalError:
        pass

    catalog = []
    for entry in SOURCE_CATALOG:
        item = {k: v for k, v in entry.items() if k not in ("family_prefix", "cause_source_key")}
        if entry.get("family_prefix"):
            count = event_counts_by_family.get(entry["family_prefix"], 0)
            item["contributed"] = count
            plural = "s" if count != 1 else ""
            phrase = f"event{plural} resolved" if entry["phase"] == 3 else f"outage event{plural}"
            item["contributed_label"] = f"{count} {phrase}"
        else:
            count = cause_event_counts.get(entry.get("cause_source_key"), 0)
            item["contributed"] = count
            item["contributed_label"] = f"{count} cause record{'s' if count != 1 else ''}"
        item["configured"] = key_flags.get(entry["name"], True) if entry["key_required"] else None
        catalog.append(item)
    return catalog


def build_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = db.events_to_dicts(db.fetch_all_events(conn))
    for r in rows:
        r["regions"] = _region_tags_for_event(r)

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
            "region_options": regions.FILTER_OPTIONS,
            "sources_catalog": _sources_status(conn, rows),
        },
    }
