"""Synthetic seed data — NOT live source data.

This sandbox's network egress is locked down, so the real connectors in
src/ingestion/ can't be exercised end-to-end here. This module generates a
clearly-labeled, reproducible (seeded RNG) synthetic dataset shaped exactly
like real pipeline output, scattered across countries with well-known
conflict, disaster, or shutdown *patterns* (not specific real incidents —
dates/durations/severities are randomized), so the rest of the pipeline
(attribution stats, dashboard, insight panels) can be built and verified
against realistic-looking data.

Every record from this module carries `source_name` ending in "(seed)" and
`source_type`/`confidence` consistent with the category, so the dashboard
can flag it distinctly from real structured/semantic data. Swap this out by
running `python main.py fetch --attribute --semantic` with real network
access and API keys (see README) — real runs never touch this module.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from . import geo

RNG_SEED = 20260824  # fixed for reproducible demo runs

# (country_code, cause, cause_subtype, source_name, base_source_type,
#  duration_hours_range, severity_range, confidence_choices, occurrences)
SCENARIOS = [
    # -- conflict: active-conflict regions, sustained multi-day blackouts --
    ("SD", "conflict", "shelling", "IODA", (18, 240), (0.6, 1.0), ("high", "medium"), 6),
    ("UA", "conflict", "infrastructure_strike", "IODA", (2, 48), (0.4, 0.9), ("high", "medium"), 8),
    ("YE", "conflict", "airstrike", "Cloudflare Radar", (6, 96), (0.5, 0.9), ("medium", "high"), 5),
    ("SY", "conflict", "shelling", "IODA", (4, 72), (0.4, 0.8), ("medium", "high"), 5),
    ("ML", "conflict", "militant_activity", "IODA", (8, 120), (0.4, 0.8), ("medium", "low"), 4),
    ("SO", "conflict", "militant_activity", "RIPE Atlas", (4, 60), (0.3, 0.7), ("medium", "low"), 4),
    ("ET", "conflict", "regional_conflict", "IODA", (12, 168), (0.4, 0.8), ("medium", "high"), 4),
    ("CD", "conflict", "militia_clashes", "IODA", (6, 96), (0.3, 0.7), ("medium", "low"), 4),
    ("BF", "conflict", "militant_activity", "RIPE Atlas", (4, 72), (0.3, 0.7), ("low", "medium"), 3),
    # -- disaster: natural-hazard-prone regions, damage-recovery shaped --
    ("PH", "disaster", "tropical_cyclone", "IODA", (4, 120), (0.3, 0.8), ("high", "medium"), 6),
    ("TR", "disaster", "earthquake", "IODA", (2, 336), (0.5, 1.0), ("high", "medium"), 3),
    ("ID", "disaster", "flood", "Cloudflare Radar", (2, 72), (0.3, 0.7), ("medium", "high"), 5),
    ("BD", "disaster", "cyclone", "IODA", (3, 96), (0.3, 0.7), ("medium", "high"), 4),
    ("MZ", "disaster", "cyclone", "IODA", (6, 240), (0.4, 0.9), ("medium", "high"), 3),
    ("HT", "disaster", "hurricane", "Cloudflare Radar", (4, 168), (0.4, 0.8), ("medium", "high"), 3),
    ("NP", "disaster", "earthquake", "IODA", (2, 72), (0.2, 0.6), ("medium", "low"), 2),
    ("VN", "disaster", "typhoon", "IODA", (2, 60), (0.2, 0.6), ("medium", "high"), 4),
    ("MW", "disaster", "flood", "RIPE Atlas", (3, 96), (0.2, 0.6), ("low", "medium"), 3),
    ("CU", "disaster", "hurricane", "IODA", (6, 200), (0.4, 0.9), ("medium", "high"), 3),
    # -- shutdown: deliberate, sharp on/off, often short and border-aligned --
    ("IR", "shutdown", "protest_response", "IODA", (2, 48), (0.6, 1.0), ("high",), 5),
    ("MM", "shutdown", "junta_order", "IODA", (4, 120), (0.6, 1.0), ("high",), 5),
    ("PK", "shutdown", "election_order", "Cloudflare Radar", (1, 24), (0.5, 0.9), ("high", "medium"), 4),
    ("IN", "shutdown", "regional_order", "IODA", (4, 96), (0.5, 0.9), ("high", "medium"), 5),
    ("GN", "shutdown", "protest_response", "IODA", (2, 48), (0.5, 0.8), ("medium", "high"), 3),
    ("SN", "shutdown", "election_order", "Cloudflare Radar", (1, 36), (0.5, 0.8), ("high", "medium"), 3),
    ("CU", "shutdown", "protest_response", "IODA", (2, 24), (0.4, 0.8), ("medium", "high"), 2),
    ("BY", "shutdown", "election_order", "IODA", (1, 18), (0.5, 0.8), ("high",), 2),
    ("EG", "shutdown", "exam_order", "RIPE Atlas", (2, 12), (0.3, 0.6), ("medium",), 3),
    ("VE", "shutdown", "protest_response", "IODA", (2, 30), (0.4, 0.7), ("medium", "high"), 3),
    # -- unexplained: short technical blips, no matched cause event --
    ("BR", "unexplained", None, "RIPE Atlas", (1, 8), (0.1, 0.4), ("low",), 3),
    ("ZA", "unexplained", None, "Cloudflare Radar", (1, 10), (0.1, 0.4), ("low",), 3),
    ("MY", "unexplained", None, "IODA", (1, 6), (0.1, 0.3), ("low",), 2),
    ("KZ", "unexplained", None, "RIPE Atlas", (1, 12), (0.1, 0.4), ("low",), 2),
    ("NG", "unexplained", "submarine_cable", "IODA", (2, 30), (0.2, 0.5), ("low", "medium"), 3),
    ("MX", "unexplained", None, "Cloudflare Radar", (1, 8), (0.1, 0.3), ("low",), 2),
    # -- a few semantic-layer style records (lower confidence, news-derived) --
    ("IQ", "shutdown", "exam_order", "Serper+Groq", (2, 10), (0.3, 0.6), ("low", "medium"), 2),
    ("NE", "conflict", "coup_aftermath", "Serper+Groq", (4, 48), (0.3, 0.6), ("low", "medium"), 2),
    ("GA", "shutdown", "coup_order", "Serper+Groq", (2, 24), (0.3, 0.5), ("low",), 1),
]


def _jitter(value: float, spread: float = 1.2) -> float:
    return value + random.uniform(-spread, spread)


def generate(lookback_days: int = 90) -> list[dict]:
    random.seed(RNG_SEED)
    now = datetime.now(timezone.utc)
    events: list[dict] = []

    for country_code, cause, subtype, base_source, dur_range, sev_range, conf_choices, occurrences in SCENARIOS:
        geo_hit = geo.lookup(country_code)
        if not geo_hit:
            continue
        country_name, base_lat, base_lon = geo_hit

        for i in range(occurrences):
            offset_days = random.uniform(0, lookback_days)
            start = now - timedelta(days=offset_days)
            duration_hours = round(random.uniform(*dur_range), 1)
            end = start + timedelta(hours=duration_hours)
            severity = round(random.uniform(*sev_range), 2)
            confidence = random.choice(conf_choices)
            source_type = "semantic" if base_source == "Serper+Groq" else "structured"

            events.append(
                {
                    "event_id": f"seed:{country_code}:{cause}:{i}:{int(start.timestamp())}",
                    "lat": round(_jitter(base_lat), 3),
                    "lon": round(_jitter(base_lon), 3),
                    "region_name": country_name,
                    "country": country_name,
                    "asn": None,
                    "timestamp_start": start.isoformat(),
                    "timestamp_end": end.isoformat() if end <= now else None,
                    "duration_hours": duration_hours if end <= now else None,
                    "cause": cause,
                    "cause_subtype": subtype,
                    "source_type": source_type,
                    "source_name": f"{base_source} (seed)",
                    "confidence": confidence,
                    "severity_score": severity,
                }
            )
    return events
