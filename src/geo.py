"""ISO 3166-1 alpha-2 country lookup: name + approximate centroid.

Outage sources (IODA, Cloudflare Radar, ACLED country field, #KeepItOn) key
events by country code/name rather than lat/lon. For the bubble map we place
country-level events at the country's centroid; region/ASN-level precision is
a known limitation (see README "Limitations").
"""
from __future__ import annotations

# code -> (name, lat, lon)
COUNTRIES: dict[str, tuple[str, float, float]] = {
    "AF": ("Afghanistan", 33.94, 67.71), "AL": ("Albania", 41.15, 20.17),
    "DZ": ("Algeria", 28.03, 1.66), "AO": ("Angola", -11.20, 17.87),
    "AR": ("Argentina", -38.42, -63.62), "AM": ("Armenia", 40.07, 45.04),
    "AU": ("Australia", -25.27, 133.78), "AT": ("Austria", 47.52, 14.55),
    "AZ": ("Azerbaijan", 40.14, 47.58), "BD": ("Bangladesh", 23.68, 90.36),
    "BY": ("Belarus", 53.71, 27.95), "BE": ("Belgium", 50.50, 4.47),
    "BJ": ("Benin", 9.31, 2.32), "BT": ("Bhutan", 27.51, 90.43),
    "BO": ("Bolivia", -16.29, -63.59), "BA": ("Bosnia and Herzegovina", 43.92, 17.68),
    "BW": ("Botswana", -22.33, 24.68), "BR": ("Brazil", -14.24, -51.93),
    "BN": ("Brunei", 4.54, 114.73), "BG": ("Bulgaria", 42.73, 25.49),
    "BF": ("Burkina Faso", 12.24, -1.56), "BI": ("Burundi", -3.37, 29.92),
    "KH": ("Cambodia", 12.57, 104.99), "CM": ("Cameroon", 7.37, 12.35),
    "CA": ("Canada", 56.13, -106.35), "CF": ("Central African Republic", 6.61, 20.94),
    "TD": ("Chad", 15.45, 18.73), "CL": ("Chile", -35.68, -71.54),
    "CN": ("China", 35.86, 104.20), "CO": ("Colombia", 4.57, -74.30),
    "CD": ("DR Congo", -4.04, 21.76), "CG": ("Congo", -0.23, 15.83),
    "CR": ("Costa Rica", 9.75, -83.75), "CI": ("Cote d'Ivoire", 7.54, -5.55),
    "HR": ("Croatia", 45.10, 15.20), "CU": ("Cuba", 21.52, -77.78),
    "CY": ("Cyprus", 35.13, 33.43), "CZ": ("Czechia", 49.82, 15.47),
    "DK": ("Denmark", 56.26, 9.50), "DJ": ("Djibouti", 11.83, 42.59),
    "DO": ("Dominican Republic", 18.74, -70.16), "EC": ("Ecuador", -1.83, -78.18),
    "EG": ("Egypt", 26.82, 30.80), "SV": ("El Salvador", 13.79, -88.90),
    "GQ": ("Equatorial Guinea", 1.65, 10.27), "ER": ("Eritrea", 15.18, 39.78),
    "EE": ("Estonia", 58.60, 25.01), "SZ": ("Eswatini", -26.52, 31.47),
    "ET": ("Ethiopia", 9.15, 40.49), "FJ": ("Fiji", -17.71, 178.07),
    "FI": ("Finland", 61.92, 25.75), "FR": ("France", 46.23, 2.21),
    "GA": ("Gabon", -0.80, 11.61), "GM": ("Gambia", 13.44, -15.31),
    "GE": ("Georgia", 42.32, 43.36), "DE": ("Germany", 51.17, 10.45),
    "GH": ("Ghana", 7.95, -1.02), "GR": ("Greece", 39.07, 21.82),
    "GT": ("Guatemala", 15.78, -90.23), "GN": ("Guinea", 9.95, -9.70),
    "GW": ("Guinea-Bissau", 11.80, -15.18), "GY": ("Guyana", 4.86, -58.93),
    "HT": ("Haiti", 18.97, -72.29), "HN": ("Honduras", 15.20, -86.24),
    "HK": ("Hong Kong", 22.32, 114.17), "HU": ("Hungary", 47.16, 19.50),
    "IS": ("Iceland", 64.96, -19.02), "IN": ("India", 20.59, 78.96),
    "ID": ("Indonesia", -0.79, 113.92), "IR": ("Iran", 32.43, 53.69),
    "IQ": ("Iraq", 33.22, 43.68), "IE": ("Ireland", 53.41, -8.24),
    "IL": ("Israel", 31.05, 34.85), "IT": ("Italy", 41.87, 12.57),
    "JM": ("Jamaica", 18.11, -77.30), "JP": ("Japan", 36.20, 138.25),
    "JO": ("Jordan", 30.59, 36.24), "KZ": ("Kazakhstan", 48.02, 66.92),
    "KE": ("Kenya", -0.02, 37.91), "KW": ("Kuwait", 29.31, 47.48),
    "KG": ("Kyrgyzstan", 41.20, 74.77), "LA": ("Laos", 19.86, 102.50),
    "LV": ("Latvia", 56.88, 24.60), "LB": ("Lebanon", 33.85, 35.86),
    "LS": ("Lesotho", -29.61, 28.23), "LR": ("Liberia", 6.43, -9.43),
    "LY": ("Libya", 26.34, 17.23), "LT": ("Lithuania", 55.17, 23.88),
    "LU": ("Luxembourg", 49.82, 6.13), "MG": ("Madagascar", -18.77, 46.87),
    "MW": ("Malawi", -13.25, 34.30), "MY": ("Malaysia", 4.21, 101.98),
    "ML": ("Mali", 17.57, -4.00), "MR": ("Mauritania", 21.01, -10.94),
    "MX": ("Mexico", 23.63, -102.55), "MD": ("Moldova", 47.41, 28.37),
    "MN": ("Mongolia", 46.86, 103.85), "ME": ("Montenegro", 42.71, 19.37),
    "MA": ("Morocco", 31.79, -7.09), "MZ": ("Mozambique", -18.67, 35.53),
    "MM": ("Myanmar", 21.91, 95.96), "NA": ("Namibia", -22.96, 18.49),
    "NP": ("Nepal", 28.39, 84.12), "NL": ("Netherlands", 52.13, 5.29),
    "NZ": ("New Zealand", -40.90, 174.89), "NI": ("Nicaragua", 12.87, -85.21),
    "NE": ("Niger", 17.61, 8.08), "NG": ("Nigeria", 9.08, 8.68),
    "KP": ("North Korea", 40.34, 127.51), "MK": ("North Macedonia", 41.61, 21.75),
    "NO": ("Norway", 60.47, 8.47), "OM": ("Oman", 21.51, 55.92),
    "PK": ("Pakistan", 30.38, 69.35), "PS": ("Palestine", 31.95, 35.23),
    "PA": ("Panama", 8.54, -80.78), "PG": ("Papua New Guinea", -6.31, 143.96),
    "PY": ("Paraguay", -23.44, -58.44), "PE": ("Peru", -9.19, -75.02),
    "PH": ("Philippines", 12.88, 121.77), "PL": ("Poland", 51.92, 19.15),
    "PT": ("Portugal", 39.40, -8.22), "QA": ("Qatar", 25.35, 51.18),
    "RO": ("Romania", 45.94, 24.97), "RU": ("Russia", 61.52, 105.32),
    "RW": ("Rwanda", -1.94, 29.87), "SA": ("Saudi Arabia", 23.89, 45.08),
    "SN": ("Senegal", 14.50, -14.45), "RS": ("Serbia", 44.02, 21.01),
    "SL": ("Sierra Leone", 8.46, -11.78), "SG": ("Singapore", 1.35, 103.82),
    "SK": ("Slovakia", 48.67, 19.70), "SI": ("Slovenia", 46.15, 14.99),
    "SO": ("Somalia", 5.15, 46.20), "ZA": ("South Africa", -30.56, 22.94),
    "KR": ("South Korea", 35.91, 127.77), "SS": ("South Sudan", 6.88, 31.31),
    "ES": ("Spain", 40.46, -3.75), "LK": ("Sri Lanka", 7.87, 80.77),
    "SD": ("Sudan", 12.86, 30.22), "SR": ("Suriname", 3.92, -56.03),
    "SE": ("Sweden", 60.13, 18.64), "CH": ("Switzerland", 46.82, 8.23),
    "SY": ("Syria", 34.80, 38.997), "TW": ("Taiwan", 23.70, 120.96),
    "TJ": ("Tajikistan", 38.86, 71.28), "TZ": ("Tanzania", -6.37, 34.89),
    "TH": ("Thailand", 15.87, 100.99), "TL": ("Timor-Leste", -8.87, 125.73),
    "TG": ("Togo", 8.62, 0.82), "TT": ("Trinidad and Tobago", 10.69, -61.22),
    "TN": ("Tunisia", 33.89, 9.54), "TR": ("Turkey", 38.96, 35.24),
    "TM": ("Turkmenistan", 38.97, 59.56), "UG": ("Uganda", 1.37, 32.29),
    "UA": ("Ukraine", 48.38, 31.17), "AE": ("United Arab Emirates", 23.42, 53.85),
    "GB": ("United Kingdom", 55.38, -3.44), "US": ("United States", 37.09, -95.71),
    "UY": ("Uruguay", -32.52, -55.77), "UZ": ("Uzbekistan", 41.38, 64.59),
    "VE": ("Venezuela", 6.42, -66.59), "VN": ("Vietnam", 14.06, 108.28),
    "YE": ("Yemen", 15.55, 48.52), "ZM": ("Zambia", -13.13, 27.85),
    "ZW": ("Zimbabwe", -19.02, 29.15), "XK": ("Kosovo", 42.60, 20.90),
}


def lookup(code: str) -> tuple[str, float, float] | None:
    return COUNTRIES.get((code or "").upper())


def name_for(code: str) -> str:
    hit = lookup(code)
    return hit[0] if hit else code


# name -> code, for callers that only have the display name an event was
# stored with (events.country) and need the ISO code back (e.g. to resolve
# region tags — see src/regions.py).
NAME_TO_CODE: dict[str, str] = {name: code for code, (name, _lat, _lon) in COUNTRIES.items()}


def code_for_name(name: str) -> str | None:
    return NAME_TO_CODE.get(name)


# External sources (GDACS, OpenSky's origin_country, and likely ACLED/others
# once configured) don't consistently use this project's canonical ISO
# short names — "Russian Federation" vs our "Russia", "The Democratic
# Republic of Congo" vs our "DR Congo", etc. Found by directly inspecting
# real cause_events/events country values after a live run and comparing
# the two sets (see attribution.py join, which is exact-string-match on
# country — a naming mismatch here silently drops a real match, not a
# genuine absence of correlation). One shared alias table + resolver so
# every connector normalizes into the same canonical names, instead of
# each maintaining its own list (src/ingestion/opensky.py used to).
EXTERNAL_NAME_ALIASES: dict[str, str] = {
    "Russian Federation": "Russia",
    "Czech Republic": "Czechia",
    "Ivory Coast": "Cote d'Ivoire",
    "Macedonia": "North Macedonia",
    "Democratic Republic of the Congo": "DR Congo",
    "The Democratic Republic of Congo": "DR Congo",
    "DR of the Congo": "DR Congo",
    "Republic of the Congo": "Congo",
    "Myanmar (Burma)": "Myanmar",
    "Syrian Arab Republic": "Syria",
    "Republic of Korea": "South Korea",
    "Korea, Republic of": "South Korea",
    "Democratic People's Republic of Korea": "North Korea",
    "Viet Nam": "Vietnam",
    "Lao People's Democratic Republic": "Laos",
    "United Republic of Tanzania": "Tanzania",
    "United States of America": "United States",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
}


def resolve_name(raw_name: str | None) -> tuple[str, float, float] | None:
    """Best-effort match of an external source's country string to our
    canonical (name, lat, lon): exact name -> known alias -> case-insensitive
    exact match, in that order. Returns None (rather than guessing further)
    for multi-country strings ("Bosnia and Herzegovina, Croatia" — split
    that yourself before calling, one call per country) and ocean/region
    labels GDACS uses for offshore earthquakes ("Northern Molucca Sea") that
    have no single owning country to attribute to."""
    if not raw_name:
        return None
    name = raw_name.strip()
    code = code_for_name(name)
    if code:
        return COUNTRIES[code]
    aliased = EXTERNAL_NAME_ALIASES.get(name)
    if aliased:
        code = code_for_name(aliased)
        if code:
            return COUNTRIES[code]
    lowered = name.lower()
    for c, (n, lat, lon) in COUNTRIES.items():
        if n.lower() == lowered:
            return (n, lat, lon)
    return None
