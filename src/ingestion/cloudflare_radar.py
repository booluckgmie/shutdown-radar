"""Cloudflare Radar — outage annotations (traffic anomalies with root-cause tags).

Free tier, requires an API token (CLOUDFLARE_API_TOKEN). See
https://developers.cloudflare.com/radar/investigate/outage-annotations/ and
https://developers.cloudflare.com/api/ (verify current field names — Radar's
annotation schema has changed shape before; this parser is defensive about
key names for that reason).

Cloudflare's own annotations sometimes already carry a root-cause tag
(e.g. "cable cut", "government directive", "power outage"). When present we
map it straight to our cause taxonomy at 'medium' confidence — a real vendor
signal, but not cross-validated against GDACS/ACLED/#KeepItOn the way
Phase 2 attribution does for IODA's cause-less events.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

from .. import geo

logger = logging.getLogger(__name__)

BASE_URL = "https://api.cloudflare.com/client/v4/radar/annotations/outages"
REQUEST_TIMEOUT = 20

CAUSE_KEYWORDS = {
    "conflict": ("war", "military", "conflict", "attack", "shelling"),
    "shutdown": ("government", "directive", "order", "censorship", "intentional", "shutdown"),
    "disaster": ("earthquake", "flood", "storm", "cyclone", "hurricane", "cable cut", "weather", "fire", "power"),
}


def _guess_cause(text: str) -> tuple[str, str | None]:
    lowered = (text or "").lower()
    for cause, keywords in CAUSE_KEYWORDS.items():
        for kw in keywords:
            if kw in lowered:
                return cause, kw
    return "unexplained", None


def _normalize(raw: dict) -> dict | None:
    locations = raw.get("locationsDetails") or raw.get("locations") or []
    if isinstance(locations, list) and locations and isinstance(locations[0], dict):
        code = locations[0].get("code")
    elif isinstance(locations, list) and locations:
        code = locations[0]
    else:
        code = None
    geo_hit = geo.lookup(code) if code else None
    if not geo_hit:
        return None
    country_name, lat, lon = geo_hit

    start = raw.get("startDate") or raw.get("startTime")
    end = raw.get("endDate") or raw.get("endTime")
    duration_hours = None
    try:
        if start and end:
            t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(end.replace("Z", "+00:00"))
            duration_hours = round((t1 - t0).total_seconds() / 3600, 2)
    except (ValueError, AttributeError):
        pass

    description = raw.get("description") or raw.get("outage", {}).get("outageCause", "") if isinstance(raw.get("outage"), dict) else raw.get("description", "")
    cause, subtype = _guess_cause(description)

    asns = raw.get("asns") or []
    asn = str(asns[0]) if asns else None

    return {
        "event_id": f"cloudflare:{raw.get('id') or code + str(start)}",
        "lat": lat,
        "lon": lon,
        "region_name": country_name,
        "country": country_name,
        "asn": asn,
        "timestamp_start": start or datetime.now(timezone.utc).isoformat(),
        "timestamp_end": end,
        "duration_hours": duration_hours,
        "cause": cause,
        "cause_subtype": subtype,
        "source_type": "structured",
        "source_name": "Cloudflare Radar",
        "confidence": "medium" if cause != "unexplained" else "low",
        "severity_score": 0.6,
    }


def fetch(lookback_days: int, api_token: str) -> list[dict]:
    if not api_token:
        logger.info("Cloudflare Radar: no API token configured, skipping source")
        return []

    date_end = datetime.now(timezone.utc)
    date_start = date_end - timedelta(days=lookback_days)
    headers = {"Authorization": f"Bearer {api_token}"}
    params = {
        "dateStart": date_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dateEnd": date_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 500,
    }
    try:
        resp = requests.get(BASE_URL, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        logger.warning("Cloudflare Radar unreachable, skipping source: %s", exc)
        return []

    annotations = payload.get("result", {}).get("annotations", []) if isinstance(payload, dict) else []
    events = []
    for raw in annotations:
        normalized = _normalize(raw)
        if normalized:
            events.append(normalized)
    logger.info("Cloudflare Radar: %d outage annotations", len(events))
    return events
