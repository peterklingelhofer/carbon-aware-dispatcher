"""ONS Brazil provider: Brazilian National Grid Operator, free, no API key.

Covers Brazil's interconnected power system (SIN) with real-time
generation data by source type.
Uses ONS Integra API for energy balance data.
No authentication required.

Data source: https://integra.ons.org.br/
"""

import requests

from providers.base import FOSSIL_AVG_INTENSITY, DEFAULT_TIMEOUT

# ONS real-time energy balance API
ONS_API = "https://integra.ons.org.br/api/energiaagora/GetBalancoEnergetico/null"

# Brazilian grid subsystems
ONS_REGIONS = {
    "BR-S": "Sul",
    "BR-SE": "Sudeste",
    "BR-CS": "Sudeste",       # Alias for Centro-Sudeste
    "BR-NE": "Nordeste",
    "BR-N": "Norte",
}

# Generation source -> emission factors (gCO2eq/kWh)
BRAZIL_EMISSION_FACTORS = {
    "hidraulica": 0,     # Hydro (dominant source ~60-70%)
    "termica": 490,      # Thermal (gas + coal + biomass mix)
    "eolica": 0,         # Wind
    "solar": 0,          # Solar
    "nuclear": 0,        # Nuclear (Angra 1 & 2)
    "importacao": 200,   # Import (estimated)
}


def _fetch_energy_balance():
    """Fetch current energy balance from ONS API.

    Returns parsed JSON or None on error.
    """
    try:
        response = requests.get(
            ONS_API,
            timeout=DEFAULT_TIMEOUT,
            headers={"Accept": "application/json"},
        )
    except requests.RequestException as exc:
        print(f"::warning::ONS Brazil API error: {exc}")
        return None

    if response.status_code != 200:
        print(f"::warning::ONS Brazil API returned {response.status_code}: "
              f"{response.text[:200]}")
        return None

    try:
        return response.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        print(f"::warning::Invalid JSON from ONS API: {response.text[:200]}")
        return None


def _parse_energy_balance(data):
    """Parse ONS energy balance into generation by source.

    ONS returns an object with generation values by source.
    Returns dict of {source: mw_value} or None.
    """
    if data is None:
        return None

    result = {}

    # The ONS API may return data in various formats depending on version.
    # Handle both direct object and nested list formats.
    if isinstance(data, dict):
        for key, value in data.items():
            key_lower = key.lower().strip()
            if isinstance(value, (int, float)) and value > 0:
                result[key_lower] = value
            elif isinstance(value, dict):
                # Nested structure: extract generation value
                gen = value.get("geracao") or value.get("valor") or value.get("total")
                if isinstance(gen, (int, float)) and gen > 0:
                    result[key_lower] = gen
    elif isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                source = (entry.get("combustivel") or entry.get("fonte")
                          or entry.get("tipo") or "").lower().strip()
                gen = (entry.get("geracao") or entry.get("valor")
                       or entry.get("total") or 0)
                if isinstance(gen, (int, float)) and gen > 0 and source:
                    result[source] = gen

    return result if result else None


def _calculate_intensity(generation):
    """Calculate carbon intensity from generation mix.

    Returns intensity in gCO2eq/kWh, or None.
    """
    total_gen = 0
    weighted_emissions = 0

    for source, gen_mw in generation.items():
        # Match source name to emission factor
        factor = 200  # Default for unknown sources
        for fuel_key, fuel_factor in BRAZIL_EMISSION_FACTORS.items():
            if fuel_key in source:
                factor = fuel_factor
                break
        total_gen += gen_mw
        weighted_emissions += gen_mw * factor

    if total_gen <= 0:
        return None

    return round(weighted_emissions / total_gen)


def check_carbon_intensity(zone, max_carbon):
    """Check carbon intensity for a Brazilian grid region.

    Returns (is_green, intensity) or (None, None) on error.
    """
    if zone not in ONS_REGIONS:
        print(f"::warning::Unknown ONS Brazil zone: {zone}. "
              f"Valid zones: {', '.join(ONS_REGIONS.keys())}")
        return None, None

    print(f"Checking carbon intensity for zone: {zone} (ONS Brazil)...")
    data = _fetch_energy_balance()
    if data is None:
        print(f"  ONS Brazil API may be temporarily unavailable. "
              f"Data source: https://integra.ons.org.br/")
        return None, None

    generation = _parse_energy_balance(data)
    if generation is None:
        print(f"::warning::Could not parse ONS energy balance for {zone}. "
              f"API format may have changed. Check https://integra.ons.org.br/")
        return None, None

    intensity = _calculate_intensity(generation)
    if intensity is None:
        return None, None

    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


def get_history_trend(zone):
    """ONS Brazil has no trend data in the public API.

    Returns None.
    """
    return None


def get_forecast(zone, max_carbon):
    """ONS Brazil forecast: not available via the public API.

    Returns (None, None).
    """
    return None, None
