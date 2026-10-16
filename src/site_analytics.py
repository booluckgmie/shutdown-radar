"""Site Web Analytics — pageview stats for the deployed dashboard itself.

Unlike every other module under src/, this doesn't describe a disruption
event; it describes usage of this tool. Pulled server-side at build time
(Phase 4, see main.py cmd_build) from Vercel's Web Analytics query API and
baked into dist/data.json as `site_analytics` — the same fail-soft,
baked-at-refresh-time pattern as every ingestion source: a missing token,
Web Analytics not yet enabled on the project, an auth/network error, or an
unrecognized response shape all degrade to `None` (dashboard shows "not
yet configured") rather than failing the build.

Two one-time manual steps outside this repo are required before this does
anything (no API exists to do either remotely — confirmed via Vercel's own
MCP: project write/analytics-query scope was not grantable to this repo's
assistant session, so this had to be designed without a live test run):
  1. Vercel dashboard -> this project -> Analytics tab -> Enable Web
     Analytics.
  2. Create a Vercel Access Token (Account Settings -> Tokens) scoped to
     the team that owns this project, and add it as the VERCEL_API_TOKEN
     GitHub secret (see README).

Response-shape hedge: the exact JSON field names returned by Vercel's
web-analytics query endpoints aren't publicly documented and couldn't be
confirmed against a live call while building this, so `_pick_total` /
`_normalize_rows` below extract generically (first numeric field = the
count, first string field = the label) rather than assuming specific key
names. If Vercel's actual response doesn't fit that shape, fetch_summary
logs a warning and returns None instead of shipping wrong numbers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.vercel.com"
REQUEST_TIMEOUT = 20
WINDOW_DAYS = 30


def _get(path: str, token: str, project_id: str, team_id: str, params: dict) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    query = {"projectId": project_id, **params}
    if team_id:
        query["teamId"] = team_id
    resp = requests.get(f"{API_BASE}{path}", headers=headers, params=query, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _pick_total(data: dict) -> int | None:
    for v in data.values():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
    return None


def _normalize_rows(data: Any) -> list[dict[str, Any]] | None:
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


def fetch_summary(token: str, project_id: str, team_id: str) -> dict[str, Any] | None:
    if not (token and project_id):
        logger.info("Site analytics: VERCEL_API_TOKEN/VERCEL_PROJECT_ID not configured, skipping")
        return None

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=WINDOW_DAYS)).isoformat()
    until = now.isoformat()

    try:
        total_raw = _get(
            "/v1/query/web-analytics/visits/count", token, project_id, team_id,
            {"since": since, "until": until},
        )
        by_day_raw = _get(
            "/v1/query/web-analytics/visits/aggregate", token, project_id, team_id,
            {"since": since, "until": until, "by": "day"},
        )
        top_pages_raw = _get(
            "/v1/query/web-analytics/visits/aggregate", token, project_id, team_id,
            {"since": since, "until": until, "by": "requestPath", "limit": 5},
        )
        top_countries_raw = _get(
            "/v1/query/web-analytics/visits/aggregate", token, project_id, team_id,
            {"since": since, "until": until, "by": "country", "limit": 5},
        )
        top_referrers_raw = _get(
            "/v1/query/web-analytics/visits/aggregate", token, project_id, team_id,
            {"since": since, "until": until, "by": "referrerHostname", "limit": 5},
        )
    except requests.RequestException as exc:
        logger.warning("Site analytics: Vercel API unreachable/unauthorized, skipping: %s", exc)
        return None

    total = _pick_total(total_raw)
    by_day = _normalize_rows(by_day_raw.get("data"))
    top_pages = _normalize_rows(top_pages_raw.get("data"))
    top_countries = _normalize_rows(top_countries_raw.get("data"))
    top_referrers = _normalize_rows(top_referrers_raw.get("data"))

    if total is None or by_day is None:
        logger.warning(
            "Site analytics: Vercel response didn't match the expected shape, skipping "
            "(raw total response keys: %s)", list(total_raw.keys()),
        )
        return None

    logger.info("Site analytics: %s total pageviews over last %d days", total, WINDOW_DAYS)
    return {
        "window_days": WINDOW_DAYS,
        "total_pageviews": total,
        "by_day": by_day,
        "top_pages": top_pages or [],
        "top_countries": top_countries or [],
        "top_referrers": top_referrers or [],
        "fetched_at": now.isoformat(),
    }
