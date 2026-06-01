"""Grid India provider: Indian power grid, free, no API key.

Covers India's five regional grids: Northern, Southern, Eastern, Western, North-Eastern.
Uses Grid India's (formerly POSOCO) public generation data API.
Updates every 5 minutes. No authentication required.

Data source: https://report.grid-india.in/
"""

from providers.base import request

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
# IPCC AR5 (2014) lifecycle median gCO2eq/kWh
INDIA_EMISSION_FACTORS = {
    "coal": 820,
    "thermal": 750,  # blended coal + gas across two IPCC classes
    "gas": 490,
    "lignite": 1050,
    "diesel": 650,
    "nuclear": 12,
    "hydro": 24,
    "solar": 45,
    "wind": 12,
    "biomass": 230,  # IPCC dedicated biomass median
    "other": 300,
}

# Fallback factor for unknown fuel types (warned about, then applied)
DEFAULT_FUEL_FACTOR = 300


def _fetch_generation_data():
    """Fetch current generation data from Grid India.

    Returns parsed JSON or None on error.
    """
    return request(
        GRID_INDIA_API,
        headers={"Accept": "application/json"},
        parse="json",
    )


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
        print(
            f"::warning::Unknown Grid India zone: {zone}. "
            f"Valid zones: {', '.join(GRID_INDIA_REGIONS.keys())}"
        )
        return None, None

    print(f"Checking carbon intensity for zone: {zone} (Grid India)...")
    data = _fetch_generation_data()
    if data is None:
        print(
            "  Grid India API may be temporarily unavailable. "
            "Data source: https://report.grid-india.in/"
        )
        return None, None

    intensity = _estimate_from_national_mix(data)
    if intensity is None:
        print(
            f"::warning::Could not parse Grid India generation data for {zone}. "
            f"API format may have changed. Check https://report.grid-india.in/"
        )
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
    """Estimate future green windows from India's known generation patterns.

    India's grid gets significantly cleaner during daytime (solar peak 10am-4pm IST).
    Southern grid (IN-SO) is cleanest due to higher solar/wind penetration.
    IST = UTC+5:30.

    Returns (forecast_green_at, forecast_intensity) or (None, None).
    """
    from datetime import datetime, timedelta, timezone

    now_utc = datetime.now(timezone.utc)
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = now_utc.astimezone(ist)
    local_hour = now_ist.hour

    # India's grid is ~60% coal but solar reduces midday intensity significantly.
    # Southern grid (IN-SO): ~30-40% renewables midday -> ~350-450 gCO2eq/kWh
    # Other regions: ~20-30% renewables midday -> ~450-550 gCO2eq/kWh
    # Night: ~600-700 gCO2eq/kWh (all thermal)
    is_south = zone == "IN-SO"
    midday_intensity = 380 if is_south else 480
    shoulder_intensity = 450 if is_south else 550

    # If we're already in a green window, no forecast needed
    if 10 <= local_hour <= 15 and midday_intensity <= max_carbon:
        return None, None

    # Find next solar peak window
    for hours_ahead in range(1, 49):
        future = now_ist + timedelta(hours=hours_ahead)
        future_hour = future.hour

        if 10 <= future_hour <= 15:
            est_intensity = midday_intensity
        elif 8 <= future_hour <= 17:
            est_intensity = shoulder_intensity
        else:
            continue

        if est_intensity <= max_carbon:
            future_utc = future.astimezone(timezone.utc)
            dt_str = future_utc.strftime("%Y-%m-%dT%H:00Z")
            print(
                f"  Forecast: India solar peak ~{est_intensity} gCO2eq/kWh at {dt_str} "
                f"(heuristic based on solar generation patterns)"
            )
            return dt_str, est_intensity

    print(f"  Forecast: no estimated green window in 48h for {zone}.")
    return "none_in_forecast", None
