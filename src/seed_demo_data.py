"""Synthetic seed data — NOT live source data.

This sandbox's network egress is locked down, so the real connectors in
src/ingestion/ can't be exercised end-to-end here. This module generates a
clearly-labeled, reproducible (seeded RNG) synthetic dataset shaped exactly
like real Phase 1/2 pipeline output, scattered across countries with
well-known conflict, disaster, or shutdown *patterns* (per public trackers
like ACLED and Access Now's #KeepItOn — not specific real incidents; dates,
durations, and severities are all randomized).

Crucially, this module does **not** hand-assign the final `cause` on
outage events. It emits two separate streams — Phase 1 outages (always
`cause="unexplained"`) and Phase 2 cause_events (GDACS/ACLED/#KeepItOn-
shaped) — exactly like the real connectors do, and leaves
`src/attribution.py`'s real join logic to decide which outages get
resolved and to what confidence. Only ~75% of outages get a nearby cause
event at all, so a meaningful share of outages stay genuinely
`unexplained` after attribution runs — the tag is earned, not scripted.
`apply_demo_semantic_layer()` then mimics Phase 3 (LLM+news gap-filling)
for a subset of what's left, the same way the real `src/semantic.py`
would, so the dashboard shows all three confidence pathways with none of
them faked at the top level.

Every record from this module carries `source_name` ending in "(seed)"
and is tagged distinctly so the dashboard can flag it. Swap this out by
running `python main.py fetch --attribute --semantic` with real network
access and API keys (see README) — real runs never touch this module.
"""
from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta, timezone

from . import db, geo

RNG_SEED = 20260824  # fixed for reproducible demo runs
CAUSE_EVENT_MATCH_PROBABILITY = 0.75  # share of outages that get a plausible nearby cause_event
SEMANTIC_RESOLVE_SHARE = 0.4  # share of still-unexplained outages the demo semantic layer resolves

CAUSE_SOURCE = {"conflict": "acled", "disaster": "gdacs", "shutdown": "keepiton"}

# (country_code, cause, cause_subtype, base_source, duration_hours_range,
#  severity_range, occurrences) — base_source is the Phase 1 detector this
# outage would have come from; cause/cause_subtype describe the Phase 2
# cause_event that *may* get generated nearby (see CAUSE_EVENT_MATCH_PROBABILITY).
HOTSPOTS = [
    # -- conflict-pattern countries (per ACLED's public conflict tracking) --
    ("SD", "conflict", "shelling", "IODA", (18, 240), (0.6, 1.0), 6),
    ("UA", "conflict", "infrastructure_strike", "IODA", (2, 48), (0.4, 0.9), 8),
    ("YE", "conflict", "airstrike", "Cloudflare Radar", (6, 96), (0.5, 0.9), 5),
    ("SY", "conflict", "shelling", "IODA", (4, 72), (0.4, 0.8), 5),
    ("ML", "conflict", "militant_activity", "IODA", (8, 120), (0.4, 0.8), 4),
    ("SO", "conflict", "militant_activity", "RIPE Atlas", (4, 60), (0.3, 0.7), 4),
    ("ET", "conflict", "regional_conflict", "IODA", (12, 168), (0.4, 0.8), 4),
    ("CD", "conflict", "militia_clashes", "IODA", (6, 96), (0.3, 0.7), 4),
    ("BF", "conflict", "militant_activity", "RIPE Atlas", (4, 72), (0.3, 0.7), 3),
    ("MM", "conflict", "regional_conflict", "IODA", (12, 200), (0.4, 0.8), 4),
    # -- disaster-pattern countries (per GDACS's hazard coverage) --
    ("PH", "disaster", "tropical_cyclone", "IODA", (4, 120), (0.3, 0.8), 6),
    ("TR", "disaster", "earthquake", "IODA", (2, 336), (0.5, 1.0), 3),
    ("ID", "disaster", "flood", "Cloudflare Radar", (2, 72), (0.3, 0.7), 5),
    ("BD", "disaster", "cyclone", "IODA", (3, 96), (0.3, 0.7), 4),
    ("MZ", "disaster", "cyclone", "IODA", (6, 240), (0.4, 0.9), 3),
    ("HT", "disaster", "hurricane", "Cloudflare Radar", (4, 168), (0.4, 0.8), 3),
    ("NP", "disaster", "earthquake", "IODA", (2, 72), (0.2, 0.6), 2),
    ("VN", "disaster", "typhoon", "IODA", (2, 60), (0.2, 0.6), 4),
    ("MW", "disaster", "flood", "RIPE Atlas", (3, 96), (0.2, 0.6), 3),
    ("CU", "disaster", "hurricane", "IODA", (6, 200), (0.4, 0.9), 3),
    # -- shutdown-pattern countries (per Access Now's #KeepItOn reporting) --
    ("IR", "shutdown", "protest_response", "IODA", (2, 48), (0.6, 1.0), 5),
    ("PK", "shutdown", "election_order", "Cloudflare Radar", (1, 24), (0.5, 0.9), 4),
    ("IN", "shutdown", "regional_order", "IODA", (4, 96), (0.5, 0.9), 5),
    ("GN", "shutdown", "protest_response", "IODA", (2, 48), (0.5, 0.8), 3),
    ("SN", "shutdown", "election_order", "Cloudflare Radar", (1, 36), (0.5, 0.8), 3),
    ("BY", "shutdown", "election_order", "IODA", (1, 18), (0.5, 0.8), 2),
    ("EG", "shutdown", "exam_order", "RIPE Atlas", (2, 12), (0.3, 0.6), 3),
    ("VE", "shutdown", "protest_response", "IODA", (2, 30), (0.4, 0.7), 3),
]

# Countries with only short, low-severity technical blips and no nearby
# cause_event ever generated — these should genuinely stay "unexplained"
# after attribution, and exist specifically to prove the tag isn't just
# a default nobody checks. Deliberately spans regions the HOTSPOTS list
# under-represents (Southeast Asia, South Asia, East Asia) so the region
# filter has coverage everywhere, not just in conflict/disaster hotspots.
UNEXPLAINED_ONLY = [
    ("BR", "RIPE Atlas", (1, 8), (0.1, 0.4), 3),
    ("ZA", "Cloudflare Radar", (1, 10), (0.1, 0.4), 3),
    ("KZ", "RIPE Atlas", (1, 12), (0.1, 0.4), 2),
    ("MX", "Cloudflare Radar", (1, 8), (0.1, 0.3), 2),
    ("MY", "IODA", (1, 6), (0.1, 0.3), 2),
    ("TH", "RIPE Atlas", (1, 6), (0.1, 0.3), 2),
    ("SG", "Cloudflare Radar", (1, 4), (0.1, 0.2), 1),
    ("KR", "IODA", (1, 5), (0.1, 0.3), 2),
    ("JP", "RIPE Atlas", (1, 6), (0.1, 0.3), 2),
    ("LK", "IODA", (1, 8), (0.1, 0.3), 2),
]

# Background cause_events with no matching outage — real GDACS/ACLED/
# #KeepItOn feeds surface far more raw records than end up correlated with
# a detected outage; this keeps the "Data Pipeline & Sources" counts honest.
NOISE_CAUSE_EVENTS = [
    ("NE", "conflict", "coup_aftermath", 3),
    ("GA", "shutdown", "coup_order", 2),
    ("IQ", "shutdown", "exam_order", 2),
    ("NG", "conflict", "militant_activity", 2),
    ("KE", "disaster", "flood", 2),
    ("CO", "conflict", "militant_activity", 2),
    ("TJ", "shutdown", "protest_response", 1),
    ("LA", "disaster", "flood", 1),
]


def _jitter(value: float, spread: float = 1.2) -> float:
    return value + random.uniform(-spread, spread)


def generate(lookback_days: int = 90) -> tuple[list[dict], list[dict]]:
    """Returns (outage_events, cause_events) — see module docstring."""
    random.seed(RNG_SEED)
    now = datetime.now(timezone.utc)
    outages: list[dict] = []
    cause_events: list[dict] = []

    for country_code, cause, subtype, base_source, dur_range, sev_range, occurrences in HOTSPOTS:
        geo_hit = geo.lookup(country_code)
        if not geo_hit:
            continue
        country_name, base_lat, base_lon = geo_hit

        for i in range(occurrences):
            start = now - timedelta(days=random.uniform(0, lookback_days))
            duration_hours = round(random.uniform(*dur_range), 1)
            end = start + timedelta(hours=duration_hours)
            severity = round(random.uniform(*sev_range), 2)

            outages.append(
                {
                    "event_id": f"seed:outage:{country_code}:{cause}:{i}:{int(start.timestamp())}",
                    "lat": round(_jitter(base_lat), 3),
                    "lon": round(_jitter(base_lon), 3),
                    "region_name": country_name,
                    "country": country_name,
                    "asn": None,
                    "timestamp_start": start.isoformat(),
                    "timestamp_end": end.isoformat() if end <= now else None,
                    "duration_hours": duration_hours if end <= now else None,
                    "cause": "unexplained",  # left for attribution.run() to resolve, same as real Phase 1
                    "cause_subtype": None,
                    "source_type": "structured",
                    "source_name": f"{base_source} (seed)",
                    "confidence": "low",
                    "severity_score": severity,
                }
            )

            if random.random() < CAUSE_EVENT_MATCH_PROBABILITY:
                offset_hours = random.uniform(1, 70) * random.choice((1, -1))
                cause_dt = start + timedelta(hours=offset_hours)
                source = CAUSE_SOURCE[cause]
                cause_events.append(
                    {
                        "cause_event_id": f"seed:{source}:{country_code}:{cause}:{i}",
                        "source": source,
                        "cause": cause,
                        "cause_subtype": subtype,
                        "country": country_name,
                        "lat": round(_jitter(base_lat), 3),
                        "lon": round(_jitter(base_lon), 3),
                        "event_date": cause_dt.isoformat(),
                        "title": f"Seed {source.upper()} record ({subtype.replace('_', ' ')}) — {country_name}",
                        "raw": {"seed": True},
                    }
                )
            # else: no cause_event generated at all — this outage should
            # genuinely remain unexplained after the real attribution join.

    for country_code, base_source, dur_range, sev_range, occurrences in UNEXPLAINED_ONLY:
        geo_hit = geo.lookup(country_code)
        if not geo_hit:
            continue
        country_name, base_lat, base_lon = geo_hit
        for i in range(occurrences):
            start = now - timedelta(days=random.uniform(0, lookback_days))
            duration_hours = round(random.uniform(*dur_range), 1)
            end = start + timedelta(hours=duration_hours)
            outages.append(
                {
                    "event_id": f"seed:outage:{country_code}:unexplained:{i}:{int(start.timestamp())}",
                    "lat": round(_jitter(base_lat), 3),
                    "lon": round(_jitter(base_lon), 3),
                    "region_name": country_name,
                    "country": country_name,
                    "asn": None,
                    "timestamp_start": start.isoformat(),
                    "timestamp_end": end.isoformat() if end <= now else None,
                    "duration_hours": duration_hours if end <= now else None,
                    "cause": "unexplained",
                    "cause_subtype": None,
                    "source_type": "structured",
                    "source_name": f"{base_source} (seed)",
                    "confidence": "low",
                    "severity_score": round(random.uniform(*sev_range), 2),
                }
            )

    for country_code, cause, subtype, count in NOISE_CAUSE_EVENTS:
        geo_hit = geo.lookup(country_code)
        if not geo_hit:
            continue
        country_name, base_lat, base_lon = geo_hit
        source = CAUSE_SOURCE[cause]
        for i in range(count):
            event_dt = now - timedelta(days=random.uniform(0, lookback_days))
            cause_events.append(
                {
                    "cause_event_id": f"seed:noise:{source}:{country_code}:{i}",
                    "source": source,
                    "cause": cause,
                    "cause_subtype": subtype,
                    "country": country_name,
                    "lat": round(_jitter(base_lat), 3),
                    "lon": round(_jitter(base_lon), 3),
                    "event_date": event_dt.isoformat(),
                    "title": f"Seed {source.upper()} record ({subtype.replace('_', ' ')}) — {country_name}",
                    "raw": {"seed": True, "noise": True},
                }
            )

    return outages, cause_events


SEMANTIC_CAUSE_SUBTYPE = {
    "conflict": "reported_unrest",
    "disaster": "reported_weather_event",
    "shutdown": "reported_order",
}


def apply_demo_semantic_layer(conn: sqlite3.Connection) -> dict[str, int]:
    """Demo stand-in for src/semantic.py's Phase 3 — mimics its *output shape*
    for a subset of outages still unexplained after the real attribution
    join, without calling Serper/Groq (no network in this sandbox). Real
    runs use src/semantic.py instead; this is never invoked outside `demo`.
    """
    random.seed(RNG_SEED + 1)
    remaining = db.fetch_unexplained_events(conn)
    remaining = sorted(remaining, key=lambda r: r["severity_score"] or 0, reverse=True)
    n_resolve = int(len(remaining) * SEMANTIC_RESOLVE_SHARE)
    to_resolve = remaining[:n_resolve]

    updates = []
    for event in to_resolve:
        cause = random.choice(("conflict", "disaster", "shutdown"))
        outlets = random.sample(
            ["Reuters", "AP", "AFP", "Al Jazeera", "BBC", "local press"], k=random.randint(2, 3)
        )
        updates.append(
            {
                "event_id": event["event_id"],
                "lat": event["lat"],
                "lon": event["lon"],
                "region_name": event["region_name"],
                "country": event["country"],
                "asn": event["asn"],
                "timestamp_start": event["timestamp_start"],
                "timestamp_end": event["timestamp_end"],
                "duration_hours": event["duration_hours"],
                "cause": cause,
                "cause_subtype": SEMANTIC_CAUSE_SUBTYPE[cause],
                "source_type": "semantic",
                "source_name": f"Serper+Groq (seed; {', '.join(outlets)})",
                "confidence": random.choice(("low", "medium")),
                "severity_score": event["severity_score"],
            }
        )
    if updates:
        db.upsert_events(conn, updates)
    return {"resolved": len(updates), "left_unexplained": len(remaining) - len(updates)}
