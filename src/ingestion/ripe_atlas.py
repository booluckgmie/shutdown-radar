"""RIPE Atlas — probe-level connectivity loss as a fine-grained outage signal.

Free, no key, REST API v2: https://atlas.ripe.net/docs/apis/rest-api-manual/
RIPE Atlas does not expose a pre-aggregated "outage events" endpoint the way
IODA does. What it does expose is the live status of every probe in the
mesh (`/api/v2/probes/?status=2` = currently disconnected), each carrying a
country code, lat/lon, and `status_since`. We aggregate disconnected probes
by country + day into outage events — a real, if noisier, ISP/city-level
signal that complements IODA's country/ASN-level view (per the source
table: RIPE Atlas is the "City / ISP-level" granularity source).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

from .. import geo

logger = logging.getLogger(__name__)

BASE_URL = "https://atlas.ripe.net/api/v2/probes/"
REQUEST_TIMEOUT = 20
PAGE_SIZE = 500
MAX_PAGES = 20  # safety cap: up to 10k disconnected probes


def _fetch_disconnected_probes(session: requests.Session) -> list[dict]:
    probes: list[dict] = []
    url = BASE_URL
    params = {"status": 2, "format": "json", "page_size": PAGE_SIZE}
    for _ in range(MAX_PAGES):
        resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        probes.extend(payload.get("results", []))
        next_url = payload.get("next")
        if not next_url:
            break
        url, params = next_url, None
    return probes


def fetch(lookback_days: int) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    session = requests.Session()
    try:
        probes = _fetch_disconnected_probes(session)
    except requests.RequestException as exc:
        logger.warning("RIPE Atlas unreachable, skipping source: %s", exc)
        return []

    # Aggregate disconnected probes -> (country, day) buckets.
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for probe in probes:
        country_code = probe.get("country_code")
        status_since = probe.get("status_since")
        if not country_code or not status_since:
            continue
        try:
            since_dt = datetime.fromtimestamp(int(status_since), tz=timezone.utc)
        except (TypeError, ValueError):
            continue
        if since_dt < since:
            continue
        day_key = since_dt.strftime("%Y-%m-%d")
        buckets[(country_code.upper(), day_key)].append(probe)

    events = []
    for (code, day), bucket_probes in buckets.items():
        geo_hit = geo.lookup(code)
        if not geo_hit:
            continue
        country_name, lat, lon = geo_hit
        count = len(bucket_probes)
        # A handful of disconnected home/office probes is normal churn;
        # treat a same-day cluster as signal.
        if count < 2:
            continue
        severity = max(0.0, min(1.0, count / 20))
        events.append(
            {
                "event_id": f"ripe:{code}:{day}",
                "lat": lat,
                "lon": lon,
                "region_name": country_name,
                "country": country_name,
                "asn": None,
                "timestamp_start": f"{day}T00:00:00+00:00",
                "timestamp_end": None,
                "duration_hours": None,
                "cause": "unexplained",
                "cause_subtype": None,
                "source_type": "structured",
                "source_name": "RIPE Atlas",
                "confidence": "low",
                "severity_score": severity,
                "_probe_count": count,
            }
        )
    logger.info("RIPE Atlas: %d country-day disconnect clusters", len(events))
    return events
