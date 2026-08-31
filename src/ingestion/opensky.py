"""OpenSky Network — global ADS-B flight tracking, used here as an aviation-
disruption proxy: a sharp drop in how many aircraft *registered to* a given
country are airborne anywhere in the world right now, relative to that
country's own recent baseline, is treated as a flight-disruption event —
grounded fleets, a closed hub airport, sanctions-driven flight bans,
war-zone no-fly restrictions, and so on.

Important scope note: this buckets by OpenSky's `origin_country` field,
which is the aircraft's *registration* country (inferred from its ICAO
24-bit address) — not the country it's currently flying over. OpenSky's
public API has no "currently over this country" field, and this project
has no border-polygon data to compute one from raw lat/lon, only country
centroids (src/geo.py). So this is "how many of Sudan's registered aircraft
are airborne right now", not "how much air traffic is over Sudan" — a real
but different signal from the one a reader might assume at a glance.

Free, works anonymously at a lower rate limit; an optional free OAuth2
client_id/client_secret (register at https://opensky-network.org/apidoc/)
raises it. There's no baseline on the very first run ever against a fresh
database — nothing gets flagged until enough prior runs are on record (see
MIN_BASELINE_SAMPLES), by design: a single snapshot can't tell you whether
today is anomalous.

OpenSky's `origin_country` strings don't always match this project's
canonical display names (e.g. "Russia" vs "Russian Federation") —
geo.resolve_name() handles that normalization, shared with gdacs.py which
hit the identical mismatch class against real GDACS data (confirmed by
directly comparing country strings across both sources after a live run;
see geo.py's EXTERNAL_NAME_ALIASES).
"""
from __future__ import annotations

import logging
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

import requests

from .. import db, geo

logger = logging.getLogger(__name__)

STATES_URL = "https://opensky-network.org/api/states/all"
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
REQUEST_TIMEOUT = 30

BASELINE_LOOKBACK_DAYS = 14
MIN_BASELINE_SAMPLES = 3    # need this many prior runs on record before flagging anything
DROP_THRESHOLD = 0.4        # flag when today's count is <= (1 - 0.4) = 60% of the trailing median
MIN_BASELINE_COUNT = 15     # ignore countries with too few aircraft for the ratio to mean anything

# Country-name normalization (OpenSky's origin_country doesn't always match
# our canonical names) now lives in geo.resolve_name() — shared with
# gdacs.py, which hit the identical problem.


def _access_token(client_id: str, client_secret: str) -> str | None:
    try:
        resp = requests.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except requests.RequestException as exc:
        logger.warning("OpenSky auth failed, falling back to anonymous access: %s", exc)
        return None


def _fetch_states(client_id: str, client_secret: str) -> list[list]:
    headers = {}
    if client_id and client_secret:
        token = _access_token(client_id, client_secret)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    resp = requests.get(STATES_URL, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("states") or []


def fetch(conn: sqlite3.Connection, client_id: str = "", client_secret: str = "") -> list[dict]:
    """Records this run's per-country airborne counts to `flight_activity`,
    compares against the trailing baseline already on record, and returns
    Event dicts for countries whose count just dropped sharply. Reading and
    writing the baseline happen together here (not split into a separate
    main.py step) since they're two sides of one measurement.
    """
    try:
        states = _fetch_states(client_id, client_secret)
    except requests.RequestException as exc:
        logger.warning("OpenSky unreachable, skipping source: %s", exc)
        return []

    now = datetime.now(timezone.utc)
    counts: dict[str, int] = {}
    for s in states:
        try:
            origin_country, on_ground = s[2], s[8]
        except (IndexError, TypeError):
            continue
        if on_ground or not origin_country:
            continue
        counts[origin_country] = counts.get(origin_country, 0) + 1

    since = (now - timedelta(days=BASELINE_LOOKBACK_DAYS)).isoformat()
    baseline = db.fetch_flight_baseline(conn, since)

    events = []
    for origin_country, today_count in counts.items():
        history = baseline.get(origin_country, [])
        if len(history) < MIN_BASELINE_SAMPLES:
            continue
        median = statistics.median(history)
        if median < MIN_BASELINE_COUNT or today_count > median * (1 - DROP_THRESHOLD):
            continue

        geo_hit = geo.resolve_name(origin_country)
        if not geo_hit:
            logger.debug("OpenSky: no country match for origin_country=%r, skipping", origin_country)
            continue
        country_name, lat, lon = geo_hit
        drop_ratio = 1 - (today_count / median)
        events.append(
            {
                "event_id": f"opensky:{country_name}:{now.strftime('%Y-%m-%d')}",
                "lat": lat,
                "lon": lon,
                "region_name": country_name,
                "country": country_name,
                "asn": None,
                "timestamp_start": now.isoformat(),
                "timestamp_end": None,
                "duration_hours": None,
                "cause": "unexplained",
                "cause_subtype": None,
                "source_type": "structured",
                "source_name": "OpenSky Network",
                "confidence": "low",
                "severity_score": max(0.0, min(1.0, drop_ratio)),
            }
        )

    db.record_flight_activity(conn, counts, now.isoformat())
    logger.info("OpenSky: %d countries tracked, %d flagged as anomalous airborne-count drops", len(counts), len(events))
    return events
