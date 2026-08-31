"""IODA (Georgia Tech / CAIDA) — Internet Outage Detection and Analysis.

Free public API, no key required. IODA moved its hosting from CAIDA/UCSD to
Georgia Tech's Internet Intelligence Lab in August 2021 (the live frontend
is https://ioda.inetintel.cc.gatech.edu). Two prior guesses at the API host
were confirmed dead/wrong by inspecting real GitHub Actions run logs
against live traffic; the current CANDIDATE_BASE_URLS[0] +
ALERTS_PATH/query-param shape below is corroborated by two independent,
actively-used third-party projects that call it successfully as of this
writing (ianlkl11234s/gis-data-collectors, PeterIbarra/dashboard-ven-monitor-app)
plus InetIntel's own uptime monitor reporting 200 OK on this exact URL —
notably the v2 API dropped the old CAIDA-era path-segmented endpoint shape
(`/outages/alerts/{entityType}/{entityCode}`) for query parameters
(`?entityType=...&entityCode=...`) entirely, which is almost certainly why
earlier guesses at the new host still failed even with the right host.
Response field names (`data[]`, `entityType`, `entityCode`, `entityName`)
are inferred from those same projects, not confirmed against an official
schema — _normalize() logs a raw response snippet at DEBUG if the shape
looks unexpected, so a future run's logs can pinpoint exactly what's wrong
if this guess is still off. CANDIDATE_BASE_URLS keeps the legacy CAIDA host
as a fallback in case this one also stops answering.

We treat IODA as the Phase 1 base signal: it tells us *where* and *when*
connectivity dropped, with no cause attached yet (cause is resolved in
Phase 2's attribution join). Every event this module emits starts out
`cause="unexplained", confidence="low"`.

Confirmed via a real alert logged from a live GitHub Actions run:
`{'datasource': 'bgp', 'entity': {...}, 'time': 1787092200, 'level':
'critical', 'condition': '< 0.99', 'value': 638, 'historyValue': 646,
'method': 'median'}` — the alerts endpoint reports a single point-in-time
threshold crossing, not an open/close interval, so `timestamp_end` and
`duration_hours` are always None for IODA events; there's no "resolved"
concept in this data to recover, by design of the upstream API, not a
parsing bug. The dashboard's recovery-time panel only reflects sources
that do report a real resolution time (e.g. Cloudflare Radar's outage
annotations, currently unconfigured — see README).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from .. import geo

logger = logging.getLogger(__name__)

CANDIDATE_BASE_URLS = [
    "https://api.ioda.inetintel.cc.gatech.edu/v2",
    "https://api.ioda.caida.org/dev",  # legacy host, kept as a last-resort fallback
]
ALERTS_PATH = "/outages/alerts"
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

    # Confirmed against a real raw alert via a live GitHub Actions run's
    # logs: {'datasource': 'bgp', 'entity': {...}, 'time': 1787092200,
    # 'level': 'critical', 'condition': '< 0.99', 'value': 638,
    # 'historyValue': 646, 'method': 'median'} -- IODA's alerts endpoint
    # reports a single point-in-time threshold crossing, never an
    # open/close interval. There is no "from"/"until"/"end" field to find
    # under a different name; this source genuinely cannot report a
    # resolution time, so timestamp_end/duration_hours stay unset for
    # every IODA event by design, not by a wrong field-name guess.
    start = raw.get("time")
    timestamp_end = None
    duration_hours = None

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
        "timestamp_end": timestamp_end,
        "duration_hours": duration_hours,
        "cause": "unexplained",
        "cause_subtype": None,
        "source_type": "structured",
        "source_name": f"IODA ({raw.get('datasource', 'unknown')})",
        "confidence": "low",
        "severity_score": severity,
        "_country_code": code,
    }


def fetch_alerts_for_country(
    code: str, since_unix: int, until_unix: int, session: requests.Session, base_url: str
) -> list[dict]:
    url = f"{base_url}{ALERTS_PATH}"
    params = {"entityType": "country", "entityCode": code, "from": since_unix, "until": until_unix}
    resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    raw_alerts = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_alerts, list):
        logger.debug("IODA: unexpected response shape for %s, raw body: %s", code, str(payload)[:500])
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
    # Connectivity probe: try each candidate base URL against just the
    # first country before committing to a full ~190-country loop against
    # a host that might be dead (see CANDIDATE_BASE_URLS above).
    base_url = None
    for candidate in CANDIDATE_BASE_URLS:
        try:
            fetch_alerts_for_country(codes[0], since_unix, until_unix, session, candidate)
            base_url = candidate
            break
        except requests.RequestException as exc:
            logger.debug("IODA candidate base URL %s failed: %s", candidate, exc)
            continue

    if base_url is None:
        logger.warning("IODA unreachable on all candidate base URLs (%s), skipping source", CANDIDATE_BASE_URLS)
        return []
    logger.info("IODA: using base URL %s", base_url)

    events: list[dict] = []
    for code in codes:
        try:
            events.extend(fetch_alerts_for_country(code, since_unix, until_unix, session, base_url))
        except requests.RequestException as exc:
            logger.debug("IODA fetch failed for %s: %s", code, exc)
            continue
    logger.info("IODA: %d alert events across %d countries", len(events), len(codes))
    return events
