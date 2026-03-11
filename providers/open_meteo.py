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
    # -----------------------------------------------------------------------
    # Europe — fallback when ENTSO-E/Electricity Maps tokens are unavailable.
    # Coordinates are for the main population/grid center of each zone.
    # -----------------------------------------------------------------------
    "NO-NO1": (59.9, 10.7),    # Norway Oslo
    "NO-NO2": (60.4, 5.3),     # Norway Bergen
    "NO-NO3": (63.4, 10.4),    # Norway Trondheim
    "NO-NO4": (69.6, 18.9),    # Norway Tromsø
    "NO-NO5": (61.5, 6.8),     # Norway Sognefjord
    "SE-SE1": (67.0, 20.2),    # Sweden North
    "SE-SE2": (63.8, 20.3),    # Sweden Mid-North
    "SE-SE3": (59.3, 18.1),    # Sweden Stockholm
    "SE-SE4": (55.6, 13.0),    # Sweden Malmö
    "DK-DK1": (56.2, 9.5),     # Denmark West (Jutland)
    "DK-DK2": (55.7, 12.6),    # Denmark East (Zealand)
    "FI": (60.2, 24.9),        # Finland Helsinki
    "EE": (59.4, 24.7),        # Estonia Tallinn
    "LV": (56.9, 24.1),        # Latvia Riga
    "LT": (54.7, 25.3),        # Lithuania Vilnius
    "FR": (48.9, 2.3),         # France Paris
    "DE": (52.5, 13.4),        # Germany Berlin
    "NL": (52.4, 4.9),         # Netherlands Amsterdam
    "BE": (50.8, 4.4),         # Belgium Brussels
    "AT": (48.2, 16.4),        # Austria Vienna
    "CH": (47.4, 8.5),         # Switzerland Zurich
    "PL": (52.2, 21.0),        # Poland Warsaw
    "CZ": (50.1, 14.4),        # Czech Republic Prague
    "ES": (40.4, -3.7),        # Spain Madrid
    "PT": (38.7, -9.1),        # Portugal Lisbon
    "IT-NO": (45.5, 9.2),      # Italy North (Milan)
    "IT-CNO": (43.8, 11.3),    # Italy Centre-North (Florence)
    "IT-CSO": (41.9, 12.5),    # Italy Centre-South (Rome)
    "IT-SO": (40.9, 14.3),     # Italy South (Naples)
    "IT-SIC": (37.5, 15.1),    # Italy Sicily (Catania)
    "IT-SAR": (39.2, 9.1),     # Italy Sardinia (Cagliari)
    "IE": (53.3, -6.3),        # Ireland Dublin
    "IS": (64.1, -21.9),       # Iceland Reykjavik
    "GR": (37.98, 23.7),       # Greece Athens
    "RO": (44.4, 26.1),        # Romania Bucharest
    "BG": (42.7, 23.3),        # Bulgaria Sofia
    "HU": (47.5, 19.0),        # Hungary Budapest
    "SK": (48.1, 17.1),        # Slovakia Bratislava
    "HR": (45.8, 16.0),        # Croatia Zagreb
    "RS": (44.8, 20.5),        # Serbia Belgrade
    "SI": (46.1, 14.5),        # Slovenia Ljubljana
    "BA": (43.9, 18.4),        # Bosnia Sarajevo
    "ME": (42.4, 19.3),        # Montenegro Podgorica
    "MK": (42.0, 21.4),        # North Macedonia Skopje
    "AL": (41.3, 19.8),        # Albania Tirana

    # -----------------------------------------------------------------------
    # Americas — Canada, Latin America (Electricity Maps fallback)
    # -----------------------------------------------------------------------
    "CA-QC": (45.5, -73.6),    # Quebec Montreal
    "CA-ON": (43.7, -79.4),    # Ontario Toronto
    "CA-BC": (49.3, -123.1),   # British Columbia Vancouver
    "CA-AB": (51.0, -114.1),   # Alberta Calgary
    "CA-SK": (50.4, -104.6),   # Saskatchewan Regina
    "CA-MB": (49.9, -97.1),    # Manitoba Winnipeg
    "CA-NB": (45.9, -66.6),    # New Brunswick Fredericton
    "CA-NS": (44.6, -63.6),    # Nova Scotia Halifax
    "UY": (-34.9, -56.2),      # Uruguay Montevideo
    "PY": (-25.3, -57.6),      # Paraguay Asunción
    "CR": (9.9, -84.1),        # Costa Rica San José
    "CL-SEN": (-33.4, -70.7),  # Chile Santiago
    "AR": (-34.6, -58.4),      # Argentina Buenos Aires
    "CO": (4.7, -74.1),        # Colombia Bogotá
    "PA": (9.0, -79.5),        # Panama City
    "PE": (-12.0, -77.0),      # Peru Lima
    "EC": (-0.2, -78.5),       # Ecuador Quito
    "MX": (19.4, -99.1),       # Mexico City

    # -----------------------------------------------------------------------
    # Asia-Pacific — Japan, Korea, Southeast Asia, Oceania
    # -----------------------------------------------------------------------
    "JP-TK": (35.7, 139.7),    # Japan Tokyo
    "JP-CB": (35.2, 137.0),    # Japan Chubu
    "JP-KN": (34.7, 135.5),    # Japan Kansai (Osaka)
    "JP-KY": (33.6, 130.4),    # Japan Kyushu (Fukuoka)
    "KR": (37.6, 127.0),       # South Korea Seoul
    "NZ-NZN": (-36.8, 174.8),  # New Zealand North (Auckland)
    "NZ-NZS": (-43.5, 172.6),  # New Zealand South (Christchurch)
    "SG": (1.3, 103.8),        # Singapore
    "TW": (25.0, 121.5),       # Taiwan Taipei
    "HK": (22.3, 114.2),       # Hong Kong
    "TH": (13.8, 100.5),       # Thailand Bangkok
    "VN": (21.0, 105.9),       # Vietnam Hanoi
    "PH": (14.6, 121.0),       # Philippines Manila
    "ID": (-6.2, 106.8),       # Indonesia Jakarta
    "MY": (3.1, 101.7),        # Malaysia KL

    # -----------------------------------------------------------------------
    # South Asia
    # -----------------------------------------------------------------------
    "PK": (24.9, 67.0),        # Pakistan Karachi
    "BD": (23.8, 90.4),        # Bangladesh Dhaka
    "LK": (6.9, 79.9),         # Sri Lanka Colombo

    # -----------------------------------------------------------------------
    # China
    # -----------------------------------------------------------------------
    "CN-BJ": (39.9, 116.4),    # Beijing
    "CN-SH": (31.2, 121.5),    # Shanghai
    "CN-GD": (23.1, 113.3),    # Guangdong

    # -----------------------------------------------------------------------
    # Africa
    # -----------------------------------------------------------------------
    "ZA": (-33.9, 18.4),       # South Africa Cape Town
    "KE": (-1.3, 36.8),        # Kenya Nairobi
    "NG": (6.5, 3.4),          # Nigeria Lagos
    "EG": (30.0, 31.2),        # Egypt Cairo
    "MA": (33.6, -7.6),        # Morocco Casablanca
    "GH": (5.6, -0.2),         # Ghana Accra
    "TZ": (-6.8, 39.3),        # Tanzania Dar es Salaam
    "ET": (9.0, 38.7),         # Ethiopia Addis Ababa

    # -----------------------------------------------------------------------
    # Middle East & Central Asia
    # -----------------------------------------------------------------------
    "AE": (25.2, 55.3),        # UAE Dubai
    "SA": (24.7, 46.7),        # Saudi Arabia Riyadh — note: not AU-SA
    "IL": (32.1, 34.8),        # Israel Tel Aviv
    "TR": (41.0, 29.0),        # Turkey Istanbul
    "KZ": (51.1, 71.4),        # Kazakhstan Astana
    "UZ": (41.3, 69.3),        # Uzbekistan Tashkent

    # -----------------------------------------------------------------------
    # Eastern Europe
    # -----------------------------------------------------------------------
    "UA": (50.4, 30.5),        # Ukraine Kyiv
    "GE": (41.7, 44.8),        # Georgia Tbilisi
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
