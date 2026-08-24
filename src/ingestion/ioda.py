"""IODA (Georgia Tech / CAIDA) — Internet Outage Detection and Analysis.

Free public API, no key required. Base URL and endpoints per
https://github.com/CAIDA/ioda-api/wiki/API-Specification (verify against
current docs before relying on this in production — free-tier API shapes
drift over time, per the project's design principles).

We treat IODA as the Phase 1 base signal: it tells us *where* and *when*
connectivity dropped, with no cause attached yet (cause is resolved in
Phase 2's attribution join). Every event this module emits starts out
`cause="unexplained", confidence="low"`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from .. import geo

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ioda.caida.org/dev"
REQUEST_TIMEOUT = 20
# IODA alert "level" values that represent a real, ongoing signal loss
# (as opposed to "normal"/"warning" recovery noise).
SIGNIFICANT_LEVELS = {"critical", "warning"}


def _iso(unix_ts: float | int | None) -> str | None:
    if unix_ts is None:
        return None
    return datetime.fromtimestamp(int(unix_ts), tz=timezone.utc).isoformat()


def _normalize(raw: dict, entity_code: str) -> dict | None:
    entity = raw.get("entity", {}) if isinstance(raw.get("entity"), dict) else {}
    code = (entity.get("code") or entity_code or "").upper()
    geo_hit = geo.lookup(code)
    if not geo_hit:
        return None
    country_name, lat, lon = geo_hit

    start = raw.get("from") or raw.get("start") or raw.get("time")
    end = raw.get("until") or raw.get("end")
    duration_hours = None
    if start and end:
        duration_hours = round((float(end) - float(start)) / 3600, 2)

    # IODA scores/values vary by datasource; normalize defensively into 0-1.
    value = raw.get("value")
    history = raw.get("historyValue") or raw.get("history_value")
    severity = 0.5
    try:
        if value is not None and history:
            drop_ratio = 1 - (float(value) / float(history))
            severity = max(0.0, min(1.0, drop_ratio))
    except (TypeError, ZeroDivisionError):
        pass

    event_id = f"ioda:{code}:{raw.get('id') or start}"
    return {
        "event_id": event_id,
        "lat": lat,
        "lon": lon,
        "region_name": entity.get("name") or country_name,
        "country": country_name,
        "asn": code if entity.get("type") == "asn" else None,
        "timestamp_start": _iso(start) or datetime.now(timezone.utc).isoformat(),
        "timestamp_end": _iso(end),
        "duration_hours": duration_hours,
        "cause": "unexplained",
        "cause_subtype": None,
        "source_type": "structured",
        "source_name": f"IODA ({raw.get('datasource', 'unknown')})",
        "confidence": "low",
        "severity_score": severity,
        "_country_code": code,
    }


def fetch_alerts_for_country(code: str, since_unix: int, until_unix: int, session: requests.Session) -> list[dict]:
    url = f"{BASE_URL}/outages/alerts/country/{code}"
    resp = session.get(url, params={"from": since_unix, "until": until_unix}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    raw_alerts = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_alerts, list):
        return []
    events = []
    for raw in raw_alerts:
        level = str(raw.get("level", "")).lower()
        if level and level not in SIGNIFICANT_LEVELS:
            continue
        normalized = _normalize(raw, code)
        if normalized:
            events.append(normalized)
    return events


def fetch(lookback_days: int, country_codes: list[str] | None = None) -> list[dict]:
    """Fetch IODA outage alerts for the rolling window, one country at a time.

    Returns [] (fail-soft) if IODA is unreachable at all — callers should not
    treat that as fatal, other sources still populate the map.
    """
    until_unix = int(datetime.now(timezone.utc).timestamp())
    since_unix = until_unix - lookback_days * 86400
    codes = country_codes or sorted(geo.COUNTRIES.keys())

    session = requests.Session()
    # Connectivity probe: if the very first call fails, don't burn time/
    # timeouts looping over ~190 countries.
    try:
        fetch_alerts_for_country(codes[0], since_unix, until_unix, session)
    except requests.RequestException as exc:
        logger.warning("IODA unreachable, skipping source: %s", exc)
        return []

    events: list[dict] = []
    for code in codes:
        try:
            events.extend(fetch_alerts_for_country(code, since_unix, until_unix, session))
        except requests.RequestException as exc:
            logger.debug("IODA fetch failed for %s: %s", code, exc)
            continue
    logger.info("IODA: %d alert events across %d countries", len(events), len(codes))
    return events
