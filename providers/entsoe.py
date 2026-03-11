"""ENTSO-E Transparency Platform provider — free EU coverage with registration.

Covers 36 European countries with actual generation per production type.
Requires a free security token from https://transparency.entsoe.eu/.
Rate limit: 400 requests/min.

Note: Returns XML, not JSON. We parse it manually (no lxml dependency).
"""

import re
from datetime import datetime, timedelta, timezone

import requests

from providers.base import compute_trend, DEFAULT_TIMEOUT

ENTSOE_API_BASE = "https://web-api.tp.entsoe.eu/api"

# Bidding zone EIC codes for major European countries/zones
# Full list: https://transparency.entsoe.eu/content/static_content/Static%20content/web%20api/Guide.html
ENTSOE_AREA_CODES = {
    # Major countries
    "DE": "10Y1001A1001A83F",   # Germany
    "FR": "10YFR-RTE------C",   # France
    "ES": "10YES-REE------0",   # Spain
    "PT": "10YPT-REN------W",   # Portugal
    "NL": "10YNL----------L",   # Netherlands
    "BE": "10YBE----------2",   # Belgium
    "AT": "10YAT-APG------L",   # Austria
    "CH": "10YCH-SWISSGRIDZ",  # Switzerland
    "PL": "10YPL-AREA-----S",   # Poland
    "CZ": "10YCZ-CEPS-----N",   # Czech Republic
    "DK-DK1": "10YDK-1--------W",  # Denmark West
    "DK-DK2": "10YDK-2--------M",  # Denmark East
    "FI": "10YFI-1--------U",   # Finland
    "SE-SE1": "10Y1001A1001A44P",  # Sweden 1
    "SE-SE2": "10Y1001A1001A45N",  # Sweden 2
    "SE-SE3": "10Y1001A1001A46L",  # Sweden 3
    "SE-SE4": "10Y1001A1001A47J",  # Sweden 4
    "NO-NO1": "10YNO-1--------2",  # Norway 1
    "NO-NO2": "10YNO-2--------T",  # Norway 2
    "NO-NO3": "10YNO-3--------J",  # Norway 3
    "NO-NO4": "10YNO-4--------9",  # Norway 4
    "NO-NO5": "10Y1001A1001A48H",  # Norway 5
    "IE": "10YIE-1001A00010",   # Ireland
    "IT-NO": "10Y1001A1001A73I",   # Italy North
    "IT-CNO": "10Y1001A1001A70O",  # Italy Centre-North
    "IT-CSO": "10Y1001A1001A71M",  # Italy Centre-South
    "IT-SO": "10Y1001A1001A788",   # Italy South
    "IT-SIC": "10Y1001A1001A74G",  # Italy Sicily
    "IT-SAR": "10Y1001A1001A75E",  # Italy Sardinia
    "GR": "10YGR-HTSO-----Y",   # Greece
    "RO": "10YRO-TEL------P",   # Romania
    "BG": "10YCA-BULGARIA-R",   # Bulgaria
    "HU": "10YHU-MAVIR----U",   # Hungary
    "SK": "10YSK-SEPS-----K",   # Slovakia
    "HR": "10YHR-HEP------M",   # Croatia
    "RS": "10YCS-SERBIATSOV",   # Serbia
    "SI": "10YSI-ELES-----O",   # Slovenia
    "BA": "10YBA-JPCC-----D",   # Bosnia
    "ME": "10YCS-CG-TSO---S",   # Montenegro
    "MK": "10YMK-MEPSO----8",   # North Macedonia
    "AL": "10YAL-KESH-----5",   # Albania
    "EE": "10Y1001A1001A39I",   # Estonia
    "LV": "10YLV-1001A00074",   # Latvia
    "LT": "10YLT-1001A0008Q",   # Lithuania
}

# ENTSO-E production type codes → emission factors (gCO2eq/kWh)
# B01-B20 are the standard ENTSO-E PSR type codes
ENTSOE_EMISSION_FACTORS = {
    "B01": 900,   # Biomass (lifecycle: growth, harvest, transport, combustion)
    "B02": 900,   # Fossil Brown coal/Lignite — very carbon-intensive
    "B03": 490,   # Fossil Coal-derived gas (similar to natural gas)
    "B04": 490,   # Fossil Gas
    "B05": 820,   # Fossil Hard coal
    "B06": 650,   # Fossil Oil
    "B07": 650,   # Fossil Oil shale
    "B08": 340,   # Fossil Peat (between coal and gas)
    "B09": 0,     # Geothermal
    "B10": 0,     # Hydro Pumped Storage
    "B11": 0,     # Hydro Run-of-river
    "B12": 0,     # Hydro Water Reservoir
    "B13": 200,   # Marine
    "B14": 0,     # Nuclear
    "B15": 200,   # Other renewable
    "B16": 0,     # Solar
    "B17": 200,   # Waste
    "B18": 0,     # Wind Offshore
    "B19": 0,     # Wind Onshore
    "B20": 200,   # Other
}


def _parse_generation_xml(xml_text):
    """Parse ENTSO-E generation XML response into a list of (psr_type, quantity) tuples.

    Uses regex parsing to avoid requiring lxml/xml.etree dependencies.
    """
    results = []
    # Find each TimeSeries block
    series_pattern = re.compile(
        r'<MktPSRType>.*?<psrType>(B\d{2})</psrType>.*?</MktPSRType>'
        r'.*?<quantity>([\d.]+)</quantity>',
        re.DOTALL
    )

    for match in series_pattern.finditer(xml_text):
        psr_type = match.group(1)
        quantity = float(match.group(2))
        if quantity > 0:
            results.append((psr_type, quantity))

    return results


def check_carbon_intensity(zone, max_carbon, entsoe_token):
    """Check carbon intensity using ENTSO-E actual generation data.

    Returns (is_green, intensity) or (None, None) on error.
    """
    if not entsoe_token:
        print(f"::error::ENTSO-E security token required for zone '{zone}'. "
              "Register free at https://transparency.entsoe.eu/ → Login → "
              "Account Settings → Web API Security Token.")
        return None, None

    area_code = ENTSOE_AREA_CODES.get(zone)
    if area_code is None:
        # Zone not in ENTSO-E — caller should try another provider
        return None, None

    # Request the most recent hour of actual generation
    now = datetime.now(timezone.utc)
    period_start = (now - timedelta(hours=1)).strftime("%Y%m%d%H00")
    period_end = now.strftime("%Y%m%d%H00")

    url = (
        f"{ENTSOE_API_BASE}?securityToken={entsoe_token}"
        f"&documentType=A75"  # Actual generation per type
        f"&processType=A16"   # Realised
        f"&in_Domain={area_code}"
        f"&periodStart={period_start}"
        f"&periodEnd={period_end}"
    )

    print(f"Checking carbon intensity for zone: {zone} (ENTSO-E)...")
    try:
        response = requests.get(url, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        print(f"::warning::ENTSO-E API error: {exc}")
        return None, None

    if response.status_code == 401:
        print("::error::ENTSO-E authentication failed. Check your entsoe_token secret. "
              "Get a free token at https://transparency.entsoe.eu/ → Account Settings → "
              "Web API Security Token.")
        return None, None

    if response.status_code == 429:
        print("::warning::ENTSO-E rate limit exceeded (400 req/min). "
              "Automatically blocked for 10 minutes. Will retry on next schedule run.")
        return None, None

    if response.status_code != 200:
        print(f"::warning::ENTSO-E API returned {response.status_code}: "
              f"{response.text[:200]}")
        return None, None

    # Parse generation data
    gen_data = _parse_generation_xml(response.text)
    if not gen_data:
        print(f"::warning::No generation data from ENTSO-E for zone {zone}")
        return None, None

    # Calculate weighted carbon intensity
    total_gen = 0
    weighted_emissions = 0
    for psr_type, quantity in gen_data:
        factor = ENTSOE_EMISSION_FACTORS.get(psr_type, 200)
        total_gen += quantity
        weighted_emissions += quantity * factor

    if total_gen <= 0:
        return None, None

    intensity = round(weighted_emissions / total_gen)
    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


def get_forecast(zone, max_carbon, entsoe_token):
    """Fetch day-ahead generation forecast from ENTSO-E.

    Returns (forecast_green_at, forecast_intensity) or (None, None).
    """
    if not entsoe_token:
        return None, None

    area_code = ENTSOE_AREA_CODES.get(zone)
    if area_code is None:
        return None, None

    now = datetime.now(timezone.utc)
    period_start = now.strftime("%Y%m%d%H00")
    period_end = (now + timedelta(hours=24)).strftime("%Y%m%d%H00")

    url = (
        f"{ENTSOE_API_BASE}?securityToken={entsoe_token}"
        f"&documentType=A71"  # Generation forecast
        f"&processType=A01"   # Day ahead
        f"&in_Domain={area_code}"
        f"&periodStart={period_start}"
        f"&periodEnd={period_end}"
    )

    print(f"  Fetching ENTSO-E forecast for zone: {zone}...")
    try:
        response = requests.get(url, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        print(f"::warning::ENTSO-E forecast error: {exc}")
        return None, None

    if response.status_code != 200:
        return None, None

    gen_data = _parse_generation_xml(response.text)
    if not gen_data:
        return None, None

    # Calculate intensity from forecast data
    total_gen = 0
    weighted_emissions = 0
    for psr_type, quantity in gen_data:
        factor = ENTSOE_EMISSION_FACTORS.get(psr_type, 200)
        total_gen += quantity
        weighted_emissions += quantity * factor

    if total_gen <= 0:
        return None, None

    intensity = round(weighted_emissions / total_gen)
    if intensity <= max_carbon:
        # Forecast shows green within 24h
        forecast_time = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:00Z")
        print(f"  Forecast: grid expected to be green ({intensity} gCO2eq/kWh)")
        return forecast_time, intensity

    print(f"  Forecast: no green window in ENTSO-E 24h forecast horizon.")
    return "none_in_forecast", None


def get_history_trend(zone, entsoe_token):
    """Fetch recent generation history and compute trend.

    Returns one of: "decreasing", "increasing", "stable", or None.
    """
    if not entsoe_token:
        return None

    area_code = ENTSOE_AREA_CODES.get(zone)
    if area_code is None:
        return None

    now = datetime.now(timezone.utc)
    period_start = (now - timedelta(hours=6)).strftime("%Y%m%d%H00")
    period_end = now.strftime("%Y%m%d%H00")

    url = (
        f"{ENTSOE_API_BASE}?securityToken={entsoe_token}"
        f"&documentType=A75"
        f"&processType=A16"
        f"&in_Domain={area_code}"
        f"&periodStart={period_start}"
        f"&periodEnd={period_end}"
    )

    print(f"  Fetching history trend for zone: {zone} (ENTSO-E)...")
    try:
        response = requests.get(url, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException:
        return None

    if response.status_code != 200:
        return None

    gen_data = _parse_generation_xml(response.text)
    if not gen_data:
        return None

    # For trend we need per-period intensities, but the simple XML parse
    # aggregates all periods. Use the overall intensity as a single point.
    total_gen = sum(q for _, q in gen_data)
    if total_gen <= 0:
        return None

    intensity = round(sum(q * ENTSOE_EMISSION_FACTORS.get(t, 200) for t, q in gen_data) / total_gen)

    # We can't compute a proper trend from a single aggregated point,
    # so return None. A more sophisticated implementation would parse
    # individual time periods from the XML.
    return None
