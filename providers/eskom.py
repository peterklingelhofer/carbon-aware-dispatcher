"""Eskom provider: South African power grid, free, no API key.

Covers South Africa's national grid operated by Eskom.
Uses Eskom's public data portal for generation mix information.
South Africa's grid is heavily coal-dependent (~80-85% coal).

Data source: https://www.eskom.co.za/dataportal/
"""

import requests

from providers.base import FOSSIL_AVG_INTENSITY, DEFAULT_TIMEOUT

# Eskom supply/demand data endpoint
ESKOM_API = "https://www.eskom.co.za/dataportal/wp-content/uploads/2023/generation.json"

# Alternative: EskomSePush status endpoint (no key needed for basic status)
ESKOM_STATUS_API = "https://loadshedding.eskom.co.za/LoadShedding/GetStatus"

# Eskom zone identifiers
ESKOM_ZONES = {"ZA"}

# South Africa's grid emission factors (gCO2eq/kWh)
# SA grid is ~85% coal, 5% nuclear, 5% wind/solar, 5% other
SA_EMISSION_FACTORS = {
    "coal": 820,
    "nuclear": 0,
    "hydro": 0,
    "pumped_storage": 0,
    "wind": 0,
    "solar": 0,
    "gas": 490,
    "diesel": 650,
    "other": 200,
}

# Known SA grid characteristics for estimation when API is unavailable.
# SA is ~85% coal with some nuclear and renewables.
# Typical intensity: 700-900 gCO2eq/kWh, one of the dirtiest grids globally.
SA_DEFAULT_INTENSITY = 750
SA_NUCLEAR_PCT = 0.05
SA_RENEWABLE_PCT = 0.10  # Wind + solar combined


def _fetch_generation_data():
    """Fetch current generation data from Eskom.

    Returns parsed JSON or None on error.
    """
    try:
        response = requests.get(
            ESKOM_API,
            timeout=DEFAULT_TIMEOUT,
            headers={"Accept": "application/json"},
        )
    except requests.RequestException as exc:
        print(f"::warning::Eskom API error: {exc}")
        return None

    if response.status_code != 200:
        # Eskom's data portal URLs change frequently.
        # Fall back to estimation.
        return None

    try:
        return response.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        return None


def _estimate_intensity(data):
    """Calculate carbon intensity from Eskom generation data.

    If API data is available, use it. Otherwise estimate from known
    grid characteristics.

    Returns intensity in gCO2eq/kWh.
    """
    if data is not None:
        total_gen = 0
        weighted_emissions = 0

        if isinstance(data, dict):
            items = data.items()
        elif isinstance(data, list) and data:
            items = []
            for entry in data:
                if isinstance(entry, dict):
                    for key, val in entry.items():
                        items.append((key, val))
        else:
            items = []

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

            factor = 200
            for fuel_key, fuel_factor in SA_EMISSION_FACTORS.items():
                if fuel_key in key_lower:
                    factor = fuel_factor
                    break
            total_gen += gen_mw
            weighted_emissions += gen_mw * factor

        if total_gen > 0:
            return round(weighted_emissions / total_gen)

    # Estimation from known grid characteristics
    # SA: ~85% coal (820), ~5% nuclear (0), ~10% renewables (0)
    coal_pct = 1.0 - SA_NUCLEAR_PCT - SA_RENEWABLE_PCT
    estimated = round(coal_pct * 820)
    return estimated


def check_carbon_intensity(zone, max_carbon):
    """Check carbon intensity for South Africa.

    Returns (is_green, intensity) or (None, None) on error.
    Note: SA grid is typically 700-900 gCO2eq/kWh, rarely "green".
    """
    if zone not in ESKOM_ZONES:
        print(f"::warning::Unknown Eskom zone: {zone}. Valid zones: ZA")
        return None, None

    print(f"Checking carbon intensity for zone: {zone} (Eskom South Africa)...")
    data = _fetch_generation_data()

    # Even if API fails, we can estimate from known grid characteristics
    intensity = _estimate_intensity(data)

    if data is None:
        print(f"  Note: Using estimated intensity for SA grid "
              f"(API unavailable, using known grid mix)")

    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


def get_history_trend(zone):
    """Eskom has no trend data in the public API.

    Returns None.
    """
    return None


def get_forecast(zone, max_carbon):
    """Eskom forecast: not available via the public API.

    Returns (None, None).
    """
    return None, None
