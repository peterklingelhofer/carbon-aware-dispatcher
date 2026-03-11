"""Open-Meteo provider — universal coverage fallback, free, no API key.

Estimates carbon intensity from real-time solar irradiance and wind speed
data at a given latitude/longitude. This is a rough estimate — it doesn't
know the actual grid mix, but can indicate renewable potential.

The model: if solar irradiance is high and/or wind speed is high, the grid
is likely cleaner than average. We map renewable potential to estimated
carbon intensity using a simple heuristic.

Free, no API key, no registration. Rate limit: ~10,000 requests/day.
API: https://api.open-meteo.com/v1/forecast
"""

import requests

from providers.base import DEFAULT_TIMEOUT, FOSSIL_AVG_INTENSITY

OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"

# Zone → (latitude, longitude) for zones not covered by other providers.
# This enables Open-Meteo as a fallback when no API key is available.
# Add coordinates for any zone that might need fallback coverage.
ZONE_COORDINATES = {
    # Africa
    "ZA": (-33.9, 18.4),       # South Africa (Cape Town)
    "KE": (-1.3, 36.8),        # Kenya (Nairobi)
    "NG": (6.5, 3.4),          # Nigeria (Lagos)
    "EG": (30.0, 31.2),        # Egypt (Cairo)
    "MA": (33.6, -7.6),        # Morocco (Casablanca)
    "GH": (5.6, -0.2),         # Ghana (Accra)
    "TZ": (-6.8, 39.3),        # Tanzania (Dar es Salaam)
    "ET": (9.0, 38.7),         # Ethiopia (Addis Ababa)
    # Middle East
    "AE": (25.2, 55.3),        # UAE (Dubai)
    "SA": (24.7, 46.7),        # Saudi Arabia (Riyadh) — note: not AU-SA
    "IL": (32.1, 34.8),        # Israel (Tel Aviv)
    "TR": (41.0, 29.0),        # Turkey (Istanbul)
    # Central Asia
    "KZ": (51.1, 71.4),        # Kazakhstan (Astana)
    "UZ": (41.3, 69.3),        # Uzbekistan (Tashkent)
    # Southeast Asia
    "TH": (13.8, 100.5),       # Thailand (Bangkok)
    "VN": (21.0, 105.9),       # Vietnam (Hanoi)
    "PH": (14.6, 121.0),       # Philippines (Manila)
    "ID": (-6.2, 106.8),       # Indonesia (Jakarta)
    "MY": (3.1, 101.7),        # Malaysia (KL)
    # South Asia
    "PK": (24.9, 67.0),        # Pakistan (Karachi)
    "BD": (23.8, 90.4),        # Bangladesh (Dhaka)
    "LK": (6.9, 79.9),         # Sri Lanka (Colombo)
    # China
    "CN-BJ": (39.9, 116.4),    # Beijing
    "CN-SH": (31.2, 121.5),    # Shanghai
    "CN-GD": (23.1, 113.3),    # Guangdong
    # Eastern Europe / Central Asia
    "UA": (50.4, 30.5),        # Ukraine (Kyiv)
    "GE": (41.7, 44.8),        # Georgia (Tbilisi)
}

# Solar irradiance thresholds (W/m²)
HIGH_SOLAR = 600    # Strong solar — significant PV generation
MEDIUM_SOLAR = 300  # Moderate solar

# Wind speed thresholds (m/s at 10m height)
HIGH_WIND = 8       # Strong wind — good turbine output
MEDIUM_WIND = 5     # Moderate wind


def _estimate_intensity_from_weather(solar_w_m2, wind_speed_ms):
    """Estimate grid carbon intensity from solar irradiance and wind speed.

    This is a heuristic — actual grid intensity depends on the local generation
    mix which we don't know. But high solar + high wind strongly correlates with
    cleaner grids in most regions.

    Returns estimated gCO2eq/kWh.
    """
    # Start from global average fossil intensity
    base = FOSSIL_AVG_INTENSITY  # ~550

    # Solar contribution: up to 40% reduction
    if solar_w_m2 >= HIGH_SOLAR:
        solar_factor = 0.60  # 40% reduction
    elif solar_w_m2 >= MEDIUM_SOLAR:
        solar_factor = 0.80  # 20% reduction
    elif solar_w_m2 > 50:
        solar_factor = 0.90  # 10% reduction
    else:
        solar_factor = 1.0   # Night / overcast

    # Wind contribution: up to 25% reduction
    if wind_speed_ms >= HIGH_WIND:
        wind_factor = 0.75   # 25% reduction
    elif wind_speed_ms >= MEDIUM_WIND:
        wind_factor = 0.85   # 15% reduction
    elif wind_speed_ms > 3:
        wind_factor = 0.93   # 7% reduction
    else:
        wind_factor = 1.0    # Calm

    # Combine — multiplicative (both solar and wind reduce intensity)
    estimated = round(base * solar_factor * wind_factor)

    return estimated


def check_carbon_intensity(zone, max_carbon, lat=None, lon=None):
    """Estimate carbon intensity using Open-Meteo weather data.

    Uses zone coordinates from ZONE_COORDINATES, or explicit lat/lon.
    Returns (is_green, intensity) or (None, None) on error.
    """
    if lat is None or lon is None:
        coords = ZONE_COORDINATES.get(zone)
        if coords is None:
            print(f"::warning::No coordinates for zone '{zone}' in Open-Meteo provider. "
                  "Use an Electricity Maps token for this zone, or add coordinates.")
            return None, None
        lat, lon = coords

    url = (
        f"{OPEN_METEO_API}?latitude={lat}&longitude={lon}"
        f"&current=global_tilted_irradiance,wind_speed_10m"
    )

    print(f"Checking renewable potential for zone: {zone} (Open-Meteo estimate)...")
    try:
        response = requests.get(url, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        print(f"::warning::Open-Meteo API error: {exc}")
        return None, None

    if response.status_code != 200:
        print(f"::warning::Open-Meteo returned {response.status_code}: {response.text[:200]}")
        return None, None

    try:
        data = response.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        print(f"::warning::Invalid JSON from Open-Meteo")
        return None, None

    current = data.get("current", {})
    solar = current.get("global_tilted_irradiance", 0) or 0
    wind = current.get("wind_speed_10m", 0) or 0

    intensity = _estimate_intensity_from_weather(solar, wind)
    is_green = intensity <= max_carbon
    status = "GREEN (estimated)" if is_green else "over threshold (estimated)"
    print(f"  Zone {zone}: ~{intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    print(f"  (Solar: {solar:.0f} W/m², Wind: {wind:.1f} m/s — this is an estimate)")
    return is_green, intensity


def get_forecast(zone, max_carbon, lat=None, lon=None):
    """Fetch hourly forecast from Open-Meteo to estimate future green windows.

    Returns (forecast_green_at, forecast_intensity) or (None, None).
    """
    if lat is None or lon is None:
        coords = ZONE_COORDINATES.get(zone)
        if coords is None:
            return None, None
        lat, lon = coords

    url = (
        f"{OPEN_METEO_API}?latitude={lat}&longitude={lon}"
        f"&hourly=global_tilted_irradiance,wind_speed_10m"
        f"&forecast_days=2"
    )

    print(f"  Fetching Open-Meteo forecast for zone: {zone}...")
    try:
        response = requests.get(url, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        print(f"::warning::Open-Meteo forecast error: {exc}")
        return None, None

    if response.status_code != 200:
        return None, None

    try:
        data = response.json()
    except (ValueError, requests.exceptions.JSONDecodeError):
        return None, None

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    solar_values = hourly.get("global_tilted_irradiance", [])
    wind_values = hourly.get("wind_speed_10m", [])

    for i, time_str in enumerate(times):
        solar = solar_values[i] if i < len(solar_values) else 0
        wind = wind_values[i] if i < len(wind_values) else 0
        intensity = _estimate_intensity_from_weather(solar or 0, wind or 0)

        if intensity <= max_carbon:
            dt = time_str.replace(" ", "T") + "Z" if "T" not in time_str else time_str
            print(f"  Forecast: estimated green at {dt} (~{intensity} gCO2eq/kWh)")
            return dt, intensity

    print(f"  Forecast: no estimated green window in 48h Open-Meteo forecast.")
    return "none_in_forecast", None


def get_history_trend(zone, lat=None, lon=None):
    """Open-Meteo doesn't provide history trend for carbon intensity.

    Returns None.
    """
    return None
