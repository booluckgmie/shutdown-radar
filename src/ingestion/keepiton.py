"""#KeepItOn (Access Now) — verified government-ordered internet shutdowns.

Access Now does not publish a stable free public API for the #KeepItOn /
STOP (Shutdown Tracker Optimization Project) dataset — it's released as
periodic reports and a browsable map (https://www.accessnow.org/keepiton/).
Rather than guess at an endpoint, this connector reads a CSV you export
yourself (KEEPITON_CSV_PATH — a local path or an http(s) URL) and normalizes
whatever subset of columns it finds. This is the only source in the pipeline
that requires a manual data-prep step; see README "Data Sources" for the
expected column names.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20

# Accept a few plausible column-name spellings so a hand-exported CSV
# doesn't need to be reshaped just to match us exactly.
COLUMN_ALIASES = {
    "country": ("country", "Country", "location"),
    "start_date": ("start_date", "date", "Start Date", "event_date"),
    "end_date": ("end_date", "End Date"),
    "reason": ("reason", "cause_subtype", "Reason", "trigger"),
    "description": ("description", "notes", "Description", "context"),
}


def _get(row: dict, key: str) -> str | None:
    for alias in COLUMN_ALIASES[key]:
        if alias in row and row[alias]:
            return row[alias]
    return None


def _read_csv_text(path_or_url: str) -> str:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        resp = requests.get(path_or_url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    with open(path_or_url, "r", encoding="utf-8") as f:
        return f.read()


def fetch(lookback_days: int, csv_path: str) -> list[dict]:
    if not csv_path:
        logger.info("#KeepItOn: no KEEPITON_CSV_PATH configured, skipping source")
        return []

    try:
        text = _read_csv_text(csv_path)
    except (OSError, requests.RequestException) as exc:
        logger.warning("#KeepItOn CSV unreadable, skipping source: %s", exc)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    cause_events = []
    reader = csv.DictReader(io.StringIO(text))
    for i, row in enumerate(reader):
        country = _get(row, "country")
        start_date = _get(row, "start_date")
        if not country or not start_date:
            continue
        try:
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if start_dt < cutoff:
            continue

        cause_events.append(
            {
                "cause_event_id": f"keepiton:{country}:{start_date}:{i}",
                "source": "keepiton",
                "cause": "shutdown",
                "cause_subtype": (_get(row, "reason") or "govt_order"),
                "country": country,
                "lat": None,
                "lon": None,
                "event_date": start_dt.isoformat(),
                "title": _get(row, "description") or f"Government-ordered shutdown in {country}",
                "raw": dict(row),
            }
        )
    logger.info("#KeepItOn: %d verified shutdown records within lookback window", len(cause_events))
    return cause_events
