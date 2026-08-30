"""GDACS — Global Disaster Alert and Coordination System.

Free, no key. This module previously parsed the GeoRSS feed
(https://www.gdacs.org/xml/rss.xml) but that was found — via real GitHub
Actions run logs against live traffic — to silently return zero results on
every single run despite fetching and parsing without error: ElementTree's
namespace-aware lookup was matching zero fields against a guessed
`xmlns:gdacs` URI, and even after switching to namespace-agnostic local-tag
matching, the feed's actual item/date structure still didn't match what was
expected (429 "item"-like containers found, none with a parseable date —
never fully diagnosed, since this project's authoring sandbox cannot reach
gdacs.org to inspect the raw feed directly).

Switched to GDACS's JSON/GeoJSON REST API instead — research turned up two
independent, currently-active real-world consumers preferring it over RSS,
and JSON has no namespace-matching ambiguity to get wrong the way the RSS
extension elements did. **The exact request/response shape below is
inferred from indirect references, not confirmed against a live sample or
official schema** (same sandbox limitation) — if this also returns
unexpectedly empty, `_log_unexpected_shape` dumps a raw response snippet at
WARNING so the next real run's GitHub Actions logs show the actual JSON
shape directly, which is a fixable diagnostic (unlike the RSS case) since
this project's CI runner *does* have real internet access even though the
authoring sandbox doesn't.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

API_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
REQUEST_TIMEOUT = 20

EVENT_TYPE_SUBTYPE = {
    "EQ": "earthquake",
    "TC": "tropical_cyclone",
    "FL": "flood",
    "VO": "volcano",
    "DR": "drought",
    "WF": "wildfire",
    "TS": "tsunami",
}


def _log_unexpected_shape(context: str, payload) -> None:
    logger.warning("GDACS: unexpected response shape (%s) — raw body: %s", context, str(payload)[:1500])


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch(lookback_days: int) -> list[dict]:
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=lookback_days)
    params = {
        "fromDate": since.strftime("%Y-%m-%d"),
        "toDate": until.strftime("%Y-%m-%d"),
        "alertlevel": "Green;Orange;Red",
        "limit": 500,
    }

    try:
        resp = requests.get(API_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("GDACS unreachable, skipping source: %s", exc)
        return []

    try:
        payload = resp.json()
    except ValueError:
        logger.warning("GDACS returned non-JSON response, skipping source. Body: %s", resp.text[:1500])
        return []

    features = payload.get("features") if isinstance(payload, dict) else payload
    if not isinstance(features, list):
        _log_unexpected_shape("no 'features' list", payload)
        return []
    if not features:
        # A genuinely quiet window is plausible but uncommon for a global
        # feed; log the raw (likely small/empty) body so a real run's logs
        # can distinguish "truly zero this week" from "wrong param names".
        logger.info("GDACS: 0 features returned — raw body: %s", str(payload)[:800])
        return []

    cause_events = []
    skipped_no_date, skipped_old, skipped_bad_shape = 0, 0, 0
    for feature in features:
        if not isinstance(feature, dict):
            skipped_bad_shape += 1
            continue
        props = feature.get("properties", {}) if isinstance(feature.get("properties"), dict) else {}
        geometry = feature.get("geometry", {}) if isinstance(feature.get("geometry"), dict) else {}
        coords = geometry.get("coordinates")

        event_date = _parse_date(props.get("fromdate") or props.get("fromDate") or props.get("pubdate"))
        if not event_date:
            skipped_no_date += 1
            continue
        if event_date < since:
            skipped_old += 1
            continue

        lat, lon = (coords[1], coords[0]) if isinstance(coords, list) and len(coords) >= 2 else (None, None)
        event_type = props.get("eventtype") or props.get("eventType")
        country = props.get("country") or props.get("iso3")
        event_id = props.get("eventid") or props.get("eventId") or f"{event_type}:{event_date.isoformat()}:{country}"

        cause_events.append(
            {
                "cause_event_id": f"gdacs:{event_id}",
                "source": "gdacs",
                "cause": "disaster",
                "cause_subtype": EVENT_TYPE_SUBTYPE.get(event_type, event_type),
                "country": country,
                "lat": lat,
                "lon": lon,
                "event_date": event_date.isoformat(),
                "title": props.get("eventname") or props.get("name"),
                "raw": {"eventtype": event_type, "country": country, "alertlevel": props.get("alertlevel")},
            }
        )

    logger.info(
        "GDACS: %d active disaster alerts within lookback window (%d features seen, "
        "%d skipped: no parseable date, %d skipped: older than lookback, %d skipped: bad shape)",
        len(cause_events), len(features), skipped_no_date, skipped_old, skipped_bad_shape,
    )
    return cause_events
