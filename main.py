#!/usr/bin/env python3
"""Infrastructure Disruption Tracker — pipeline CLI.

    python main.py demo                 # offline: synthetic seed data -> dashboard
    python main.py fetch                # Phase 1+2: real ingestion (needs network)
    python main.py attribute            # Phase 2: re-run join logic against stored data
    python main.py semantic             # Phase 3: LLM gap-filling (needs SERPER/GROQ keys)
    python main.py build                # Phase 4: export + render dashboard from current DB
    python main.py all                  # fetch + attribute + semantic + build (full real run)

Every step is fail-soft: a source that errors or lacks credentials is
skipped and logged, the run continues with reduced coverage rather than
aborting (see README "Design Principles").
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from src import attribution, config, db, export, semantic
from src.ingestion import acled, cloudflare_radar, gdacs, ioda, keepiton, ripe_atlas
from src import seed_demo_data
from dashboard.build_dashboard import render_dashboard

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("pipeline")


def cmd_seed(args: argparse.Namespace) -> None:
    with db.connect() as conn:
        db.init_db(conn)
        events = seed_demo_data.generate(lookback_days=args.lookback_days or 90)
        n = db.upsert_events(conn, events)
        logger.info("Seeded %d synthetic demo events (see src/seed_demo_data.py)", n)


def cmd_fetch(args: argparse.Namespace) -> None:
    settings = config.SETTINGS
    lookback = args.lookback_days or settings.lookback_days
    started = datetime.now(timezone.utc).isoformat()
    ok, failed = [], []

    with db.connect() as conn:
        db.init_db(conn)

        # Phase 1 — base outage signal
        for name, fn in (
            ("ioda", lambda: ioda.fetch(lookback)),
            ("cloudflare_radar", lambda: cloudflare_radar.fetch(lookback, settings.cloudflare_api_token)),
            ("ripe_atlas", lambda: ripe_atlas.fetch(lookback)),
        ):
            try:
                events = fn()
                db.upsert_events(conn, events)
                (ok if events or name == "cloudflare_radar" else failed).append(name)
            except Exception:
                logger.exception("Source %s failed unexpectedly, continuing", name)
                failed.append(name)

        # Phase 2 — cause-labeled sources (staged in cause_events for attribution.run)
        for name, fn in (
            ("gdacs", lambda: gdacs.fetch(lookback)),
            ("acled", lambda: acled.fetch(lookback, settings.acled_api_key, settings.acled_email)),
            ("keepiton", lambda: keepiton.fetch(lookback, settings.keepiton_csv_path)),
        ):
            try:
                cause_events = fn()
                db.upsert_cause_events(conn, cause_events)
                (ok if cause_events else failed).append(name)
            except Exception:
                logger.exception("Source %s failed unexpectedly, continuing", name)
                failed.append(name)

        stats = attribution.run(conn)
        finished = datetime.now(timezone.utc).isoformat()
        db.record_run(conn, started, finished, ok, failed, notes=json.dumps(stats))

    logger.info("Fetch complete. OK sources: %s | failed/skipped: %s", ok, failed)


def cmd_attribute(args: argparse.Namespace) -> None:
    with db.connect() as conn:
        db.init_db(conn)
        attribution.run(conn)


def cmd_semantic(args: argparse.Namespace) -> None:
    with db.connect() as conn:
        db.init_db(conn)
        semantic.run(conn, config.SETTINGS)


def cmd_build(args: argparse.Namespace) -> None:
    with db.connect() as conn:
        db.init_db(conn)
        payload = export.build_payload(conn)

    data_path = config.DIST_DIR / "data.json"
    data_path.write_text(json.dumps(payload, indent=2, default=str))
    logger.info("Wrote %s (%d events)", data_path, payload["meta"]["total_events"])

    out_path = config.DIST_DIR / "dashboard.html"
    render_dashboard(payload, out_path)
    logger.info("Wrote %s", out_path)


def cmd_all(args: argparse.Namespace) -> None:
    cmd_fetch(args)
    cmd_semantic(args)
    cmd_build(args)


def cmd_demo(args: argparse.Namespace) -> None:
    cmd_seed(args)
    cmd_attribute(args)
    cmd_build(args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lookback-days", type=int, default=None, help="override LOOKBACK_DAYS")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed", help="load synthetic demo data (offline, no network)").set_defaults(func=cmd_seed)
    sub.add_parser("fetch", help="Phase 1+2: real ingestion + attribution join").set_defaults(func=cmd_fetch)
    sub.add_parser("attribute", help="Phase 2: re-run attribution join only").set_defaults(func=cmd_attribute)
    sub.add_parser("semantic", help="Phase 3: LLM gap-filling for unexplained events").set_defaults(func=cmd_semantic)
    sub.add_parser("build", help="Phase 4: export data.json + render dashboard.html").set_defaults(func=cmd_build)
    sub.add_parser("all", help="fetch + semantic + build (full real-data run)").set_defaults(func=cmd_all)
    sub.add_parser("demo", help="seed + attribute + build (offline demo run)").set_defaults(func=cmd_demo)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
