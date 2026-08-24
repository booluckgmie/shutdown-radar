"""Region taxonomy for the dashboard's region filter.

Countries aren't mutually exclusive to one bucket here — Turkiye is both
"Middle East" and "Europe", ASEAN is a political-bloc subset of the broader
"Southeast Asia" geographic tag, and every Asia sub-region also rolls up
into the broad "Asia" tag. `regions_for(code)` returns every tag that
applies so the dashboard filter can match on any of them.

This is derived data (not part of the unified Event schema stored in
SQLite) — src/export.py attaches it to each event in the JSON payload the
dashboard consumes.
"""
from __future__ import annotations

ASEAN = {"BN", "KH", "ID", "LA", "MY", "MM", "PH", "SG", "TH", "VN"}
SOUTHEAST_ASIA = ASEAN | {"TL"}
SOUTH_ASIA = {"AF", "BD", "BT", "IN", "LK", "NP", "PK"}
EAST_ASIA = {"CN", "HK", "TW", "JP", "KR", "KP", "MN"}
CENTRAL_ASIA = {"KZ", "KG", "TJ", "TM", "UZ"}
MIDDLE_EAST = {"AE", "IQ", "IR", "IL", "JO", "KW", "LB", "OM", "PS", "QA", "SA", "SY", "YE", "TR"}
ASIA = SOUTHEAST_ASIA | SOUTH_ASIA | EAST_ASIA | CENTRAL_ASIA | MIDDLE_EAST

EUROPE = {
    "AL", "AT", "BY", "BE", "BA", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
    "GR", "HU", "IS", "IE", "IT", "LV", "LT", "LU", "MD", "ME", "NL", "MK", "NO", "PL",
    "PT", "RO", "RS", "SK", "SI", "ES", "SE", "CH", "UA", "GB", "XK", "TR",
}
AFRICA = {
    "DZ", "AO", "BJ", "BW", "BF", "BI", "CM", "CF", "TD", "CI", "CD", "CG", "DJ", "EG",
    "GQ", "ER", "SZ", "ET", "GA", "GM", "GH", "GN", "GW", "KE", "LS", "LR", "LY", "MG",
    "MW", "ML", "MR", "MA", "MZ", "NA", "NE", "NG", "RW", "SN", "SL", "SO", "ZA", "SS",
    "SD", "TZ", "TG", "TN", "UG", "ZM", "ZW",
}
NORTH_AMERICA = {"CA", "US", "MX", "CR", "CU", "DO", "SV", "GT", "HT", "HN", "JM", "NI", "PA", "TT"}
SOUTH_AMERICA = {"AR", "BO", "BR", "CL", "CO", "EC", "GY", "PY", "PE", "SR", "UY", "VE"}
AMERICAS = NORTH_AMERICA | SOUTH_AMERICA
OCEANIA = {"AU", "FJ", "NZ", "PG"}

# Order here is display order in the dashboard's Region dropdown.
REGION_TAGS: dict[str, set[str]] = {
    "Southeast Asia": SOUTHEAST_ASIA,
    "ASEAN": ASEAN,
    "South Asia": SOUTH_ASIA,
    "East Asia": EAST_ASIA,
    "Central Asia": CENTRAL_ASIA,
    "Middle East": MIDDLE_EAST,
    "Asia": ASIA,
    "Europe": EUROPE,
    "Africa": AFRICA,
    "North America": NORTH_AMERICA,
    "South America": SOUTH_AMERICA,
    "Americas": AMERICAS,
    "Oceania": OCEANIA,
}

FILTER_OPTIONS = [{"value": "all", "label": "All regions"}] + [
    {"value": tag, "label": tag} for tag in REGION_TAGS
]


def regions_for(code: str | None) -> list[str]:
    if not code:
        return []
    code = code.upper()
    return [tag for tag, codes in REGION_TAGS.items() if code in codes]
