"""Phase 3 — semantic gap-filling for outages with no structured-source match.

For each outage still `unexplained` after Phase 2, search recent coverage via
Serper.dev — both its general web search *and* its dedicated news vertical,
so the extraction draws on whichever outlets actually covered the event
rather than whatever ranks highest in plain web search — ask Groq (Llama) to
extract {location_text, date, event_type, cause} from the combined results,
then geocode location_text through Nominatim (never trust LLM-generated
coordinates directly, per the project's design principles). Records produced
here are always tagged `source_type="semantic"` and capped at confidence in
{low, medium} — they never compete with structured-source confidence tiers.
Each resolved record's `source_name` lists the distinct outlet domains that
contributed, so the dashboard can show provenance down to which news sources
were actually behind a given attribution.

Gated entirely behind SERPER_API_KEY + GROQ_API_KEY; both free tiers are
small, so this only processes the highest-severity unexplained outages
(MAX_EVENTS_PER_RUN) rather than the whole backlog on every run.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from urllib.parse import urlparse

import requests

from . import config, db

logger = logging.getLogger(__name__)

SERPER_SEARCH_URL = "https://google.serper.dev/search"
SERPER_NEWS_URL = "https://google.serper.dev/news"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

MAX_EVENTS_PER_RUN = 15
MAX_OUTLETS_SHOWN = 3
RESULTS_PER_ENDPOINT = 5
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


def _domain(url: str) -> str | None:
    try:
        netloc = urlparse(url).netloc
        return netloc[4:] if netloc.startswith("www.") else netloc or None
    except ValueError:
        return None


def _gather_coverage(query: str, api_key: str) -> tuple[str, list[str]]:
    """Pool Serper's general web search and its news vertical so extraction
    draws on whichever outlets actually covered the event — returns
    (snippet text for the LLM prompt, distinct outlet domains found)."""
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    items: list[dict] = []
    for url, result_key in ((SERPER_SEARCH_URL, "organic"), (SERPER_NEWS_URL, "news")):
        try:
            resp = requests.post(url, headers=headers, json={"q": query, "num": RESULTS_PER_ENDPOINT}, timeout=20)
            resp.raise_for_status()
            items.extend(resp.json().get(result_key, [])[:RESULTS_PER_ENDPOINT])
        except requests.RequestException as exc:
            logger.debug("Serper %s request failed, continuing with other endpoint: %s", url, exc)
            continue

    lines, domains = [], []
    for item in items:
        lines.append(f"- {item.get('title', '')}: {item.get('snippet', '')} ({item.get('date', '')})")
        d = _domain(item.get("link", ""))
        if d and d not in domains:
            domains.append(d)
    return ("\n".join(lines) if lines else "(no results)"), domains


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
            snippets, domains = _gather_coverage(query, settings.serper_api_key)
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
        if domains:
            shown = ", ".join(domains[:MAX_OUTLETS_SHOWN])
            extra = len(domains) - MAX_OUTLETS_SHOWN
            outlets = shown + (f" +{extra} more" if extra > 0 else "")
            source_name = f"Serper+Groq ({outlets})"
        else:
            source_name = "Serper+Groq"
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
                "source_name": source_name,
                "confidence": confidence,
                "severity_score": event["severity_score"],
            }
        )
        stats["enriched"] += 1

    if updates:
        db.upsert_events(conn, updates)
    logger.info("Semantic layer: enriched %d, skipped %d", stats["enriched"], stats["skipped"])
    return stats
