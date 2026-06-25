"""Electricity Maps provider: requires an API key.

Direct consumption-based carbon intensity in gCO2eq/kWh. Electricity Maps covers
200+ zones, but the FREE tier is limited to a SINGLE zone, which you choose when
registering (plus a 50 requests/hour cap). So this provider only works for that
one registered zone unless you have a paid plan; it is not a global catch-all on
the free tier. Use the free grid-operator providers for broad coverage and
register the one Electricity Maps zone you most need (e.g. a region with no free
feed, such as JP-TK or SG).

Register at https://portal.electricitymaps.com/. Paid plans (200+ zones) are
sold per country/year, so they cost far more than a flat API key.
"""

from providers.base import api_request_with_header, ci_secret_hint, compute_trend, green_result

EMAPS_API_BASE = "https://api-access.electricitymaps.com/free-tier"


def check_carbon_intensity(zone, max_carbon, emaps_api_key):
    """Check carbon intensity using the Electricity Maps API.

    Returns (is_green, intensity) or (None, None) on error.
    """
    if not emaps_api_key:
        print(
            f"::error::Electricity Maps API token required for zone '{zone}'. "
            "Get a free token at https://portal.electricitymaps.com/ "
            f"and {ci_secret_hint('electricity_maps_token')}."
        )
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

    return green_result(zone, round(intensity), max_carbon)


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

    print("  Forecast: no green window found in Electricity Maps forecast horizon.")
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
