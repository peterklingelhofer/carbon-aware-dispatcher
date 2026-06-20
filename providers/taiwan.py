"""Taiwan grid carbon data via Taipower's real-time per-unit generation feed.

Free, no API key. Taipower publishes every operating unit's current output with
a fuel label (Chinese + English); we sum by fuel and apply emission factors.
Updates roughly every 10 minutes.
"""

import json
import re

from providers.base import FUEL_FACTORS, green_result, mix_to_intensity, request

API_URL = "https://www.taipower.com.tw/d006/loadGraph/loadGraph/data/genary.json"
TAIWAN_ZONES = {"TW"}

_TAG_RE = re.compile(r"<[^>]+>")

# Taipower returns an empty 202 unless the request looks like the dashboard's
# own XHR (Referer + X-Requested-With), so mirror those headers.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://www.taipower.com.tw/tc/page.aspx?mid=206",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

# Local fuel labels mapped to canonical factors (see providers.base.FUEL_FACTORS)
TAIWAN_EMISSION_FACTORS = {
    "coal": FUEL_FACTORS["coal"],
    "natural_gas": FUEL_FACTORS["gas"],
    "oil": FUEL_FACTORS["oil"],
    "nuclear": FUEL_FACTORS["nuclear"],
    "solar": FUEL_FACTORS["solar"],
    "wind": FUEL_FACTORS["wind"],
    "hydro": FUEL_FACTORS["hydro"],
    "geothermal": FUEL_FACTORS["geothermal"],
    "other": FUEL_FACTORS["other"],
}

# Storage is not primary generation, exclude from the weighted mix
TAIWAN_STORAGE_FUELS = {"battery"}


def _fuel_of(label):
    """Map a Taipower fuel label to a normalized fuel.

    Matches on the English name (most stable). Returns None for rows to skip.
    """
    if "Load" in label:
        return None  # storage charging is a load on the grid
    if "Energy Storage" in label:
        return "battery"
    if "Coal" in label:
        return "coal"
    if "LNG" in label or "Co-Gen" in label:
        return "natural_gas"
    if "Fuel Oil" in label:
        return "oil"
    if "Nuclear" in label:
        return "nuclear"
    if "Solar" in label:
        return "solar"
    if "Wind" in label:
        return "wind"
    if "Hydro" in label:
        return "hydro"
    if "Other Renewable" in label:
        return "geothermal"  # geothermal/biomass mix, counted renewable
    return "other"


def _parse_generation(raw_bytes):
    """Parse the Taipower genary feed bytes into a {fuel: MW} mix, or None.

    The feed is served with a UTF-8 BOM, so decode with utf-8-sig.
    """
    try:
        rows = json.loads(raw_bytes.decode("utf-8-sig")).get("aaData", [])
    except (ValueError, UnicodeDecodeError):
        return None

    fuel_mix = {}
    for r in rows:
        if len(r) < 5:
            continue
        fuel = _fuel_of(_TAG_RE.sub("", r[0]))
        if fuel is None:
            continue
        try:
            mw = float(r[4])
        except (TypeError, ValueError):
            continue
        if mw <= 0:
            continue
        fuel_mix[fuel] = fuel_mix.get(fuel, 0) + mw
    return fuel_mix or None


def check_carbon_intensity(zone, max_carbon):
    """Check carbon intensity for Taiwan via Taipower.

    Returns (is_green, intensity) or (None, None) on error.
    """
    if zone not in TAIWAN_ZONES:
        print(f"::warning::Unknown Taiwan zone: {zone}. Valid: TW")
        return None, None

    print(f"Checking carbon intensity for zone: {zone} (Taipower)...")
    response = request(API_URL, headers=_HEADERS, parse="response")
    if response is None or response.status_code != 200:
        print(f"::warning::No generation data from Taipower for zone {zone}")
        return None, None

    fuel_mix = _parse_generation(response.content)
    if not fuel_mix:
        print(f"::warning::Could not parse Taipower generation for zone {zone}")
        return None, None

    intensity = mix_to_intensity(fuel_mix, TAIWAN_EMISSION_FACTORS, TAIWAN_STORAGE_FUELS)
    if intensity is None:
        return None, None

    return green_result(zone, intensity, max_carbon)


def get_forecast(zone, max_carbon):
    """Taipower exposes only a current snapshot, no forecast endpoint.

    Returns (None, None).
    """
    return None, None


def get_history_trend(zone):
    """Taipower exposes no history endpoint, so no trend is available.

    Returns None.
    """
    return None
