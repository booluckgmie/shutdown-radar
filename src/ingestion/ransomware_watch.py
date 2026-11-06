"""ransomware.live — ransomware group leak-site claims, a Phase 2 "cyber"
cause source.

Free, public, no API key (https://www.ransomware.live, aggregated from
ransomware groups' own dark-web leak sites — a well-established open
threat-intel project, not a dark-web scrape of our own). Chosen over
relying solely on src/ingestion/cyber_attacks.py's Cloudflare Radar DDoS
feed (still unverified against a live call and returning 0 records in
production) and over Google Trends (tested directly against this project's
candidate search terms: per-country "interest by region" is a
within-country-normalized popularity ranking, not a dated/located event —
in testing it pegged 100+ countries to an identical "100" score purely
from low search-volume normalization noise, the opposite of the
documented-evidence standard the rest of this pipeline holds to).

`/v2/victims/{year}/{month}` returns that month's full set of ransomware
victim claims — each one dated (when the group posted the claim, not
necessarily when the breach itself began; same publish-time-vs-event-time
caveat GDACS/ACLED's own feeds carry) and country-coded. Deliberately
terse on what gets surfaced: cause_subtype/title name only the victim
organization, sector, and ransomware group (already public once a group
posts a claim) — never the leak-site's own description text or its
dark-web (.onion) claim URL, which this module doesn't even request.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

from .. import geo

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ransomware.live/v2/victims"
REQUEST_TIMEOUT = 20


def _months_in_window(lookback_days: int, now: datetime) -> list[tuple[int, int]]:
    start = now - timedelta(days=lookback_days)
    months, cursor = [], start.replace(day=1)
    while (cursor.year, cursor.month) <= (now.year, now.month):
        months.append((cursor.year, cursor.month))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def _parse_date(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    # Some records carry a bare date/naive timestamp rather than a full
    # offset-aware one -- assume UTC rather than crashing the comparison.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch(lookback_days: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=lookback_days)

    cause_events = []
    for year, month in _months_in_window(lookback_days, now):
        try:
            resp = requests.get(f"{BASE_URL}/{year}/{month}", timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            records = resp.json()
        except requests.RequestException as exc:
            logger.warning("ransomware.live %d-%02d unreachable, skipping: %s", year, month, exc)
            continue
        if not isinstance(records, list):
            logger.warning("ransomware.live %d-%02d: unrecognized response shape, skipping", year, month)
            continue

        for raw in records:
            if not isinstance(raw, dict):
                continue
            country_code = (raw.get("country") or "").strip()
            if not country_code:
                continue
            attack_dt = _parse_date(raw.get("attackdate") or "")
            if not attack_dt or attack_dt < window_start:
                continue
            geo_hit = geo.lookup(country_code)
            if not geo_hit:
                continue
            country_name, lat, lon = geo_hit

            group = raw.get("group") or "unknown group"
            victim = raw.get("victim") or "an organization"
            activity = raw.get("activity")

            cause_events.append(
                {
                    "cause_event_id": f"ransomware_watch:{country_code}:{raw.get('url') or victim}",
                    "source": "ransomware_watch",
                    "cause": "cyber",
                    "cause_subtype": f"Ransomware claim ({group})",
                    "country": country_name,
                    "lat": lat,
                    "lon": lon,
                    "event_date": attack_dt.isoformat(),
                    "title": f"{victim}" + (f" ({activity})" if activity else "") + f" — claimed by {group} ransomware group",
                    "raw": {"group": group, "activity": activity, "url": raw.get("url")},
                }
            )

    logger.info("ransomware.live: %d ransomware claims within lookback window resolved to a country", len(cause_events))
    return cause_events
