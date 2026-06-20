"""AEMO (Australian Energy Market Operator) provider: free, no auth.

Covers the five National Electricity Market (NEM) regions of eastern
Australia. Data from AEMO's public visualisations API.
"""

from providers.base import FUEL_FACTORS, green_result, mix_to_intensity, request

# AEMO NEM region codes
AEMO_REGIONS = {
    "AU-NSW": "NSW1",
    "AU-QLD": "QLD1",
    "AU-VIC": "VIC1",
    "AU-SA": "SA1",
    "AU-TAS": "TAS1",
}

# Local fuel labels mapped to canonical factors (see providers.base.FUEL_FACTORS).
# Keyed by lowercased AEMO labels so "Black coal"/"Black Coal" both match.
AEMO_EMISSION_FACTORS = {
    "black coal": FUEL_FACTORS["coal"],
    "brown coal": FUEL_FACTORS["lignite"],
    "gas": FUEL_FACTORS["gas"],
    "liquid fuel": FUEL_FACTORS["oil"],
    "solar": FUEL_FACTORS["solar"],
    "wind": FUEL_FACTORS["wind"],
    "hydro": FUEL_FACTORS["hydro"],
    "biomass": FUEL_FACTORS["biomass"],
    "other": FUEL_FACTORS["other"],
}

# Storage discharge is not primary generation, exclude from the mix
AEMO_STORAGE_FUELS = {"battery", "battery (discharging)", "pump storage"}

AEMO_FUEL_API = "https://visualisations.aemo.com.au/aemo/apps/api/report/FUEL"


def _fetch_fuel_data():
    """Fetch current fuel mix rows from the AEMO API.

    The live API returns {"FUEL_CURRENT": [rows], ...}; this returns the
    FUEL_CURRENT list, or None on error or unexpected shape.
    """
    data = request(
        AEMO_FUEL_API,
        method="POST",
        json_body={"type": ["CURRENT"]},
        headers={"Content-Type": "application/json"},
        parse="json",
    )
    if isinstance(data, dict):
        return data.get("FUEL_CURRENT")
    # Older/alternate shape returned a bare list of rows
    if isinstance(data, list):
        return data
    return None


def _region_fuel_mix(fuel_data, region_code):
    """Sum one region's rows into a {fuel: MW} mix.

    Each live row looks like {"STATE": "NSW1", "FUEL_TYPE": "Black coal",
    "SUPPLY": 1234.5}. Older rows used REGIONID/FUELTYPE/GEN_MW, both are
    accepted. Non-dict rows and other regions are skipped.
    """
    mix: dict = {}
    for entry in fuel_data or []:
        if not isinstance(entry, dict):
            continue
        if (entry.get("STATE") or entry.get("REGIONID", "")) != region_code:
            continue
        fuel_type = entry.get("FUEL_TYPE") or entry.get("FUELTYPE", "")
        supply = entry.get("SUPPLY")
        if supply is None:
            supply = entry.get("GEN_MW", 0)
        if supply is None or supply <= 0:
            continue
        key = fuel_type.strip().lower()
        mix[key] = mix.get(key, 0.0) + supply
    return mix


def check_carbon_intensity(zone, max_carbon):
    """Check carbon intensity for an Australian NEM region.

    Returns (is_green, intensity) or (None, None) on error.
    """
    region_code = AEMO_REGIONS.get(zone)
    if region_code is None:
        print(
            f"::warning::Unknown AEMO zone: {zone}. Valid zones: {', '.join(AEMO_REGIONS.keys())}"
        )
        return None, None

    print(f"Checking carbon intensity for zone: {zone} (AEMO NEM)...")
    fuel_data = _fetch_fuel_data()
    if fuel_data is None:
        return None, None

    mix = _region_fuel_mix(fuel_data, region_code)
    intensity = mix_to_intensity(mix, AEMO_EMISSION_FACTORS, AEMO_STORAGE_FUELS)
    if intensity is None:
        print(f"::warning::No generation data for region {region_code}")
        return None, None

    return green_result(zone, intensity, max_carbon)


def get_forecast(zone, max_carbon):
    """AEMO exposes only a current snapshot, no forecast endpoint.

    Returns (None, None).
    """
    return None, None


def get_history_trend(zone):
    """AEMO snapshot has no history endpoint, so no trend is available.

    Returns None.
    """
    return None
