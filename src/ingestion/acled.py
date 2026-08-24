"""ACLED — Armed Conflict Location & Event Data Project.

Free with registration (https://acleddata.com/register/). Classic REST
endpoint used here: https://api.acleddata.com/acled/read — ACLED has been
migrating some access paths to an OAuth flow; verify current auth
requirements against https://acleddata.com/knowledge-base/ before relying on
this in production. Rate-limited on the free tier, so we scope requests to
the rolling lookback window rather than pulling the full archive.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.acleddata.com/acled/read"
REQUEST_TIMEOUT = 30
PAGE_SIZE = 500
MAX_PAGES = 10


def fetch(lookback_days: int, api_key: str, email: str) -> list[dict]:
    if not (api_key and email):
        logger.info("ACLED: no API key/email configured, skipping source")
        return []

    until = datetime.now(timezone.utc).date()
    since = until - timedelta(days=lookback_days)

    session = requests.Session()
    cause_events: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        params = {
            "key": api_key,
            "email": email,
            "event_date": f"{since.isoformat()}|{until.isoformat()}",
            "event_date_where": "BETWEEN",
            "limit": PAGE_SIZE,
            "page": page,
        }
        try:
            resp = session.get(BASE_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as exc:
            logger.warning("ACLED unreachable, skipping source: %s", exc)
            return cause_events
        except ValueError as exc:
            logger.warning("ACLED returned non-JSON response, skipping source: %s", exc)
            return cause_events

        rows = payload.get("data", []) if isinstance(payload, dict) else []
        if not rows:
            break

        for row in rows:
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            event_date = row.get("event_date")
            if not event_date:
                continue
            cause_events.append(
                {
                    "cause_event_id": f"acled:{row.get('event_id_cnty', f'{lat}:{lon}:{event_date}')}",
                    "source": "acled",
                    "cause": "conflict",
                    "cause_subtype": row.get("sub_event_type") or row.get("event_type"),
                    "country": row.get("country"),
                    "lat": lat,
                    "lon": lon,
                    "event_date": f"{event_date}T00:00:00+00:00",
                    "title": row.get("notes", "")[:200],
                    "raw": {"event_type": row.get("event_type"), "fatalities": row.get("fatalities")},
                }
            )

        if len(rows) < PAGE_SIZE:
            break

    logger.info("ACLED: %d conflict events within lookback window", len(cause_events))
    return cause_events
