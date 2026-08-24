# Infrastructure Disruption Tracker

Correlating internet outages with natural disasters, armed conflict, and government shutdowns.

A real-data pipeline that detects internet connectivity outages worldwide and attributes
each one to a likely cause — natural disaster, armed conflict, government-ordered
shutdown, or unexplained/technical failure — using only free, open-access data sources.
The output is a single self-contained, interactive HTML dashboard: a bubble map plus
frequency/duration/fragility charts and auto-generated insights.

Internet connectivity signal loss is treated as a real-time proxy for physical
infrastructure damage — often faster and more granular than official reporting.

```
python main.py demo     # instant, offline: synthetic seed data -> dist/dashboard.html
python main.py all      # real pipeline: live sources -> dist/dashboard.html (needs network)
```

Open `dist/dashboard.html` directly in a browser — no server required.

## Problem statement

> Can publicly available internet outage signals be reliably correlated with natural
> disaster, conflict, and shutdown events to produce a real-time, cause-attributed map
> of global infrastructure disruption — without relying on manual reporting or paid
> data services?

Sub-questions this project targets (see the dashboard's "Auto-generated insights" panel,
which answers these live against whatever data is currently loaded):

1. Can outage data reveal disruptions before they're officially confirmed?
2. Do conflict-caused outages behave differently (duration, recovery shape) than
   disaster-caused ones?
3. Can a deliberate shutdown be distinguished from physical damage by the shape of the
   signal (short + high-confidence vs. long + gradual)?
4. Which regions have disproportionately frequent/long outages relative to triggering
   events — i.e. fragile infrastructure?

## How it works — the four phases

| Phase | What it does | Code |
|---|---|---|
| 1. Base signal | Detect outages with no cause attached yet | `src/ingestion/ioda.py`, `cloudflare_radar.py`, `ripe_atlas.py` |
| 2. Attribution | Join outages to cause-labeled events within a ±72h/same-country window | `src/ingestion/gdacs.py`, `acled.py`, `keepiton.py`, `src/attribution.py` |
| 3. Semantic gap-fill | News search + LLM extraction for events still unexplained | `src/semantic.py` |
| 4. Visualize | Export + render the interactive dashboard | `src/export.py`, `dashboard/build_dashboard.py` |

Every source is **fail-soft**: no key, a rate limit, or a network error just skips that
source (logged, not fatal) and the run continues with the rest at reduced coverage —
see `src/config.py`'s `Settings.has_*()` checks and the try/except in every
`src/ingestion/*.py::fetch()`.

Confidence is a direct function of how tight the time match was:

- **high** — matched a structured cause record within 24h, same country
- **medium** — within 48h
- **low** — within 72h, or a semantic-layer (LLM-extracted) match
- outage stays `cause="unexplained"` if nothing matched within 72h — and `confidence` is
  forced to `"low"` whenever `cause="unexplained"`, enforced centrally in
  `db.upsert_events` (there's nothing to be confident about with no attributed cause).
  The demo dataset (`src/seed_demo_data.py`) doesn't hardcode which outages end up
  unexplained either — it emits Phase 1 outages and Phase 2 cause records as two
  separate streams, the same shape the real connectors produce, and lets
  `attribution.py`'s real join decide; only ~75% of outages get a plausible nearby cause
  record at all, so the rest are genuinely earning the "unexplained" tag rather than
  being told to wear it.

The semantic layer (Phase 3) pools Serper's general web search *and* its dedicated news
vertical per query, not just one, and records which distinct outlet domains actually
contributed — e.g. `source_name: "Serper+Groq (reuters.com, apnews.com +2 more)"` — so
attribution provenance is visible down to which news sources were behind a given call,
not just "the LLM said so."

## ⚠️ A note on where this was built

This project was authored inside a sandboxed environment with **no outbound network
access** (only pypi/npm/github/anthropic reachable). That means the real connectors in
`src/ingestion/` could not be exercised live during development — they're written and
reviewed against each source's public API documentation, but not integration-tested
against the live endpoints here.

To make the pipeline demonstrable end-to-end anyway, `python main.py demo` runs
`src/seed_demo_data.py`: a seeded-random, clearly-labeled **synthetic** dataset shaped
exactly like real pipeline output (same schema, same country/cause distribution
patterns), so the attribution stats, dashboard, and insight panels can all be verified
against realistic data. Every seed record's `source_name` ends in `(seed)` and the
dashboard shows a banner when it detects seed-only data.

Two ways to get *real* data:

1. **Locally**, from a machine with normal internet access: `python main.py all`
   (see Setup below).
2. **On a schedule, automatically**: `.github/workflows/refresh.yml` runs the real
   pipeline **hourly** on a GitHub-hosted runner (which has full internet access) and
   commits the refreshed `dist/dashboard.html`/`dist/data.json` back to the repo, plus
   the growing `data/disruption_tracker.sqlite` itself — since `db.upsert_events` never
   deletes, that turns the rolling per-run lookback window into an actual accumulating
   historical archive across runs (see "Historical data" below), not just a snapshot of
   whatever's in the last 14 days. Configure API keys as repository secrets and it starts
   producing live data with zero local setup — IODA and GDACS need no key at all, so it's
   useful even with none configured. If the dashboard is also served over http(s) (e.g.
   GitHub Pages), it polls `data.json` every 15 minutes and hot-swaps in newer data with
   no manual reload — opening the file directly (`file://`) skips this automatically,
   since a local file can't `fetch()` another one.

## Setup

```
pip install -r requirements.txt
cp .env.example .env   # fill in whichever keys you have — all optional, see below
```

| Env var | Source | Required for |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | [Cloudflare Radar](https://developers.cloudflare.com/radar/get-started/first-request/) (free tier) | Phase 1 Cloudflare connector |
| `ACLED_API_KEY` / `ACLED_EMAIL` | [ACLED registration](https://acleddata.com/register/) (free) | Phase 2 conflict attribution |
| `KEEPITON_CSV_PATH` | Your own export of Access Now's [#KeepItOn](https://www.accessnow.org/keepiton/) data (no stable public API exists) | Phase 2 shutdown attribution |
| `SERPER_API_KEY` / `GROQ_API_KEY` | [Serper.dev](https://serper.dev) / [Groq](https://console.groq.com) (both free tier) | Phase 3 semantic gap-filling |
| `NOMINATIM_USER_AGENT` | none — just set a real contact per [Nominatim's usage policy](https://operations.osmfoundation.org/policies/nominatim/) | Phase 3 geocoding |

IODA, GDACS, and RIPE Atlas need no key and run unconditionally.

```
python main.py fetch       # Phase 1+2: real ingestion + attribution join
python main.py semantic    # Phase 3: LLM gap-filling (needs SERPER_API_KEY + GROQ_API_KEY)
python main.py build       # Phase 4: export dist/data.json + render dist/dashboard.html
python main.py all         # fetch + semantic + build in one go
python main.py demo        # offline: seed data + build (no network, no keys needed)
```

`--lookback-days N` overrides the rolling window (default from `LOOKBACK_DAYS`, 14 days)
on any subcommand.

## Data sources

| Source | Data type | Real-time? | Access | Granularity |
|---|---|---|---|---|
| [IODA](https://ioda.inetintel.cc.gatech.edu/) (Georgia Tech / CAIDA) | Outage signals (BGP, active probing, darknet traffic) | ~hourly | Free, no key | Country / ASN |
| [Cloudflare Radar](https://developers.cloudflare.com/radar/) | Traffic anomalies + outage annotations | Yes | Free, API token | Country |
| [RIPE Atlas](https://atlas.ripe.net/) | Probe disconnect events | Yes | Free, no key | City / ISP |
| [GDACS](https://www.gdacs.org/) | Disaster alerts (storms, quakes, floods) | Yes | Free, no key | Lat/lon |
| [ACLED](https://acleddata.com/) | Armed conflict / violence events | Near real-time | Free, registration | Lat/lon |
| [#KeepItOn](https://www.accessnow.org/keepiton/) (Access Now) | Verified govt-ordered shutdowns | Manually verified | Free, no stable API — bring your own CSV export | Country/region |
| Serper.dev + Groq | News extraction (semantic gap-fill) | Real-time | Free tier | Geocoded via Nominatim |

Free-tier API shapes drift over time — every connector module's docstring links the
spec it was written against and says to re-verify before relying on it in production
(see e.g. `src/ingestion/ioda.py`, `acled.py`).

## Unified schema

Every record — structured or semantic — lands in SQLite (`data/disruption_tracker.sqlite`)
as one row of the `events` table (`src/db.py`):

```
event_id, lat, lon, region_name, country, asn,
timestamp_start, timestamp_end, duration_hours,
cause [disaster|conflict|shutdown|unexplained], cause_subtype,
source_type [structured|semantic], source_name,
confidence [high|medium|low], severity_score
```

Raw cause-labeled records from GDACS/ACLED/#KeepItOn are staged separately in
`cause_events` before the Phase 2 join (`src/attribution.py`), so re-running attribution
never requires re-fetching those sources.

### Historical data

`db.upsert_events`/`upsert_cause_events` are upserts keyed by a stable `event_id` —
re-running `fetch` doesn't duplicate or drop anything outside the current lookback
window, it only touches rows the new fetch actually saw. The hourly GitHub Actions run
commits `data/disruption_tracker.sqlite` itself (see below), so the database is a
genuinely accumulating archive across runs, not just a rolling snapshot of the last
`LOOKBACK_DAYS`. The dashboard's time-range filter includes an "All available" option
specifically so that history stays reachable as it builds up — `db.fetch_all_events`
exports the whole table on every build, uncapped.

## Dashboard

`dist/dashboard.html` is a single file — Leaflet (map) and the app itself are the only
dependencies, loaded from CDN; every filter, chart, table, and insight is hand-rendered
vanilla JS/SVG against the JSON embedded in the page, so it needs no build step and no
server. Layout is a fixed sidebar-nav "ops console" shell (`dashboard/template.html`) —
Overview / Outage map / Trends & insights / Pipeline & sources / Raw data as scroll-spy'd
sections, a persistent dark nav rail regardless of light/dark content theme, a floating
legend/controls overlay on the map, and a collapsible off-canvas nav below 860px.
Filtering (cause, **region**, time range, confidence threshold,
structured-vs-semantic, free-text **search**) re-aggregates the map, every chart, the
fragility ranking, and the insight text live, client-side.

- **Bubble map** — one bubble per country in view; size = cumulative downtime-hours,
  color = dominant cause (red = conflict, orange = disaster, purple = shutdown,
  gray = unexplained). Scroll-to-zoom, drag-to-pan, and a "Reset view" button; degrades
  to a message (rest of the dashboard keeps working) if the Leaflet CDN is unreachable.
- **Region filter** — country → region tags computed in `src/regions.py` (Southeast Asia,
  ASEAN, South Asia, East Asia, Central Asia, Middle East, the broad "Asia" rollup,
  Europe, Africa, North/South America, Oceania); a country can carry more than one tag
  (Türkiye is both Middle East and Europe, every ASEAN state is also Southeast Asia).
- **Search box** — free-text match against country, region, cause subtype, and source
  name; flies the map to the matching country when the filter narrows to exactly one.
- **Weekly frequency chart**, **avg. recovery time by cause**, **fragility ranking**
  (top countries by downtime), and an **auto-generated insights** panel that
  recomputes the sub-question-style findings above against the current filter.
- **Data pipeline & sources** section — the four-phase flow plus a live table of every
  source in `src/export.py::SOURCE_CATALOG`: configured/not-configured (from your `.env`),
  and how many records each has actually contributed to the current database (outage
  events for Phase 1 sources, raw cause records for Phase 2 — pulled straight from the
  `cause_events` table, not estimated).
- A raw event table (collapsed by default) keeps every value reachable without hovering.
- Colors were chosen and validated for colorblind-safety against the project's data-viz
  skill (`validate_palette.js`): the conflict/disaster/shutdown hues clear both the CVD
  and normal-vision separation floors; "unexplained" gray is deliberately desaturated
  (a neutral/no-signal marker, not a competing identity color).

## Known limitations

- **Correlation ≠ causation.** Time/geo proximity is not proof of a causal link —
  confidence tiers flag this, they don't eliminate it.
- **Reporting bias.** The semantic layer over-represents heavily-covered regions.
- **Source lag.** ACLED and #KeepItOn are not truly real-time; "near real-time" applies
  mainly to IODA, Cloudflare, RIPE, and GDACS.
- **Geocoding precision.** Country-level joins can misattribute border-region or
  multi-ASN outages; bubbles sit at country centroids (`src/geo.py`), not exact sites.
- **Free-tier caps.** Serper/Groq are rate-limited; `src/semantic.py` only processes the
  highest-severity unexplained events per run (`MAX_EVENTS_PER_RUN`), not the full backlog.
- **Not integration-tested against live sources** — see the sandbox note above.

## Design principles

- Free/open-access only — no paid APIs.
- Modular, swappable sources — each is its own `src/ingestion/*.py` module.
- Fail-soft — a down/unkeyed source reduces coverage, never crashes the run.
- Self-contained deliverable — `dist/dashboard.html` is one portable file.
- Confidence-aware — every record carries `source_type` and `confidence`; nothing is
  presented as verified fact without structured-source backing.

## Roadmap

- [x] Phase 1 ingestion (IODA, Cloudflare, RIPE) + SQLite schema
- [x] Phase 2 attribution join (GDACS, ACLED, #KeepItOn)
- [x] Phase 3 semantic gap-filling layer (Serper + Groq + Nominatim)
- [x] Phase 4 interactive dashboard (bubble map, charts, insights)
- [x] Scheduled real-data refresh via GitHub Actions
- [ ] Verify each connector against live traffic (blocked in the authoring sandbox —
      see note above; do this first when picking the project back up)
- [ ] Apply for/confirm ACLED access and exercise the real endpoint
- [ ] Explore predictive/forecasting extension once a real historical archive exists

## Project layout

```
main.py                        CLI: seed | fetch | attribute | semantic | build | all | demo
src/
  config.py                    env-driven Settings, fail-soft has_*() checks
  db.py                        SQLite schema + upserts (events, cause_events)
  geo.py                       ISO country code -> name/centroid lookup
  attribution.py                Phase 2 join logic
  semantic.py                   Phase 3 LLM gap-filling
  export.py                     DB -> dashboard JSON payload
  seed_demo_data.py             synthetic offline demo dataset (NOT live data)
  ingestion/
    ioda.py cloudflare_radar.py ripe_atlas.py      Phase 1 connectors
    gdacs.py acled.py keepiton.py                  Phase 2 connectors
dashboard/
  template.html                page shell + CSS (light/dark)
  app.js                        filters, map, charts, insights (vanilla JS)
  build_dashboard.py            inlines data + app.js into dist/dashboard.html
.github/workflows/refresh.yml  scheduled real-data pipeline run
```
