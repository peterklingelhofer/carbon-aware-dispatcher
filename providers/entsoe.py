"""ENTSO-E Transparency Platform provider — free EU coverage with registration.

Covers 36 European countries with actual generation per production type.
Requires a free security token from https://transparency.entsoe.eu/.
Rate limit: 400 requests/min.

Note: Returns XML, not JSON. We parse it manually (no lxml dependency).
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from providers.base import DEFAULT_FUEL_FACTOR, FUEL_FACTORS, request

ENTSOE_API_BASE = "https://web-api.tp.entsoe.eu/api"

# Bidding zone EIC codes for major European countries/zones
# Full list: https://transparency.entsoe.eu/content/static_content/Static%20content/web%20api/Guide.html
ENTSOE_AREA_CODES = {
    # Major countries
    "DE": "10Y1001A1001A83F",  # Germany
    "FR": "10YFR-RTE------C",  # France
    "ES": "10YES-REE------0",  # Spain
    "PT": "10YPT-REN------W",  # Portugal
    "NL": "10YNL----------L",  # Netherlands
    "BE": "10YBE----------2",  # Belgium
    "AT": "10YAT-APG------L",  # Austria
    "CH": "10YCH-SWISSGRIDZ",  # Switzerland
    "PL": "10YPL-AREA-----S",  # Poland
    "CZ": "10YCZ-CEPS-----N",  # Czech Republic
    "DK-DK1": "10YDK-1--------W",  # Denmark West
    "DK-DK2": "10YDK-2--------M",  # Denmark East
    "FI": "10YFI-1--------U",  # Finland
    "SE-SE1": "10Y1001A1001A44P",  # Sweden 1
    "SE-SE2": "10Y1001A1001A45N",  # Sweden 2
    "SE-SE3": "10Y1001A1001A46L",  # Sweden 3
    "SE-SE4": "10Y1001A1001A47J",  # Sweden 4
    "NO-NO1": "10YNO-1--------2",  # Norway 1
    "NO-NO2": "10YNO-2--------T",  # Norway 2
    "NO-NO3": "10YNO-3--------J",  # Norway 3
    "NO-NO4": "10YNO-4--------9",  # Norway 4
    "NO-NO5": "10Y1001A1001A48H",  # Norway 5
    "IE": "10YIE-1001A00010",  # Ireland
    "IT-NO": "10Y1001A1001A73I",  # Italy North
    "IT-CNO": "10Y1001A1001A70O",  # Italy Centre-North
    "IT-CSO": "10Y1001A1001A71M",  # Italy Centre-South
    "IT-SO": "10Y1001A1001A788",  # Italy South
    "IT-SIC": "10Y1001A1001A74G",  # Italy Sicily
    "IT-SAR": "10Y1001A1001A75E",  # Italy Sardinia
    "GR": "10YGR-HTSO-----Y",  # Greece
    "RO": "10YRO-TEL------P",  # Romania
    "BG": "10YCA-BULGARIA-R",  # Bulgaria
    "HU": "10YHU-MAVIR----U",  # Hungary
    "SK": "10YSK-SEPS-----K",  # Slovakia
    "HR": "10YHR-HEP------M",  # Croatia
    "RS": "10YCS-SERBIATSOV",  # Serbia
    "SI": "10YSI-ELES-----O",  # Slovenia
    "BA": "10YBA-JPCC-----D",  # Bosnia
    "ME": "10YCS-CG-TSO---S",  # Montenegro
    "MK": "10YMK-MEPSO----8",  # North Macedonia
    "AL": "10YAL-KESH-----5",  # Albania
    "EE": "10Y1001A1001A39I",  # Estonia
    "LV": "10YLV-1001A00074",  # Latvia
    "LT": "10YLT-1001A0008Q",  # Lithuania
}

# ENTSO-E production type codes → emission factors (gCO2eq/kWh)
# ENTSO-E PSR type codes (B01-B20) mapped to canonical factors (see
# providers.base.FUEL_FACTORS)
ENTSOE_EMISSION_FACTORS = {
    "B01": FUEL_FACTORS["biomass"],
    "B02": FUEL_FACTORS["lignite"],  # Fossil Brown coal/Lignite
    "B03": FUEL_FACTORS["gas"],  # Fossil Coal-derived gas
    "B04": FUEL_FACTORS["gas"],  # Fossil Gas
    "B05": FUEL_FACTORS["coal"],  # Fossil Hard coal
    "B06": FUEL_FACTORS["oil"],  # Fossil Oil
    "B07": FUEL_FACTORS["oil"],  # Fossil Oil shale
    "B08": FUEL_FACTORS["lignite"],  # Fossil Peat (lignite-class)
    "B09": FUEL_FACTORS["geothermal"],
    # B10 Hydro Pumped Storage is storage, not generation, and is excluded
    "B11": FUEL_FACTORS["hydro"],  # Hydro Run-of-river
    "B12": FUEL_FACTORS["hydro"],  # Hydro Water Reservoir
    "B13": FUEL_FACTORS["marine"],
    "B14": FUEL_FACTORS["nuclear"],
    "B15": FUEL_FACTORS["solar"],  # Other renewable
    "B16": FUEL_FACTORS["solar"],
    "B17": FUEL_FACTORS["waste"],
    "B18": FUEL_FACTORS["wind"],  # Wind Offshore
    "B19": FUEL_FACTORS["wind"],  # Wind Onshore
    "B20": FUEL_FACTORS["other"],
}

# PSR codes that represent storage, not primary generation
# Pumped storage discharge is not zero-carbon and double-counts the energy
# used to pump it, so it is excluded from the weighted-average denominator
ENTSOE_STORAGE_PSR = {"B10"}


def _local_name(tag):
    """Strip any XML namespace from an element tag."""
    return tag.rsplit("}", 1)[-1]


def _parse_generation_xml(xml_text):
    """Parse ENTSO-E generation XML response into a list of (psr_type, quantity) tuples.

    For each production type only the LATEST time position is used (the
    highest <position> in the most recent <Period>), not a sum or blend
    across hours, so the result reflects current generation. Quantities of
    zero or less are excluded
    """
    if not xml_text or not xml_text.strip():
        return []
    # Wrap in a synthetic root so bare sibling <TimeSeries> elements (and any
    # real single-rooted document) both parse cleanly
    wrapped = f"<root>{xml_text}</root>"
    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

    # per psr_type track the latest (period_end, position) and its quantity
    latest = {}
    for ts in root.iter():
        if _local_name(ts.tag) != "TimeSeries":
            continue
        psr_type = None
        for el in ts.iter():
            if _local_name(el.tag) == "psrType" and el.text:
                psr_type = el.text.strip()
                break
        if psr_type is None:
            continue
        for period in ts.iter():
            if _local_name(period.tag) != "Period":
                continue
            period_end = ""
            for el in period.iter():
                if _local_name(el.tag) == "end" and el.text:
                    period_end = el.text.strip()
            for point in period.iter():
                if _local_name(point.tag) != "Point":
                    continue
                position = None
                quantity = None
                for child in point:
                    cname = _local_name(child.tag)
                    if cname == "position" and child.text:
                        try:
                            position = int(child.text.strip())
                        except ValueError:
                            position = None
                    elif cname == "quantity" and child.text:
                        try:
                            quantity = float(child.text.strip())
                        except ValueError:
                            quantity = None
                if quantity is None:
                    continue
                key = (period_end, position if position is not None else -1)
                prev = latest.get(psr_type)
                if prev is None or key > prev[0]:
                    latest[psr_type] = (key, quantity)

    results = []
    for psr_type, (_key, quantity) in latest.items():
        if quantity > 0:
            results.append((psr_type, quantity))
    return results


def _parse_flow_latest(xml_text):
    """Most recent physical-flow value (MW) from an ENTSO-E A11 document.

    A11 (cross-border physical flow) has one <quantity> per <Point> in time
    order; the last one is the current flow. Returns a float MW, or None.
    """
    if not xml_text or not xml_text.strip():
        return None
    wrapped = f"<root>{xml_text}</root>"
    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None
    latest = None
    for el in root.iter():
        if _local_name(el.tag) == "quantity" and el.text:
            try:
                latest = float(el.text.strip())
            except ValueError:
                continue
    return latest


def _intensity_from_gen_data(gen_data):
    """Weighted carbon intensity from (psr_type, quantity) pairs.

    Storage PSR codes are excluded from the denominator because storage
    discharge is not primary generation. Unknown PSR codes warn then fall
    back to DEFAULT_FUEL_FACTOR. Returns gCO2eq/kWh or None
    """
    total_gen = 0
    weighted_emissions = 0
    for psr_type, quantity in gen_data:
        if psr_type in ENTSOE_STORAGE_PSR:
            continue
        factor = ENTSOE_EMISSION_FACTORS.get(psr_type)
        if factor is None:
            print(
                f"::warning::Unknown ENTSO-E production type '{psr_type}', "
                f"using fallback {DEFAULT_FUEL_FACTOR} gCO2eq/kWh"
            )
            factor = DEFAULT_FUEL_FACTOR
        total_gen += quantity
        weighted_emissions += quantity * factor
    if total_gen <= 0:
        return None
    return round(weighted_emissions / total_gen)


def _total_generation_mw(gen_data):
    """Total non-storage generation (MW) from (psr_type, quantity) pairs."""
    return sum(q for psr, q in gen_data if psr not in ENTSOE_STORAGE_PSR)


def production_for_zone(zone, entsoe_token):
    """Fetch a zone's production intensity AND total generation MW.

    Returns (intensity_gco2_kwh, total_mw) or (None, None) on error. Used by the
    flow-tracing layer, which needs the MW total (P_i) that check_carbon_intensity
    discards.
    """
    if not entsoe_token:
        return None, None
    area_code = ENTSOE_AREA_CODES.get(zone)
    if area_code is None:
        return None, None

    now = datetime.now(timezone.utc)
    period_start = (now - timedelta(hours=1)).strftime("%Y%m%d%H00")
    period_end = now.strftime("%Y%m%d%H00")
    url = (
        f"{ENTSOE_API_BASE}?securityToken={entsoe_token}"
        f"&documentType=A75&processType=A16&in_Domain={area_code}"
        f"&periodStart={period_start}&periodEnd={period_end}"
    )
    response = request(url, parse="response")
    if response is None or response.status_code != 200:
        return None, None
    gen_data = _parse_generation_xml(response.text)
    if not gen_data:
        return None, None
    intensity = _intensity_from_gen_data(gen_data)
    total_mw = _total_generation_mw(gen_data)
    if intensity is None or total_mw <= 0:
        return None, None
    return intensity, total_mw


def check_carbon_intensity(zone, max_carbon, entsoe_token):
    """Check carbon intensity using ENTSO-E actual generation data.

    Returns (is_green, intensity) or (None, None) on error.
    """
    if not entsoe_token:
        print(
            f"::error::ENTSO-E security token required for zone '{zone}'. "
            "Register free at https://transparency.entsoe.eu/ → Login → "
            "Account Settings → Web API Security Token."
        )
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
        f"&processType=A16"  # Realised
        f"&in_Domain={area_code}"
        f"&periodStart={period_start}"
        f"&periodEnd={period_end}"
    )

    print(f"Checking carbon intensity for zone: {zone} (ENTSO-E)...")
    # Route through base for retries/429 handling; parse="response" lets us
    # keep ENTSO-E's tailored 401/429 messages and read the raw XML text
    response = request(url, parse="response")
    if response is None:
        print("::warning::ENTSO-E API error: request failed")
        return None, None

    if response.status_code == 401:
        print(
            "::error::ENTSO-E authentication failed. Check your entsoe_token secret. "
            "Get a free token at https://transparency.entsoe.eu/ → Account Settings → "
            "Web API Security Token."
        )
        return None, None

    if response.status_code == 429:
        print(
            "::warning::ENTSO-E rate limit exceeded (400 req/min). "
            "Automatically blocked for 10 minutes. Will retry on next schedule run."
        )
        return None, None

    if response.status_code != 200:
        print(f"::warning::ENTSO-E API returned {response.status_code}: {response.text[:200]}")
        return None, None

    # Parse generation data
    gen_data = _parse_generation_xml(response.text)
    if not gen_data:
        print(f"::warning::No generation data from ENTSO-E for zone {zone}")
        return None, None

    intensity = _intensity_from_gen_data(gen_data)
    if intensity is None:
        return None, None

    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


# Variable renewables ENTSO-E publishes a day-ahead forecast for:
# wind onshore (B19), wind offshore (B18), solar (B16).
_VRE_PSR = {"B16", "B18", "B19"}

# How far ahead to search for a green window
FORECAST_HORIZON_HOURS = 48


def _forecast_series_by_hour(xml_text, psr_filter):
    """Parse an ENTSO-E forecast document into {hour(UTC): MW}.

    psr_filter limits to specific production types (generation docs); pass None
    for load documents. Sub-hourly resolutions are averaged within the hour, and
    multiple matching TimeSeries (e.g. wind + solar) are summed per hour.
    """
    if not xml_text or not xml_text.strip():
        return {}
    wrapped = f"<root>{xml_text}</root>"
    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return {}

    totals = {}
    for ts in root.iter():
        if _local_name(ts.tag) != "TimeSeries":
            continue
        if psr_filter is not None:
            psr = None
            for el in ts.iter():
                if _local_name(el.tag) == "psrType" and el.text:
                    psr = el.text.strip()
                    break
            if psr not in psr_filter:
                continue
        for period in ts.iter():
            if _local_name(period.tag) != "Period":
                continue
            start_text = None
            resolution = None
            for el in period.iter():
                name = _local_name(el.tag)
                if name == "start" and el.text and start_text is None:
                    start_text = el.text.strip()
                elif name == "resolution" and el.text:
                    resolution = el.text.strip()
            if not start_text:
                continue
            try:
                start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
            except ValueError:
                continue
            step = 15 if resolution == "PT15M" else 60
            buckets = {}
            for pt in period.iter():
                if _local_name(pt.tag) != "Point":
                    continue
                pos = qty = None
                for child in pt:
                    cname = _local_name(child.tag)
                    if cname == "position" and child.text:
                        try:
                            pos = int(child.text.strip())
                        except ValueError:
                            pos = None
                    elif cname == "quantity" and child.text:
                        try:
                            qty = float(child.text.strip())
                        except ValueError:
                            qty = None
                if pos is None or qty is None:
                    continue
                dt = start + timedelta(minutes=(pos - 1) * step)
                hour = dt.replace(minute=0, second=0, microsecond=0)
                buckets.setdefault(hour, []).append(qty)
            for hour, vals in buckets.items():
                totals[hour] = totals.get(hour, 0.0) + sum(vals) / len(vals)
    return totals


def _vre_fraction_curve(zone, entsoe_token):
    """Forecasted variable-renewable share of load, per UTC hour for the horizon.

    Combines the day-ahead wind+solar generation forecast (A69) with the
    day-ahead total-load forecast (A65). Returns {hour(UTC): fraction 0..1}, or
    {} if unavailable. A higher VRE share means lower expected carbon intensity.
    """
    area_code = ENTSOE_AREA_CODES.get(zone)
    if not entsoe_token or area_code is None:
        return {}

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    period_start = now.strftime("%Y%m%d%H00")
    period_end = (now + timedelta(hours=FORECAST_HORIZON_HOURS)).strftime("%Y%m%d%H00")

    gen_url = (
        f"{ENTSOE_API_BASE}?securityToken={entsoe_token}"
        f"&documentType=A69&processType=A01&in_Domain={area_code}"
        f"&periodStart={period_start}&periodEnd={period_end}"
    )
    load_url = (
        f"{ENTSOE_API_BASE}?securityToken={entsoe_token}"
        f"&documentType=A65&processType=A01&outBiddingZone_Domain={area_code}"
        f"&periodStart={period_start}&periodEnd={period_end}"
    )

    gen_resp = request(gen_url, parse="response")
    load_resp = request(load_url, parse="response")
    if gen_resp is None or gen_resp.status_code != 200:
        return {}
    if load_resp is None or load_resp.status_code != 200:
        return {}

    vre = _forecast_series_by_hour(gen_resp.text, _VRE_PSR)
    load = _forecast_series_by_hour(load_resp.text, None)
    return {
        hour: min(1.0, max(0.0, vre[hour] / mw_load))
        for hour, mw_load in load.items()
        if mw_load > 0 and hour in vre
    }


def get_forecast(zone, max_carbon, entsoe_token):
    """Forecast the next green window from ENTSO-E day-ahead forecasts.

    Uses the forecasted variable-renewable (wind+solar) share of load to scale
    the zone's current production intensity hour by hour, then returns the first
    hour expected below the threshold. This is a real day-ahead signal, not a
    fixed daily curve.

    Returns (forecast_green_at, forecast_intensity) or (None, None) on error,
    or ("none_in_forecast", None) when no green hour is found in the horizon.
    """
    if not entsoe_token or zone not in ENTSOE_AREA_CODES:
        return None, None

    # Current production intensity and VRE share, to anchor the projection
    current_intensity, _total = production_for_zone(zone, entsoe_token)
    if current_intensity is None:
        return None, None

    print(f"  Fetching ENTSO-E day-ahead forecast for zone: {zone}...")
    curve = _vre_fraction_curve(zone, entsoe_token)
    if not curve:
        return None, None

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    current_frac = curve.get(now)
    # Without a baseline VRE share we cannot scale; bail to "unknown"
    if current_frac is None:
        return None, None

    # Scale intensity by how non-VRE (fossil-ish) share changes vs now. More
    # renewables -> less fossil -> lower intensity. Clamp the baseline so a
    # near-100%-renewable now doesn't divide by zero.
    base_fossil = max(1.0 - current_frac, 0.05)
    for offset in range(1, FORECAST_HORIZON_HOURS + 1):
        hour = now + timedelta(hours=offset)
        frac = curve.get(hour)
        if frac is None:
            continue
        projected = round(current_intensity * (1.0 - frac) / base_fossil)
        if projected <= max_carbon:
            dt = hour.strftime("%Y-%m-%dT%H:00Z")
            print(f"  Forecast: grid expected green at {dt} (~{projected} gCO2eq/kWh)")
            return dt, projected

    print(f"  Forecast: no green window in ENTSO-E {FORECAST_HORIZON_HOURS}h horizon.")
    return "none_in_forecast", None


def get_history_trend(zone, entsoe_token):
    """Fetch recent generation history and compute trend.

    Per-period trend is not yet implemented, so short-circuit up front
    rather than fetching and parsing XML only to discard it. A future
    implementation would compute intensity per <Period> and feed compute_trend

    Returns one of: "decreasing", "increasing", "stable", or None.
    """
    return None
