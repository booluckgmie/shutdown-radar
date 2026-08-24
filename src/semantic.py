"""Phase 3 — semantic gap-filling for outages with no structured-source match.

For each outage still `unexplained` after Phase 2, search recent news via
Serper.dev, ask Groq (Llama) to extract {location_text, date, event_type,
cause} from the snippets, then geocode location_text through Nominatim
(never trust LLM-generated coordinates directly, per the project's design
principles). Records produced here are always tagged
`source_type="semantic"` and capped at confidence in {low, medium} — they
never compete with structured-source confidence tiers.

Gated entirely behind SERPER_API_KEY + GROQ_API_KEY; both free tiers are
small, so this only processes the highest-severity unexplained outages
(MAX_EVENTS_PER_RUN) rather than the whole backlog on every run.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time

import requests

from . import config, db

logger = logging.getLogger(__name__)

SERPER_URL = "https://google.serper.dev/search"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

MAX_EVENTS_PER_RUN = 15
NOMINATIM_MIN_INTERVAL_SEC = 1.0

EXTRACTION_PROMPT = """You are extracting structured facts from news search snippets about a \
possible internet/connectivity outage. Read the snippets and respond with ONLY a JSON object:
{{"location_text": "<specific place name, or null if unclear>",
  "event_date": "<YYYY-MM-DD, or null>",
  "cause": "<one of: disaster, conflict, shutdown, unexplained>",
  "cause_subtype": "<short phrase, e.g. 'flood', 'shelling', 'govt_order', or null>",
  "confidence": "<low or medium>"}}
Only claim disaster/conflict/shutdown if the snippets give a real reason. If nothing useful is \
present, return cause "unexplained" with confidence "low".

Country: {country}
Approximate date: {date}
Search snippets:
{snippets}
"""


def _search_snippets(query: str, api_key: str) -> str:
    resp = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": query, "num": 5},
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    lines = []
    for item in payload.get("organic", [])[:5]:
        lines.append(f"- {item.get('title', '')}: {item.get('snippet', '')} ({item.get('date', '')})")
    return "\n".join(lines) if lines else "(no results)"


def _extract_with_groq(country: str, date: str, snippets: str, api_key: str) -> dict | None:
    prompt = EXTRACTION_PROMPT.format(country=country, date=date, snippets=snippets)
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        },
        timeout=30,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except (json.JSONDecodeError, KeyError):
        logger.debug("Groq returned non-JSON extraction, discarding")
        return None


_last_nominatim_call = 0.0


def _geocode(location_text: str, user_agent: str) -> tuple[float, float] | None:
    global _last_nominatim_call
    elapsed = time.monotonic() - _last_nominatim_call
    if elapsed < NOMINATIM_MIN_INTERVAL_SEC:
        time.sleep(NOMINATIM_MIN_INTERVAL_SEC - elapsed)
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": location_text, "format": "json", "limit": 1},
            headers={"User-Agent": user_agent},
            timeout=15,
        )
        _last_nominatim_call = time.monotonic()
        resp.raise_for_status()
        results = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if not results:
        return None
    try:
        return float(results[0]["lat"]), float(results[0]["lon"])
    except (KeyError, ValueError):
        return None


def run(conn: sqlite3.Connection, settings: config.Settings) -> dict[str, int]:
    if not settings.has_semantic():
        logger.info("Semantic layer: SERPER_API_KEY/GROQ_API_KEY not set, skipping")
        return {"enriched": 0, "skipped": 0}

    unexplained = db.fetch_unexplained_events(conn)
    unexplained = sorted(unexplained, key=lambda r: r["severity_score"] or 0, reverse=True)
    unexplained = unexplained[:MAX_EVENTS_PER_RUN]

    stats = {"enriched": 0, "skipped": 0}
    updates = []
    for event in unexplained:
        date_str = (event["timestamp_start"] or "")[:10]
        query = f"internet outage OR internet disruption {event['country']} {date_str}"
        try:
            snippets = _search_snippets(query, settings.serper_api_key)
            extraction = _extract_with_groq(event["country"], date_str, snippets, settings.groq_api_key)
        except requests.RequestException as exc:
            logger.warning("Semantic layer request failed for %s, skipping event: %s", event["event_id"], exc)
            stats["skipped"] += 1
            continue

        if not extraction or extraction.get("cause") in (None, "unexplained"):
            stats["skipped"] += 1
            continue

        lat, lon = event["lat"], event["lon"]
        location_text = extraction.get("location_text")
        if location_text:
            geocoded = _geocode(f"{location_text}, {event['country']}", settings.nominatim_user_agent)
            if geocoded:
                lat, lon = geocoded

        confidence = extraction.get("confidence") if extraction.get("confidence") in ("low", "medium") else "low"
        updates.append(
            {
                "event_id": event["event_id"],
                "lat": lat,
                "lon": lon,
                "region_name": event["region_name"],
                "country": event["country"],
                "asn": event["asn"],
                "timestamp_start": event["timestamp_start"],
                "timestamp_end": event["timestamp_end"],
                "duration_hours": event["duration_hours"],
                "cause": extraction["cause"],
                "cause_subtype": extraction.get("cause_subtype"),
                "source_type": "semantic",
                "source_name": "Serper+Groq",
                "confidence": confidence,
                "severity_score": event["severity_score"],
            }
        )
        stats["enriched"] += 1

    if updates:
        db.upsert_events(conn, updates)
    logger.info("Semantic layer: enriched %d, skipped %d", stats["enriched"], stats["skipped"])
    return stats
