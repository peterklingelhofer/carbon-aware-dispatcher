"""Eskom provider: South African power grid, free, no API key.

Covers South Africa's national grid operated by Eskom.
Uses Eskom's public data portal for generation mix information.
South Africa's grid is heavily coal-dependent (~80-85% coal).

Data source: https://www.eskom.co.za/dataportal/
"""

from providers.base import request

# Eskom supply/demand data endpoint
ESKOM_API = "https://www.eskom.co.za/dataportal/wp-content/uploads/2023/generation.json"

# Alternative: EskomSePush status endpoint (no key needed for basic status)
ESKOM_STATUS_API = "https://loadshedding.eskom.co.za/LoadShedding/GetStatus"

# Eskom zone identifiers
ESKOM_ZONES = {"ZA"}

# South Africa's grid emission factors (gCO2eq/kWh)
# IPCC AR5 (2014) lifecycle median gCO2eq/kWh
# SA grid is ~85% coal, 5% nuclear, 5% wind/solar, 5% other
SA_EMISSION_FACTORS = {
    "coal": 820,
    "nuclear": 12,
    "hydro": 24,
    "wind": 12,
    "solar": 45,
    "gas": 490,
    "diesel": 650,
    "other": 300,
}

# Fuel name fragments that represent storage
# Pumped storage discharge is not zero-carbon, so it is excluded from the mix
SA_STORAGE_FUELS = ("pumped_storage", "pumped storage", "pumped")

# Fallback factor for unknown fuel types (warned about, then applied)
DEFAULT_FUEL_FACTOR = 300

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
    # Eskom's data portal URLs change frequently. On any failure we return
    # None and the caller falls back to estimation from known grid mix
    return request(
        ESKOM_API,
        headers={"Accept": "application/json"},
        parse="json",
    )


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

            # Storage discharge is not zero-carbon, so exclude it from the mix
            if any(s in key_lower for s in SA_STORAGE_FUELS):
                continue

            # keys here are arbitrary JSON fields, so
            # only count keys that map to a known fuel and skip the rest rather
            # than counting non-fuel fields at a fallback factor
            factor = None
            for fuel_key, fuel_factor in SA_EMISSION_FACTORS.items():
                if fuel_key in key_lower:
                    factor = fuel_factor
                    break
            if factor is None:
                continue
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
        print(
            "  Note: Using estimated intensity for SA grid (API unavailable, using known grid mix)"
        )

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
    """Estimate future green windows from South Africa's known generation patterns.

    SA's grid is ~85% coal with small contributions from nuclear, wind, and solar.
    Solar and wind reduce intensity slightly during daytime (10am-4pm SAST).
    Typical range: 650-800 gCO2eq/kWh. Rarely below 600.
    SAST = UTC+2.

    Returns (forecast_green_at, forecast_intensity) or (None, None).
    """
    from datetime import datetime, timedelta, timezone

    now_utc = datetime.now(timezone.utc)
    sast = timezone(timedelta(hours=2))
    now_sast = now_utc.astimezone(sast)

    # SA grid intensity by time of day (approximate):
    # Midday (10-16): ~650 gCO2eq/kWh (solar generation helps)
    # Off-peak night (22-06): ~700 gCO2eq/kWh (lower demand, but all thermal)
    # Evening peak (17-21): ~780 gCO2eq/kWh (peak demand, all thermal + diesel)
    midday_intensity = 650
    offpeak_intensity = 700
    peak_intensity = 780

    # Find next window below threshold
    for hours_ahead in range(1, 49):
        future = now_sast + timedelta(hours=hours_ahead)
        future_hour = future.hour

        if 10 <= future_hour <= 16:
            est_intensity = midday_intensity
        elif 17 <= future_hour <= 21:
            est_intensity = peak_intensity
        else:
            est_intensity = offpeak_intensity

        if est_intensity <= max_carbon:
            future_utc = future.astimezone(timezone.utc)
            dt_str = future_utc.strftime("%Y-%m-%dT%H:00Z")
            print(
                f"  Forecast: SA grid ~{est_intensity} gCO2eq/kWh at {dt_str} "
                f"(heuristic: SA grid is ~85% coal)"
            )
            return dt_str, est_intensity

    print(
        f"  Forecast: SA grid unlikely to go below {max_carbon} gCO2eq/kWh. "
        f"Consider using auto:escape-coal:ZA to route to clean alternatives."
    )
    return "none_in_forecast", None
