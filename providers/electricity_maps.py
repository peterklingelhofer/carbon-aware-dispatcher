"""Electricity Maps provider — global coverage, requires free API key.

Covers 200+ zones worldwide including Europe, Canada, India, Japan, Australia,
Latin America, and more. Direct carbon intensity values in gCO2eq/kWh.

Free tier: 50 requests/hour. Register at https://portal.electricitymaps.com/
"""

from providers.base import api_request_with_header, compute_trend

EMAPS_API_BASE = "https://api-access.electricitymaps.com/free-tier"


def check_carbon_intensity(zone, max_carbon, emaps_api_key):
    """Check carbon intensity using the Electricity Maps API.

    Returns (is_green, intensity) or (None, None) on error.
    """
    if not emaps_api_key:
        print("::error::Electricity Maps API key required for zone "
              f"'{zone}'. Set electricity_maps_token input.")
        return None, None

    url = f"{EMAPS_API_BASE}/carbon-intensity/latest?zone={zone}"
    print(f"Checking carbon intensity for zone: {zone} (Electricity Maps)...")
    data = api_request_with_header(url, "auth-token", emaps_api_key)
    if data is None:
        return None, None

    intensity = data.get("carbonIntensity")
    if intensity is None:
        print(f"::warning::No carbon intensity in response for zone {zone}")
        return None, None

    intensity = round(intensity)
    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


def get_forecast(zone, max_carbon, emaps_api_key):
    """Fetch carbon intensity forecast from Electricity Maps.

    Returns (forecast_green_at, forecast_intensity) or (None, None).
    """
    if not emaps_api_key:
        return None, None

    url = f"{EMAPS_API_BASE}/carbon-intensity/forecast?zone={zone}"
    print(f"  Fetching Electricity Maps forecast for zone: {zone}...")
    data = api_request_with_header(url, "auth-token", emaps_api_key)
    if data is None:
        return None, None

    forecast = data.get("forecast", [])
    if not forecast:
        print(f"::warning::No forecast data for zone {zone}")
        return None, None

    for period in forecast:
        intensity = period.get("carbonIntensity")
        if intensity is not None and round(intensity) <= max_carbon:
            dt = period.get("datetime", "")
            intensity = round(intensity)
            print(f"  Forecast: grid expected to be green at {dt} ({intensity} gCO2eq/kWh)")
            return dt, intensity

    print(f"  Forecast: no green window found in Electricity Maps forecast horizon.")
    return "none_in_forecast", None


def get_history_trend(zone, emaps_api_key):
    """Fetch recent history from Electricity Maps and compute trend.

    Returns one of: "decreasing", "increasing", "stable", or None.
    """
    if not emaps_api_key:
        return None

    url = f"{EMAPS_API_BASE}/carbon-intensity/history?zone={zone}"
    print(f"  Fetching history trend for zone: {zone} (Electricity Maps)...")
    data = api_request_with_header(url, "auth-token", emaps_api_key)
    if data is None:
        return None

    history = data.get("history", [])
    if not history:
        return None

    points = []
    for entry in history:
        intensity = entry.get("carbonIntensity")
        if intensity is not None:
            points.append(round(intensity))

    return compute_trend(points)
