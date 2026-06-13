"""ONS Brazil provider: Brazilian National Grid Operator, free, no API key.

Covers Brazil's interconnected power system (SIN) with real-time
generation data by source type.
Uses ONS Integra API for energy balance data.
No authentication required.

Data source: https://integra.ons.org.br/
"""

from providers.base import DEFAULT_FUEL_FACTOR, FUEL_FACTORS, request

# ONS real-time energy balance API
ONS_API = "https://integra.ons.org.br/api/energiaagora/GetBalancoEnergetico/null"

# Brazilian grid subsystems mapped to the ONS API region keys. The live
# GetBalancoEnergetico response is {region_key: {"geracao": {fuel: MW}}}
ONS_REGIONS = {
    "BR-S": "sul",
    "BR-SE": "sudesteECentroOeste",
    "BR-CS": "sudesteECentroOeste",  # Alias for Centro-Sudeste
    "BR-NE": "nordeste",
    "BR-N": "norte",
}

# Local (Portuguese) fuel labels mapped to canonical factors (see
# providers.base.FUEL_FACTORS)
BRAZIL_EMISSION_FACTORS = {
    "hidraulica": FUEL_FACTORS["hydro"],  # dominant source ~60-70%
    "termica": FUEL_FACTORS["oil"],  # thermal mix, oil-class median
    "eolica": FUEL_FACTORS["wind"],
    "solar": FUEL_FACTORS["solar"],
    "nuclear": FUEL_FACTORS["nuclear"],  # Angra 1 & 2
    "importacao": FUEL_FACTORS["other"],  # import (estimated)
}


def _fetch_energy_balance():
    """Fetch current energy balance from ONS API.

    Returns parsed JSON or None on error.
    """
    return request(
        ONS_API,
        headers={"Accept": "application/json"},
        parse="json",
    )


def _parse_energy_balance(data, region_key):
    """Parse the ONS energy balance for one region into generation by source.

    The live API returns {region_key: {"geracao": {"total": x, "hidraulica":
    y, "termica": z, ...}}}. We read that region's geracao block and drop the
    "total" aggregate so only per-fuel values remain.
    Returns dict of {source: mw_value} or None.
    """
    if not isinstance(data, dict):
        return None

    region = data.get(region_key)
    if not isinstance(region, dict):
        return None

    geracao = region.get("geracao")
    if not isinstance(geracao, dict):
        return None

    result = {}
    for source, value in geracao.items():
        key = source.lower().strip()
        if key == "total":
            continue
        if isinstance(value, (int, float)) and value > 0:
            result[key] = value

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

    generation = _parse_energy_balance(data, ONS_REGIONS[zone])
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
    """ONS Brazil has no trend data in the public API.

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
