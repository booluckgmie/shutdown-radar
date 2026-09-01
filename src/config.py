"""Central configuration loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DIST_DIR = ROOT_DIR / "dist"
DB_PATH = DATA_DIR / "disruption_tracker.sqlite"

CAUSES = ("disaster", "conflict", "shutdown", "unexplained")
SOURCE_TYPES = ("structured", "semantic")
CONFIDENCE_TIERS = ("high", "medium", "low")

# Attribution join window (Phase 2): an outage is matched to a cause event
# when they fall within this many hours of each other and in the same
# region/country (see src/attribution.py).
ATTRIBUTION_WINDOW_HOURS = 72


@dataclass
class Settings:
    lookback_days: int = int(os.environ.get("LOOKBACK_DAYS", "14"))

    cloudflare_api_token: str = field(default_factory=lambda: os.environ.get("CLOUDFLARE_API_TOKEN", ""))

    acled_api_key: str = field(default_factory=lambda: os.environ.get("ACLED_API_KEY", ""))
    acled_email: str = field(default_factory=lambda: os.environ.get("ACLED_EMAIL", ""))

    keepiton_csv_path: str = field(default_factory=lambda: os.environ.get("KEEPITON_CSV_PATH", ""))

    serper_api_key: str = field(default_factory=lambda: os.environ.get("SERPER_API_KEY", ""))
    groq_api_key: str = field(default_factory=lambda: os.environ.get("GROQ_API_KEY", ""))

    nominatim_user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "NOMINATIM_USER_AGENT", "shutdown-radar/1.0 (no contact configured)"
        )
    )

    # OpenSky works anonymously at a lower rate limit; a free registered
    # account (client_id/secret, OAuth2 client-credentials) raises it.
    # Optional either way.
    opensky_client_id: str = field(default_factory=lambda: os.environ.get("OPENSKY_CLIENT_ID", ""))
    opensky_client_secret: str = field(default_factory=lambda: os.environ.get("OPENSKY_CLIENT_SECRET", ""))

    # FAA NOTAM API — free registration at https://developer.faa.gov, but
    # US-airspace-scoped only (FAA = Federal Aviation Administration). Not
    # a global restricted-airspace source; see README "Limitations".
    faa_notam_client_id: str = field(default_factory=lambda: os.environ.get("FAA_NOTAM_CLIENT_ID", ""))
    faa_notam_client_secret: str = field(default_factory=lambda: os.environ.get("FAA_NOTAM_CLIENT_SECRET", ""))

    # Government shutdown/restriction announcement scraper — comma-separated
    # "CountryName|https://..." pairs pointing at official government/
    # regulator notice pages. See src/ingestion/gov_shutdown_scraper.py.
    gov_shutdown_sources: str = field(default_factory=lambda: os.environ.get("GOV_SHUTDOWN_SOURCES", ""))

    def has_cloudflare(self) -> bool:
        return bool(self.cloudflare_api_token)

    def has_acled(self) -> bool:
        return bool(self.acled_api_key and self.acled_email)

    def has_keepiton(self) -> bool:
        return bool(self.keepiton_csv_path)

    def has_semantic(self) -> bool:
        return bool(self.serper_api_key and self.groq_api_key)

    def has_faa_notam(self) -> bool:
        return bool(self.faa_notam_client_id and self.faa_notam_client_secret)

    def has_gov_shutdown_sources(self) -> bool:
        return bool(self.gov_shutdown_sources)


SETTINGS = Settings()

DATA_DIR.mkdir(exist_ok=True)
DIST_DIR.mkdir(exist_ok=True)
