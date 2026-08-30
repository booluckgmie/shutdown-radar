"""FAA NOTAM API — US airspace restriction notices, used as a Phase 2 cause
source alongside GDACS/ACLED/#KeepItOn: a Temporary Flight Restriction (TFR)
or security/defense-airspace NOTAM is itself a government-issued "restricted
here" signal, and correlates with conflict/shutdown attribution the same
way a verified shutdown record does.

Scope note — this is deliberately **US-only**: FAA is the United States'
Federal Aviation Administration, its NOTAM feed only ever covers US
airspace (plus a few oceanic/territorial FIRs). It is not a global
restricted-airspace source. For worldwide restricted-location coverage
you'd need a different feed per aviation authority (EASA for the EU, ICAO's
own aggregation, etc.) — out of scope here; #KeepItOn and ACLED remain the
global-coverage cause sources.

Only NOTAMs that actually look like a *restriction* (TFR, security,
military, defense-airspace, disaster-driven closures) become cause_events —
the routine kind (runway lighting outage, obstacle notices, GPS test
notices) are filtered out; see CONFLICT_KEYWORDS/DISASTER_KEYWORDS/
SHUTDOWN_KEYWORDS below. Free registration required at
https://developer.faa.gov (client_id/client_secret headers) — verify the
exact endpoint/response shape against current docs before relying on this
in production; unverified against live traffic in this project's authoring
sandbox (see README "A note on where this was built").
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://external-api.faa.gov/notamapi/v1/notams"
REQUEST_TIMEOUT = 20
RESULTS_PER_REGION = 50

# A handful of regional centroids for a coarse geo-search sweep of US
# airspace — the API takes a lat/lon/radius per call, not a single
# nationwide query, so this stands in for a real border polygon.
US_REGIONS = [
    ("Northeast", 40.7, -74.0),
    ("Southeast", 33.7, -84.4),
    ("Midwest", 41.9, -87.6),
    ("South Central", 32.8, -96.8),
    ("Mountain West", 39.7, -104.9),
    ("Pacific", 34.1, -118.2),
    ("Pacific Northwest", 47.6, -122.3),
    ("Alaska", 61.2, -149.9),
    ("Hawaii", 21.3, -157.9),
]
SEARCH_RADIUS_NM = 150

CONFLICT_KEYWORDS = ("military", "national defense", "defense airspace", "combat", "hostilities")
DISASTER_KEYWORDS = ("volcanic", "volcano", "hurricane", "wildfire", "flood", "earthquake", "disaster", "wx")
SHUTDOWN_KEYWORDS = (
    "security", "vip", "presidential", "national security", "prohibited",
    "temporary flight restriction", " tfr ", "special security",
)


def _classify(text: str) -> tuple[str, str] | None:
    lowered = f" {(text or '').lower()} "
    for kw in CONFLICT_KEYWORDS:
        if kw in lowered:
            return "conflict", kw.strip()
    for kw in DISASTER_KEYWORDS:
        if kw in lowered:
            return "disaster", kw.strip()
    for kw in SHUTDOWN_KEYWORDS:
        if kw in lowered:
            return "shutdown", "airspace_restriction"
    return None


def _fetch_region(lat: float, lon: float, headers: dict, session: requests.Session) -> list[dict]:
    params = {
        "locationLongitude": lon,
        "locationLatitude": lat,
        "locationRadius": SEARCH_RADIUS_NM,
        "pageSize": RESULTS_PER_REGION,
        "sortBy": "effectiveStartDate",
        "sortOrder": "Desc",
    }
    resp = session.get(BASE_URL, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("items", []) if isinstance(payload, dict) else []


def fetch(lookback_days: int, client_id: str, client_secret: str) -> list[dict]:
    if not (client_id and client_secret):
        logger.info("FAA NOTAM: no client_id/client_secret configured, skipping source")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    headers = {"client_id": client_id, "client_secret": client_secret}
    session = requests.Session()

    try:
        _fetch_region(*US_REGIONS[0][1:], headers, session)
    except requests.RequestException as exc:
        logger.warning("FAA NOTAM unreachable, skipping source: %s", exc)
        return []

    seen_ids: set[str] = set()
    cause_events = []
    for region_name, lat, lon in US_REGIONS:
        try:
            items = _fetch_region(lat, lon, headers, session)
        except requests.RequestException as exc:
            logger.debug("FAA NOTAM fetch failed for region %s: %s", region_name, exc)
            continue

        for item in items:
            props = item.get("properties", {}) if isinstance(item, dict) else {}
            core = props.get("coreNOTAMData", {}).get("notam", {}) if isinstance(props.get("coreNOTAMData"), dict) else {}
            notam_id = core.get("id") or core.get("number")
            if not notam_id or notam_id in seen_ids:
                continue

            text = core.get("text", "")
            classification = _classify(text)
            if not classification:
                continue
            cause, subtype = classification

            effective_start = core.get("effectiveStart")
            if not effective_start:
                continue
            try:
                event_dt = datetime.fromisoformat(effective_start.replace("Z", "+00:00"))
            except ValueError:
                continue
            if event_dt < cutoff:
                continue

            geometry = item.get("geometry", {}) if isinstance(item, dict) else {}
            coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
            item_lon, item_lat = (coords[0], coords[1]) if coords and len(coords) >= 2 else (lon, lat)

            seen_ids.add(notam_id)
            cause_events.append(
                {
                    "cause_event_id": f"notam:{notam_id}",
                    "source": "notam",
                    "cause": cause,
                    "cause_subtype": subtype,
                    "country": "United States",
                    "lat": item_lat,
                    "lon": item_lon,
                    "event_date": event_dt.isoformat(),
                    "title": (text or "")[:200],
                    "raw": {"location": core.get("location"), "classification": core.get("classification")},
                }
            )

    logger.info("FAA NOTAM: %d restriction-classified NOTAMs within lookback window", len(cause_events))
    return cause_events
