"""GDACS — Global Disaster Alert and Coordination System.

Free, no key, GeoRSS feed: https://www.gdacs.org/xml/rss.xml (rolling feed of
currently active alerts; verify against https://www.gdacs.org/Knowledge/ for
the current feed URL/schema before relying on this in production).
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

logger = logging.getLogger(__name__)

FEED_URL = "https://www.gdacs.org/xml/rss.xml"
REQUEST_TIMEOUT = 20

NS = {
    "georss": "http://www.georss.org/georss",
    "gdacs": "http://www.gdacs.org",
}

EVENT_TYPE_SUBTYPE = {
    "EQ": "earthquake",
    "TC": "tropical_cyclone",
    "FL": "flood",
    "VO": "volcano",
    "DR": "drought",
    "WF": "wildfire",
    "TS": "tsunami",
}


def _parse_point(text: str | None) -> tuple[float, float] | None:
    if not text:
        return None
    parts = text.strip().split()
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
        return lat, lon
    except ValueError:
        return None


def fetch(lookback_days: int) -> list[dict]:
    try:
        resp = requests.get(FEED_URL, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("GDACS unreachable, skipping source: %s", exc)
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        logger.warning("GDACS feed parse error, skipping source: %s", exc)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    cause_events = []
    for item in root.findall(".//item"):
        point = _parse_point((item.findtext("georss:point", namespaces=NS) or "").strip() or None)
        event_type = item.findtext("gdacs:eventtype", namespaces=NS)
        country = item.findtext("gdacs:country", namespaces=NS)
        from_date = item.findtext("gdacs:fromdate", namespaces=NS)
        title = item.findtext("title")
        pub_date_raw = item.findtext("pubDate")

        event_date = from_date
        if not event_date and pub_date_raw:
            try:
                event_date = parsedate_to_datetime(pub_date_raw).isoformat()
            except (TypeError, ValueError):
                event_date = None
        if not event_date:
            continue
        try:
            event_dt = datetime.fromisoformat(event_date.replace("Z", "+00:00"))
        except ValueError:
            continue
        if event_dt < cutoff:
            continue

        lat, lon = point if point else (None, None)
        event_id = item.findtext("guid") or f"gdacs:{event_type}:{event_date}:{country}"
        cause_events.append(
            {
                "cause_event_id": f"gdacs:{event_id}",
                "source": "gdacs",
                "cause": "disaster",
                "cause_subtype": EVENT_TYPE_SUBTYPE.get(event_type, event_type),
                "country": country,
                "lat": lat,
                "lon": lon,
                "event_date": event_dt.isoformat(),
                "title": title,
                "raw": {"eventtype": event_type, "country": country},
            }
        )
    logger.info("GDACS: %d active disaster alerts within lookback window", len(cause_events))
    return cause_events
