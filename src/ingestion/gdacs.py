"""GDACS — Global Disaster Alert and Coordination System.

Free, no key, GeoRSS feed: https://www.gdacs.org/xml/rss.xml (rolling feed of
currently active alerts; verify against https://www.gdacs.org/Knowledge/ for
the current feed URL/schema before relying on this in production).

Tag names below (gdacs:fromdate, gdacs:country, gdacs:eventtype,
gdacs:alertlevel) were cross-checked against a maintained third-party GDACS
GeoRSS parser (exxamalte/python-aio-georss-gdacs) and match. What's *not*
verified is the exact `xmlns:gdacs="..."` URI the live feed declares —
ElementTree's namespace-aware `findtext("gdacs:x", namespaces=NS)` silently
returns None for every element if that URI is even slightly off (no
exception, no warning), which was quietly producing zero results in
production against this project's original hardcoded NS dict. To not
depend on getting that URI exactly right, this version matches by local
tag name (stripping whatever namespace ElementTree expands the tag to)
instead of a fixed prefix->URI map — the field names are the part that's
actually verified, so match on those.
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

EVENT_TYPE_SUBTYPE = {
    "EQ": "earthquake",
    "TC": "tropical_cyclone",
    "FL": "flood",
    "VO": "volcano",
    "DR": "drought",
    "WF": "wildfire",
    "TS": "tsunami",
}


def _local_name(tag: str) -> str:
    """'{http://www.gdacs.org}eventtype' -> 'eventtype', regardless of
    whatever the actual namespace URI turns out to be."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(elem: ET.Element, local_name: str) -> str | None:
    for child in elem.iter():
        if child is elem:
            continue
        if _local_name(child.tag).lower() == local_name.lower():
            text = (child.text or "").strip()
            return text or None
    return None


def _find_containers(root: ET.Element) -> list[ET.Element]:
    """RSS 'item' by local name, tolerant of an unexpected root namespace
    or (should the feed ever switch shape) an Atom 'entry'."""
    return [el for el in root.iter() if _local_name(el.tag) in ("item", "entry")]


def _parse_point(text: str | None) -> tuple[float, float] | None:
    if not text:
        return None
    parts = text.strip().split()
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
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

    items = _find_containers(root)
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    cause_events = []
    skipped_no_date, skipped_old = 0, 0

    for item in items:
        point = _parse_point(_child_text(item, "point"))
        event_type = _child_text(item, "eventtype")
        country = _child_text(item, "country")
        from_date = _child_text(item, "fromdate")
        title = _child_text(item, "title")
        pub_date_raw = _child_text(item, "pubDate")

        event_date = from_date
        if not event_date and pub_date_raw:
            try:
                event_date = parsedate_to_datetime(pub_date_raw).isoformat()
            except (TypeError, ValueError):
                event_date = None
        if not event_date:
            skipped_no_date += 1
            continue
        try:
            event_dt = datetime.fromisoformat(event_date.replace("Z", "+00:00"))
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            skipped_no_date += 1
            continue
        if event_dt < cutoff:
            skipped_old += 1
            continue

        lat, lon = point if point else (None, None)
        event_id = _child_text(item, "guid") or f"gdacs:{event_type}:{event_date}:{country}"
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

    logger.info(
        "GDACS: %d active disaster alerts within lookback window (%d containers seen, "
        "%d skipped: no parseable date, %d skipped: older than lookback)",
        len(cause_events), len(items), skipped_no_date, skipped_old,
    )
    return cause_events
