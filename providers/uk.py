"""UK Carbon Intensity API provider (no auth required, GB only)."""

import json

from providers import UK_REGION_IDS
from providers.base import api_request, compute_trend, iso_now

UK_API_BASE = "https://api.carbonintensity.org.uk"


def check_carbon_intensity(zone, max_carbon):
    """Check carbon intensity using the UK Carbon Intensity API.

    Returns (is_green, intensity) or (None, None) on error.
    """
    region_id = UK_REGION_IDS.get(zone)

    if zone in ("GB", "GB-national"):
        url = f"{UK_API_BASE}/intensity"
    elif region_id is not None:
        url = f"{UK_API_BASE}/regional/regionid/{region_id}"
    else:
        print(f"::error::Unknown UK zone '{zone}'. Use GB, GB-1 through GB-17, or a region name.")
        return None, None

    print(f"Checking carbon intensity for zone: {zone} (UK Carbon Intensity API)...")
    data = api_request(url)
    if data is None:
        return None, None

    try:
        if zone in ("GB", "GB-national"):
            intensity = data["data"][0]["intensity"]["forecast"]
        else:
            intensity = data["data"][0]["data"][0]["intensity"]["forecast"]
    except (KeyError, IndexError, TypeError):
        print(f"::warning::Unexpected response structure for zone {zone}: {json.dumps(data)[:200]}")
        return None, None

    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


def get_forecast(zone, max_carbon):
    """Fetch 48h forecast from UK Carbon Intensity API.

    Returns (forecast_green_at, forecast_intensity) or (None, None).
    """
    if zone in ("GB", "GB-national"):
        url = f"{UK_API_BASE}/intensity/date"
    else:
        region_id = UK_REGION_IDS.get(zone)
        if region_id is None:
            return None, None
        url = f"{UK_API_BASE}/regional/intensity/{iso_now()}/fw48h/regionid/{region_id}"

    print(f"  Fetching forecast for zone: {zone}...")
    data = api_request(url)
    if data is None:
        return None, None

    try:
        if zone in ("GB", "GB-national"):
            periods = data.get("data", [])
            for period in periods:
                intensity = period["intensity"]["forecast"]
                if intensity <= max_carbon:
                    dt = period["from"]
                    print(f"  Forecast: grid expected to be green at {dt} ({intensity} gCO2eq/kWh)")
                    return dt, intensity
        else:
            periods = data.get("data", {}).get("data", [])
            for period in periods:
                intensity = period["intensity"]["forecast"]
                if intensity <= max_carbon:
                    dt = period["from"]
                    print(f"  Forecast: grid expected to be green at {dt} ({intensity} gCO2eq/kWh)")
                    return dt, intensity
    except (KeyError, TypeError):
        print(f"::warning::Could not parse forecast response for zone {zone}")
        return None, None

    print(f"  Forecast: no green window found in next {len(periods)} periods.")
    return "none_in_forecast", None


def get_history_trend(zone):
    """Fetch past 24h history from UK Carbon Intensity API and compute trend.

    Returns one of: "decreasing", "increasing", "stable", or None.
    """
    if zone in ("GB", "GB-national"):
        url = f"{UK_API_BASE}/intensity/date"
    else:
        region_id = UK_REGION_IDS.get(zone)
        if region_id is None:
            return None
        url = f"{UK_API_BASE}/regional/intensity/{iso_now()}/pt24h/regionid/{region_id}"

    print(f"  Fetching history trend for zone: {zone}...")
    data = api_request(url)
    if data is None:
        return None

    try:
        if zone in ("GB", "GB-national"):
            points = [p["intensity"]["forecast"] for p in data.get("data", [])]
        else:
            points = [p["intensity"]["forecast"] for p in data.get("data", {}).get("data", [])]
    except (KeyError, TypeError):
        return None

    return compute_trend(points)
