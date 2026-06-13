"""AEMO (Australian Energy Market Operator) provider — free, no auth.

Covers the five National Electricity Market (NEM) regions of eastern
Australia. Data from AEMO's public visualisations API.
"""

from providers.base import DEFAULT_FUEL_FACTOR, FUEL_FACTORS, request

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


def _fuel_mix_to_intensity(fuel_data, region_code):
    """Calculate carbon intensity from AEMO fuel mix data for a region.

    Each live row looks like {"STATE": "NSW1", "FUEL_TYPE": "Black coal",
    "SUPPLY": 1234.5}. Older rows used REGIONID/FUELTYPE/GEN_MW, both are
    accepted. Returns intensity in gCO2eq/kWh, or None if no usable data.
    """
    if not fuel_data:
        return None

    total_gen = 0
    weighted_emissions = 0

    for entry in fuel_data:
        # The live AEMO feed can include non-dict rows, skip anything we
        # cannot read as a record
        if not isinstance(entry, dict):
            continue

        entry_region = entry.get("STATE") or entry.get("REGIONID", "")
        if entry_region != region_code:
            continue

        fuel_type = entry.get("FUEL_TYPE") or entry.get("FUELTYPE", "")
        supply = entry.get("SUPPLY")
        if supply is None:
            supply = entry.get("GEN_MW", 0)
        if supply is None or supply <= 0:
            continue

        key = fuel_type.strip().lower()
        # Storage discharge is not zero-carbon, so exclude it from the mix
        if key in AEMO_STORAGE_FUELS:
            continue
        if key in AEMO_EMISSION_FACTORS:
            factor = AEMO_EMISSION_FACTORS[key]
        else:
            print(
                f"::warning::Unknown fuel type '{fuel_type}', using fallback "
                f"{DEFAULT_FUEL_FACTOR} gCO2eq/kWh"
            )
            factor = DEFAULT_FUEL_FACTOR
        total_gen += supply
        weighted_emissions += supply * factor

    if total_gen <= 0:
        return None

    return round(weighted_emissions / total_gen)


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

    intensity = _fuel_mix_to_intensity(fuel_data, region_code)
    if intensity is None:
        print(f"::warning::No generation data for region {region_code}")
        return None, None

    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


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
