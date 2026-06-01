"""ONS Brazil provider — Brazilian National Grid Operator, free, no API key.

Covers Brazil's interconnected power system (SIN) with real-time
generation data by source type.
Uses ONS Integra API for energy balance data.
No authentication required.

Data source: https://integra.ons.org.br/
"""

from providers.base import request

# ONS real-time energy balance API
ONS_API = "https://integra.ons.org.br/api/energiaagora/GetBalancoEnergetico/null"

# Brazilian grid subsystems
ONS_REGIONS = {
    "BR-S": "Sul",
    "BR-SE": "Sudeste",
    "BR-CS": "Sudeste",  # Alias for Centro-Sudeste
    "BR-NE": "Nordeste",
    "BR-N": "Norte",
}

# Generation source → emission factors (gCO2eq/kWh)
# IPCC AR5 (2014) lifecycle median gCO2eq/kWh
BRAZIL_EMISSION_FACTORS = {
    "hidraulica": 24,  # Hydro (dominant source ~60-70%)
    "termica": 650,  # Thermal (gas + coal + biomass mix, oil-class median)
    "eolica": 12,  # Wind
    "solar": 45,  # Solar
    "nuclear": 12,  # Nuclear (Angra 1 & 2)
    "importacao": 300,  # Import (estimated)
}

# Fallback factor for unknown fuel types (warned about, then applied)
DEFAULT_FUEL_FACTOR = 300


def _fetch_energy_balance():
    """Fetch current energy balance from ONS API.

    Returns parsed JSON or None on error.
    """
    return request(
        ONS_API,
        headers={"Accept": "application/json"},
        parse="json",
    )


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
                # Nested structure — extract generation value
                gen = value.get("geracao") or value.get("valor") or value.get("total")
                if isinstance(gen, (int, float)) and gen > 0:
                    result[key_lower] = gen
    elif isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict):
                source = (
                    (entry.get("combustivel") or entry.get("fonte") or entry.get("tipo") or "")
                    .lower()
                    .strip()
                )
                gen = entry.get("geracao") or entry.get("valor") or entry.get("total") or 0
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
        factor = None
        for fuel_key, fuel_factor in BRAZIL_EMISSION_FACTORS.items():
            if fuel_key in source:
                factor = fuel_factor
                break
        if factor is None:
            print(
                f"::warning::Unknown fuel type '{source}', using fallback "
                f"{DEFAULT_FUEL_FACTOR} gCO2eq/kWh"
            )
            factor = DEFAULT_FUEL_FACTOR
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
        print(
            f"::warning::Unknown ONS Brazil zone: {zone}. "
            f"Valid zones: {', '.join(ONS_REGIONS.keys())}"
        )
        return None, None

    print(f"Checking carbon intensity for zone: {zone} (ONS Brazil)...")
    data = _fetch_energy_balance()
    if data is None:
        print(
            "  ONS Brazil API may be temporarily unavailable. "
            "Data source: https://integra.ons.org.br/"
        )
        return None, None

    generation = _parse_energy_balance(data)
    if generation is None:
        print(
            f"::warning::Could not parse ONS energy balance for {zone}. "
            f"API format may have changed. Check https://integra.ons.org.br/"
        )
        return None, None

    intensity = _calculate_intensity(generation)
    if intensity is None:
        return None, None

    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


def get_history_trend(zone):
    """ONS Brazil trend — not available via the public API.

    Returns None.
    """
    return None


def get_forecast(zone, max_carbon):
    """Estimate future green windows from Brazil's known generation patterns.

    Brazil's grid is ~65-75% hydro, making it one of the cleanest large grids.
    South (BR-S) and North (BR-N) are hydro-dominated (~80-100 gCO2eq/kWh).
    Northeast (BR-NE) has strong wind generation, especially at night.
    Thermal dispatch increases during evening peak (17:00-21:00 BRT).
    BRT = UTC-3.

    Returns (forecast_green_at, forecast_intensity) or (None, None).
    """
    from datetime import datetime, timedelta, timezone

    now_utc = datetime.now(timezone.utc)
    brt = timezone(timedelta(hours=-3))
    now_brt = now_utc.astimezone(brt)
    local_hour = now_brt.hour

    # Off-peak (22:00-16:00): mostly hydro, very clean
    # Evening peak (17:00-21:00): thermal plants dispatched, dirtier
    hydro_zones = {"BR-S", "BR-N", "BR-CS"}
    is_hydro_heavy = zone in hydro_zones

    offpeak_intensity = 80 if is_hydro_heavy else 120
    peak_intensity = 150 if is_hydro_heavy else 200

    # If already in off-peak and green, no forecast needed
    if not (17 <= local_hour <= 21):
        if offpeak_intensity <= max_carbon:
            return None, None

    # Find next green window
    for hours_ahead in range(1, 49):
        future = now_brt + timedelta(hours=hours_ahead)
        future_hour = future.hour

        if 17 <= future_hour <= 21:
            est_intensity = peak_intensity
        else:
            est_intensity = offpeak_intensity

        if est_intensity <= max_carbon:
            future_utc = future.astimezone(timezone.utc)
            dt_str = future_utc.strftime("%Y-%m-%dT%H:00Z")
            print(
                f"  Forecast: Brazil grid ~{est_intensity} gCO2eq/kWh at {dt_str} "
                f"(heuristic based on hydro/thermal dispatch patterns)"
            )
            return dt_str, est_intensity

    print(f"  Forecast: no estimated green window in 48h for {zone}.")
    return "none_in_forecast", None
