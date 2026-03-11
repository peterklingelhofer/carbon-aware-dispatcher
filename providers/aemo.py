"""AEMO provider — Australian National Electricity Market (NEM), free, no API key.

Covers 5 NEM regions: NSW, QLD, VIC, SA, TAS.
Uses the AEMO visualisation API for real-time fuel mix data.
Updates every 5 minutes. No authentication required.
"""

import requests

from providers.base import FOSSIL_AVG_INTENSITY, compute_trend, DEFAULT_TIMEOUT

AEMO_FUEL_API = "https://visualisations.aemo.com.au/aemo/apps/api/report/FUEL"

# AEMO region codes → display names
AEMO_REGIONS = {
    "AU-NSW": "NSW1",
    "AU-QLD": "QLD1",
    "AU-VIC": "VIC1",
    "AU-SA": "SA1",
    "AU-TAS": "TAS1",
}

# AEMO fuel types → emission factors (gCO2eq/kWh)
AEMO_EMISSION_FACTORS = {
    "Black Coal": 820,
    "Brown Coal": 900,
    "Natural Gas": 490,
    "Gas": 490,
    "Liquid Fuel": 650,
    "Diesel": 650,
    "Oil": 650,
    "Solar": 0,
    "Wind": 0,
    "Hydro": 0,
    "Battery": 0,
    "Biomass": 200,
    "Other": 200,
}


def _fetch_fuel_data():
    """Fetch current fuel mix data from AEMO API.

    Returns the parsed JSON list or None on error.
    """
    try:
        response = requests.post(
            AEMO_FUEL_API,
            json={"type": ["CURRENT"]},
            timeout=DEFAULT_TIMEOUT,
            headers={"Content-Type": "application/json"},
        )
    except requests.RequestException as exc:
        print(f"::warning::AEMO API error: {exc}")
        return None

    if response.status_code != 200:
        print(f"::warning::AEMO API returned {response.status_code}: {response.text[:200]}")
        return None

    try:
        return response.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        print(f"::warning::Invalid JSON from AEMO API: {response.text[:200]}")
        return None


def _fuel_mix_to_intensity(fuel_data, region_code):
    """Calculate carbon intensity from AEMO fuel mix data for a specific region.

    Returns intensity in gCO2eq/kWh, or None if insufficient data.
    """
    total_gen = 0
    weighted_emissions = 0

    for entry in fuel_data:
        entry_region = entry.get("REGIONID", "")
        if entry_region != region_code:
            continue

        fuel_type = entry.get("FUELTYPE", "")
        gen_mw = entry.get("GEN_MW", 0)

        if gen_mw is None or gen_mw <= 0:
            continue

        factor = AEMO_EMISSION_FACTORS.get(fuel_type, 200)
        total_gen += gen_mw
        weighted_emissions += gen_mw * factor

    if total_gen <= 0:
        return None

    return round(weighted_emissions / total_gen)


def check_carbon_intensity(zone, max_carbon):
    """Check carbon intensity for an Australian NEM region.

    Returns (is_green, intensity) or (None, None) on error.
    """
    region_code = AEMO_REGIONS.get(zone)
    if region_code is None:
        print(f"::warning::Unknown AEMO zone: {zone}. "
              f"Valid zones: {', '.join(AEMO_REGIONS.keys())}")
        return None, None

    print(f"Checking carbon intensity for zone: {zone} (AEMO NEM)...")
    fuel_data = _fetch_fuel_data()
    if fuel_data is None:
        return None, None

    intensity = _fuel_mix_to_intensity(fuel_data, region_code)
    if intensity is None:
        print(f"::warning::No generation data for region {region_code}")
        return None, None

    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


def get_history_trend(zone):
    """Compute trend from AEMO data.

    AEMO's FUEL API with CURRENT type returns recent 5-min snapshots.
    We use the last several data points to compute a trend.
    Returns one of: "decreasing", "increasing", "stable", or None.
    """
    region_code = AEMO_REGIONS.get(zone)
    if region_code is None:
        return None

    fuel_data = _fetch_fuel_data()
    if fuel_data is None:
        return None

    # Group entries by settlement period to get per-period intensities
    periods = {}
    for entry in fuel_data:
        if entry.get("REGIONID") != region_code:
            continue
        period = entry.get("SETTLEMENTDATE", "")
        if period not in periods:
            periods[period] = {"total_gen": 0, "weighted_emissions": 0}
        gen_mw = entry.get("GEN_MW", 0)
        if gen_mw is None or gen_mw <= 0:
            continue
        fuel_type = entry.get("FUELTYPE", "")
        factor = AEMO_EMISSION_FACTORS.get(fuel_type, 200)
        periods[period]["total_gen"] += gen_mw
        periods[period]["weighted_emissions"] += gen_mw * factor

    # Calculate intensity per period, sorted by time
    points = []
    for period in sorted(periods.keys()):
        data = periods[period]
        if data["total_gen"] > 0:
            points.append(round(data["weighted_emissions"] / data["total_gen"]))

    return compute_trend(points)


def get_forecast(zone, max_carbon):
    """AEMO forecast — not available via the free visualisation API.

    Returns (None, None). AEMO provides pre-dispatch forecasts via a
    different API that requires MMS access.
    """
    return None, None
