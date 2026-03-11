"""Grid India provider: Indian power grid, free, no API key.

Covers India's five regional grids: Northern, Southern, Eastern, Western, North-Eastern.
Uses Grid India's (formerly POSOCO) public generation data API.
Updates every 5 minutes. No authentication required.

Data source: https://report.grid-india.in/
"""

import requests

from providers.base import FOSSIL_AVG_INTENSITY, DEFAULT_TIMEOUT

# Grid India real-time generation overview API
GRID_INDIA_API = "https://report.grid-india.in/ReportData/Generation"

# Indian grid regions -> display names
GRID_INDIA_REGIONS = {
    "IN-NO": "Northern",
    "IN-SO": "Southern",
    "IN-EA": "Eastern",
    "IN-WE": "Western",
    "IN-NE": "North-Eastern",
}

# Generation source -> emission factors (gCO2eq/kWh)
INDIA_EMISSION_FACTORS = {
    "coal": 820,
    "thermal": 750,   # Mix of coal + gas
    "gas": 490,
    "lignite": 900,
    "diesel": 650,
    "nuclear": 0,
    "hydro": 0,
    "solar": 0,
    "wind": 0,
    "biomass": 200,
    "other": 200,
}


def _fetch_generation_data():
    """Fetch current generation data from Grid India.

    Returns parsed JSON or None on error.
    """
    try:
        response = requests.get(
            GRID_INDIA_API,
            timeout=DEFAULT_TIMEOUT,
            headers={"Accept": "application/json"},
        )
    except requests.RequestException as exc:
        print(f"::warning::Grid India API error: {exc}")
        return None

    if response.status_code != 200:
        print(f"::warning::Grid India API returned {response.status_code}: "
              f"{response.text[:200]}")
        return None

    try:
        return response.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        print(f"::warning::Invalid JSON from Grid India API: {response.text[:200]}")
        return None


def _estimate_from_national_mix(data):
    """Estimate carbon intensity from national generation mix data.

    Grid India returns generation by source type. We calculate weighted
    intensity from the fuel mix.

    Returns intensity in gCO2eq/kWh, or None if data is insufficient.
    """
    total_gen = 0
    weighted_emissions = 0

    # Try to extract generation by fuel type from the response
    # Grid India's API format may vary; we handle common structures
    if isinstance(data, dict):
        items = data.items()
    elif isinstance(data, list):
        # If it's a list of records, try to use them directly
        items = []
        for entry in data:
            if isinstance(entry, dict):
                for key, val in entry.items():
                    items.append((key, val))
    else:
        return None

    for key, value in items:
        key_lower = key.lower().strip() if isinstance(key, str) else ""
        gen_mw = 0
        if isinstance(value, (int, float)):
            gen_mw = value
        elif isinstance(value, str):
            try:
                gen_mw = float(value)
            except (ValueError, TypeError):
                continue

        if gen_mw <= 0:
            continue

        # Match key to emission factor
        factor = None
        for fuel_key, fuel_factor in INDIA_EMISSION_FACTORS.items():
            if fuel_key in key_lower:
                factor = fuel_factor
                break

        if factor is not None:
            total_gen += gen_mw
            weighted_emissions += gen_mw * factor

    if total_gen <= 0:
        return None

    return round(weighted_emissions / total_gen)


def check_carbon_intensity(zone, max_carbon):
    """Check carbon intensity for an Indian grid region.

    Returns (is_green, intensity) or (None, None) on error.
    """
    if zone not in GRID_INDIA_REGIONS:
        print(f"::warning::Unknown Grid India zone: {zone}. "
              f"Valid zones: {', '.join(GRID_INDIA_REGIONS.keys())}")
        return None, None

    print(f"Checking carbon intensity for zone: {zone} (Grid India)...")
    data = _fetch_generation_data()
    if data is None:
        print(f"  Grid India API may be temporarily unavailable. "
              f"Data source: https://report.grid-india.in/")
        return None, None

    intensity = _estimate_from_national_mix(data)
    if intensity is None:
        print(f"::warning::Could not parse Grid India generation data for {zone}. "
              f"API format may have changed. Check https://report.grid-india.in/")
        return None, None

    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


def get_history_trend(zone):
    """Grid India has no trend data in the public API.

    Returns None.
    """
    return None


def get_forecast(zone, max_carbon):
    """Grid India forecast: not available via the public API.

    Returns (None, None).
    """
    return None, None
