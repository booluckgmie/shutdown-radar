"""Global cyber-attack volume — raw attack-traffic intensity by country and
industry, the kind of thing a commercial threat map (FortiGuard, Kaspersky,
etc.) shows as a live globe: "Malaysia saw 950 attacks in the last 24h."

This is deliberately NOT a Phase 2 cause source. Every other module in this
project attributes a specific, dated, located outage to a specific, dated,
located cause (see src/attribution.py) — but "Malaysia received 950 attack
hits in the last 24h" is an aggregate traffic-share statistic, not a
discrete event with a timestamp a Phase 1 outage signal could be matched
against. Treating it as a cause_event would misrepresent what it actually
is. Instead it's background context, pulled fresh at build time and baked
into dist/data.json as `threat_landscape` — same non-attribution pattern as
src/site_analytics.py.

Deliberately NOT scraped from FortiGuard's own Threat Map: that's a
commercial vendor's proprietary visualization (not a published open API),
and pulling it programmatically would be a ToS violation, the same category
of problem flagged for Google Trends elsewhere in this project (see
src/ingestion/ransomware_watch.py's docstring) but legal/contractual here
rather than a data-quality one. Cloudflare Radar's attack-traffic-share
endpoints are the free, ToS-clean equivalent — reusing CLOUDFLARE_API_TOKEN,
no new credential.

Response-shape hedge, same caveat as site_analytics.py: these exact
endpoint paths and field names are this module author's best understanding
of Cloudflare Radar's v4 API, not verified against a live call (no working
token in the sandbox this was built in -- see cyber_attacks.py, which hit
the same limitation and has been returning 0 results in production as a
result). Unlike that earlier attempt, failures here log the full raw
response body, not just a one-line summary, so the next scheduled run's
logs give a definitive, fixable answer instead of a silent empty result.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.cloudflare.com/client/v4/radar/attacks"
REQUEST_TIMEOUT = 20
TOP_N = 10


def _get(path: str, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"{BASE_URL}{path}", headers=headers, params={"dateRange": "1d", "limit": TOP_N}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _normalize_rows(data: Any) -> list[dict[str, Any]] | None:
    """Generic extraction, same approach as site_analytics.py: first string
    field in a row = label, first numeric field = value. Doesn't assume
    Cloudflare's exact key names (e.g. targetCountryName vs target_country)."""
    if not isinstance(data, list):
        return None
    out = []
    for row in data:
        if not isinstance(row, dict):
            return None
        label, value = None, None
        for v in row.values():
            if isinstance(v, str) and label is None:
                label = v
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and value is None:
                value = v
        out.append({"label": label if label is not None else "(unknown)", "value": value or 0})
    return out


def _fetch_ranked(path: str, token: str, metric_name: str) -> list[dict[str, Any]] | None:
    try:
        raw = _get(path, token)
    except requests.RequestException as exc:
        logger.warning("Threat landscape (%s): unreachable, skipping: %s", metric_name, exc)
        return None

    result = raw.get("result", {}) if isinstance(raw, dict) else {}
    rows = None
    for val in result.values():
        if isinstance(val, list):
            rows = val
            break

    normalized = _normalize_rows(rows) if rows is not None else None
    if normalized is None:
        logger.warning(
            "Threat landscape (%s): unrecognized response shape, skipping. Raw response: %s",
            metric_name, raw,
        )
        return None
    return normalized


def fetch_summary(token: str) -> dict[str, Any] | None:
    if not token:
        logger.info("Threat landscape: no CLOUDFLARE_API_TOKEN configured, skipping")
        return None

    top_targets_l3 = _fetch_ranked("/layer3/top/locations/target", token, "L3 top targeted countries")
    top_targets_l7 = _fetch_ranked("/layer7/top/locations/target", token, "L7 top targeted countries")
    top_industries = _fetch_ranked("/layer7/top/industry", token, "L7 top targeted industries")

    if top_targets_l3 is None and top_targets_l7 is None and top_industries is None:
        logger.warning("Threat landscape: all endpoints failed or returned an unrecognized shape, skipping entirely")
        return None

    # Combine L3 + L7 target-country rankings into one list by summing
    # matching labels, since the dashboard shows one "most targeted
    # countries" ranking rather than splitting by attack layer.
    combined: dict[str, float] = {}
    for rows in (top_targets_l3 or [], top_targets_l7 or []):
        for r in rows:
            combined[r["label"]] = combined.get(r["label"], 0) + r["value"]
    top_countries = sorted(
        ({"label": k, "value": v} for k, v in combined.items()), key=lambda r: r["value"], reverse=True
    )[:TOP_N]

    logger.info(
        "Threat landscape: %d countries / %d industries ranked by attack-traffic share",
        len(top_countries), len(top_industries or []),
    )
    return {
        "window": "last 24h",
        "top_countries": top_countries,
        "top_industries": top_industries or [],
    }
