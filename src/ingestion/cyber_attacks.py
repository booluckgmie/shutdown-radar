"""Cloudflare Radar — notable DDoS attacks, a Phase 2 "cyber" cause source.

Reuses CLOUDFLARE_API_TOKEN (the same free-tier token as
src/ingestion/cloudflare_radar.py's outage-annotation feed) — no new
credential needed. Radar's "top attacks" endpoints return the most notable
DDoS attacks (layer 3/network and layer 7/application) in a window; each
one with a resolvable target country becomes a cause_event dated to the
attack's actual start time — a real timestamp, unlike
gov_shutdown_scraper's fetch-time stamping, so this source isn't
confidence-capped in attribution.py the way that one is.

Response-shape hedge: Cloudflare's exact JSON field names for these
endpoints weren't verified against a live call while building this (no
token available in the sandbox this was authored in — same situation as
src/site_analytics.py). _first() below extracts generically across a list
of plausible key-name candidates per field rather than assuming one exact
schema; any attack record that doesn't yield both a date and a resolvable
country is skipped, and an unrecognized top-level response shape just
skips that layer, rather than guessing wrong or crashing the run.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .. import geo

logger = logging.getLogger(__name__)

BASE_URL = "https://api.cloudflare.com/client/v4/radar/attacks"
REQUEST_TIMEOUT = 20
LAYERS = (
    ("layer3", "L3/network-layer DDoS"),
    ("layer7", "L7/application-layer DDoS"),
)


def _first(raw: dict, *keys: str) -> Any:
    for k in keys:
        v = raw.get(k)
        if v is None:
            continue
        if isinstance(v, list):
            if v:
                return v[0]
            continue
        return v
    return None


def _normalize(raw: dict, layer: str, label: str) -> dict | None:
    start = _first(raw, "startDate", "dateStart", "date_start", "start")
    if not start:
        return None

    target_code = _first(raw, "targetCountryAlpha2", "target_country_alpha2", "targetCountry", "target")
    target_name = _first(raw, "targetCountryName", "target_country_name")
    geo_hit = (geo.lookup(target_code) if target_code else None) or (
        geo.resolve_name(target_name) if target_name else None
    )
    if not geo_hit:
        return None
    country_name, lat, lon = geo_hit

    origin_name = _first(raw, "originCountryName", "origin_country_name", "originCountry")
    end = _first(raw, "endDate", "dateEnd", "date_end", "end")

    return {
        "cause_event_id": f"cyber_cf_radar:{layer}:{country_name}:{start}",
        "source": "cyber_cf_radar",
        "cause": "cyber",
        "cause_subtype": label + (f" (origin: {origin_name})" if origin_name else ""),
        "country": country_name,
        "lat": lat,
        "lon": lon,
        "event_date": start,
        "title": f"Notable {label} targeting {country_name}",
        "raw": {"layer": layer, "start": start, "end": end},
    }


def fetch(lookback_days: int, api_token: str) -> list[dict]:
    if not api_token:
        logger.info("Cyber attacks (Cloudflare Radar): no API token configured, skipping source")
        return []

    date_end = datetime.now(timezone.utc)
    date_start = date_end - timedelta(days=lookback_days)
    headers = {"Authorization": f"Bearer {api_token}"}
    params = {
        "dateStart": date_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dateEnd": date_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 100,
    }

    cause_events = []
    for layer, label in LAYERS:
        try:
            resp = requests.get(
                f"{BASE_URL}/{layer}/top/attacks", headers=headers, params=params, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as exc:
            logger.warning("Cyber attacks (Cloudflare Radar %s): unreachable, skipping: %s", layer, exc)
            continue

        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        attacks = None
        for val in result.values():
            if isinstance(val, list):
                attacks = val
                break
        if attacks is None:
            logger.warning("Cyber attacks (Cloudflare Radar %s): unrecognized response shape, skipping", layer)
            continue

        for raw in attacks:
            if not isinstance(raw, dict):
                continue
            normalized = _normalize(raw, layer, label)
            if normalized:
                cause_events.append(normalized)

    logger.info("Cyber attacks (Cloudflare Radar): %d notable DDoS attacks resolved to a country", len(cause_events))
    return cause_events
