"""Government shutdown/restriction announcement scraper — Phase 2 cause source.

Unlike #KeepItOn (a manually-curated third-party dataset) or FAA NOTAM (a
structured API), this reads official government/regulator pages directly:
telecom ministry notices, regulator press releases, and similar first-party
"internet shut down here" or "movement/communications restricted here"
announcements that don't have a feed or API of their own.

Configuration (GOV_SHUTDOWN_SOURCES): a comma-separated list of
"CountryName|https://example.gov/notices" pairs — you supply the country
because a government domain can't be reliably mapped to one automatically,
and you supply the URL because there's no directory of these pages to crawl
from. Leave unset to skip this source entirely (fail-soft, like every other
source in this pipeline).

Deliberately conservative about *how* it fetches: government sites are
normally plain, unprotected HTML, so this uses `requests` with a descriptive
User-Agent (same courtesy this project already extends to Nominatim) rather
than a stealth/anti-bot-bypass toolchain. If a specific target page turns
out to sit behind Cloudflare or similar, that's a signal to special-case
that one page, not to reach for evasion tooling by default.

Honesty about precision: this has no per-page structured parser, so it
can't extract a specific announcement's real date the way an RSS feed or
API would. It instead treats a keyword hit anywhere on the page as "a
restriction notice is live on this page as of now" and dates the record to
the fetch time, deduplicated per country per day (see cause_event_id). This
is a coarser, lower-confidence signal than #KeepItOn/FAA NOTAM's structured
records — see SOURCE_PRIORITY in attribution.py, where it's ranked last.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .. import geo

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20
REQUEST_HEADERS = {
    "User-Agent": "shutdown-radar/1.0 (+https://github.com/booluckgmie/shutdown-radar; "
    "research project tracking government-ordered internet/communications restrictions)"
}

# Deliberately broad and multi-domain (internet shutdowns, movement/curfew
# restrictions, communications blackouts) since one government notice page
# might phrase a restriction any of these ways.
RESTRICTION_KEYWORDS = (
    "internet shutdown", "network shutdown", "shut down the internet",
    "suspend internet", "suspend access", "suspension of internet",
    "internet restriction", "internet blackout", "communications blackout",
    "network disruption order", "block access to", "social media block",
    "restricted zone", "movement restriction", "curfew",
    "state of emergency", "telecommunications suspended", "network suspended",
)

CONTEXT_CHARS = 100  # how much surrounding text to keep as an audit snippet


def has_gov_shutdown_scraper(sources_config: str) -> bool:
    return bool(sources_config and sources_config.strip())


def _parse_sources(sources_config: str) -> list[tuple[str, str]]:
    parsed = []
    for entry in sources_config.split(","):
        entry = entry.strip()
        if not entry or "|" not in entry:
            continue
        country, url = entry.split("|", 1)
        country, url = country.strip(), url.strip()
        if country and url:
            parsed.append((country, url))
    return parsed


def _page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def _find_match(text: str) -> tuple[str, str] | None:
    lowered = text.lower()
    for kw in RESTRICTION_KEYWORDS:
        idx = lowered.find(kw)
        if idx == -1:
            continue
        start = max(0, idx - CONTEXT_CHARS)
        end = min(len(text), idx + len(kw) + CONTEXT_CHARS)
        snippet = re.sub(r"\s+", " ", text[start:end]).strip()
        return kw, snippet
    return None


def fetch(lookback_days: int, sources_config: str) -> list[dict]:
    if not has_gov_shutdown_scraper(sources_config):
        logger.info("Gov shutdown scraper: no GOV_SHUTDOWN_SOURCES configured, skipping source")
        return []

    sources = _parse_sources(sources_config)
    if not sources:
        logger.warning(
            "Gov shutdown scraper: GOV_SHUTDOWN_SOURCES set but no valid "
            "'Country|https://...' entries found, skipping source"
        )
        return []

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    cause_events = []
    checked, matched, failed = 0, 0, 0

    for country, url in sources:
        checked += 1
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Gov shutdown scraper: %s (%s) unreachable, skipping: %s", country, url, exc)
            failed += 1
            continue

        try:
            text = _page_text(resp.text)
        except Exception as exc:  # malformed HTML shouldn't take down the whole run
            logger.warning("Gov shutdown scraper: %s (%s) failed to parse, skipping: %s", country, url, exc)
            failed += 1
            continue

        hit = _find_match(text)
        if not hit:
            continue
        keyword, snippet = hit
        matched += 1

        geo_hit = geo.resolve_name(country)
        country_name, lat, lon = geo_hit if geo_hit else (country, None, None)

        cause_events.append(
            {
                "cause_event_id": f"gov_scraper:{country_name}:{today}",
                "source": "gov_scraper",
                "cause": "shutdown",
                "cause_subtype": keyword,
                "country": country_name,
                "lat": lat,
                "lon": lon,
                "event_date": now.isoformat(),
                "title": f"Restriction notice detected on {country_name} government page: “{snippet}”",
                "raw": {"url": url, "matched_keyword": keyword},
            }
        )

    logger.info(
        "Gov shutdown scraper: %d/%d pages matched a restriction keyword (%d unreachable)",
        matched, checked, failed,
    )
    return cause_events
