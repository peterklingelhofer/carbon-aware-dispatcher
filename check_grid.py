"""Carbon-Aware Dispatcher - checks grid carbon intensity and dispatches workflows."""

import json
import os
import sys
import time

import requests

# Exit codes
EXIT_SUCCESS = 0
EXIT_FAILURE = 1

# Defaults
DEFAULT_MAX_CARBON = 250
DEFAULT_REF = "main"
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 2
RETRY_DELAY = 5

# Provider constants
PROVIDER_UK = "uk_carbon_intensity"
PROVIDER_EIA = "eia"

UK_API_BASE = "https://api.carbonintensity.org.uk"
EIA_API_BASE = "https://api.eia.gov/v2"
GRIDSTATUS_API_BASE = "https://api.gridstatus.io/v1"

# Lifecycle emission factors in gCO2eq/kWh by EIA fuel type code
EIA_EMISSION_FACTORS = {
    "COL": 820,  # Coal
    "NG": 490,   # Natural Gas
    "OIL": 650,  # Petroleum
    "NUC": 0,    # Nuclear
    "SUN": 0,    # Solar
    "WND": 0,    # Wind
    "WAT": 0,    # Hydroelectric
    "GEO": 0,    # Geothermal
    "OTH": 200,  # Other (biomass, waste, etc.) — conservative estimate
    "BAT": 0,    # Battery storage (not a source)
}

# EIA Balancing Authority codes (US grid regions)
# Full list at: https://www.eia.gov/electricity/gridmonitor/
EIA_BALANCING_AUTHORITIES = {
    # Major ISOs
    "CISO", "ERCO", "PJM", "NYIS", "MISO", "ISNE", "SWPP", "SPA",
    # Regions
    "CAL", "CAR", "CENT", "FLA", "MIDA", "MIDW", "NE", "NY", "NW",
    "SE", "SW", "TEX",
    # Other BAs
    "AEC", "AECI", "AVA", "AZPS", "BANC", "BPAT", "CHPD", "CPLE",
    "CPLW", "DEAA", "DOPD", "DUK", "EEI", "EPE", "ERCO", "FMPP",
    "FPC", "FPL", "GCPD", "GVL", "HST", "IID", "IPCO", "JEA",
    "LDWP", "LGEE", "MISO", "NEVP", "NSB", "NWMT", "NYIS", "PACE",
    "PACW", "PGE", "PNM", "PSCO", "PSEI", "SC", "SCEG", "SCL",
    "SEC", "SEPA", "SOCO", "SPA", "SRP", "SWPP", "TAL", "TEC",
    "TEPC", "TIDC", "TPWR", "TVA", "WACM", "WALC", "WAUW", "YAD",
    # Canadian
    "IESO", "AESO",
}

# UK Carbon Intensity API region IDs
UK_REGION_IDS = {
    "GB": None,  # National
    "GB-national": None,
    "GB-1": 1, "North Scotland": 1,
    "GB-2": 2, "South Scotland": 2,
    "GB-3": 3, "North West England": 3,
    "GB-4": 4, "North East England": 4,
    "GB-5": 5, "Yorkshire": 5,
    "GB-6": 6, "North Wales": 6,
    "GB-7": 7, "South Wales": 7,
    "GB-8": 8, "West Midlands": 8,
    "GB-9": 9, "East Midlands": 9,
    "GB-10": 10, "East England": 10,
    "GB-11": 11, "South West England": 11,
    "GB-12": 12, "South England": 12,
    "GB-13": 13, "London": 13,
    "GB-14": 14, "South East England": 14,
    "GB-15": 15, "England": 15,
    "GB-16": 16, "Scotland": 16,
    "GB-17": 17, "Wales": 17,
}

# GridStatus.io ISO mapping: EIA BA code -> (gridstatus source, solar+wind dataset, load dataset)
# Only ISOs with both renewable forecast and load forecast are included.
GRIDSTATUS_ISO_MAP = {
    # CAISO
    "CISO": {
        "renewable_dataset": "caiso_solar_and_wind_forecast_dam",
        "load_dataset": "caiso_load_forecast",
        "solar_col": "solar_mw",
        "wind_col": "wind_mw",
        "load_col": "load_forecast",
        "location_filter": "CAISO",  # filter by location column
    },
    # ERCOT
    "ERCO": {
        "renewable_dataset": "ercot_net_load_forecast",
        "load_dataset": None,  # net load forecast has all fields
        "solar_col": "solar_forecast",
        "wind_col": "wind_forecast",
        "load_col": "load_forecast",
        "location_filter": None,
    },
    # ISO New England
    "ISNE": {
        "renewable_dataset": None,  # separate solar + wind datasets
        "solar_dataset": "isone_solar_forecast_hourly",
        "wind_dataset": "isone_wind_forecast_hourly",
        "load_dataset": "isone_load_forecast",
        "solar_col": "solar_forecast",
        "wind_col": "wind_forecast",
        "load_col": "load_forecast",
        "location_filter": None,
    },
    # MISO
    "MISO": {
        "renewable_dataset": None,
        "solar_dataset": "miso_solar_forecast_hourly",
        "wind_dataset": "miso_wind_forecast_hourly",
        "load_dataset": "miso_load_forecast",
        "solar_col": None,  # uses regional columns, sum them
        "wind_col": None,
        "load_col": "load_forecast",
        "location_filter": None,
        "sum_columns": True,  # solar/wind have regional columns to sum
    },
    # NYISO
    "NYIS": {
        "renewable_dataset": "nyiso_btm_solar_forecast",
        "load_dataset": "nyiso_load_forecast",
        "solar_col": "system_btm_solar_forecast",
        "wind_col": None,  # no wind forecast dataset
        "load_col": "load_forecast",
        "location_filter": None,
    },
    # PJM
    "PJM": {
        "renewable_dataset": None,
        "solar_dataset": "pjm_solar_forecast_hourly",
        "wind_dataset": "pjm_wind_forecast_hourly",
        "load_dataset": "pjm_load_forecast",
        "solar_col": "solar_forecast",
        "wind_col": "wind_forecast",
        "load_col": "load_forecast",
        "location_filter": None,
    },
    # SPP
    "SWPP": {
        "renewable_dataset": "spp_solar_and_wind_forecast_mid_term",
        "load_dataset": "spp_load_forecast",
        "solar_col": "solar_forecast_mw",
        "wind_col": "wind_forecast_mw",
        "load_col": "load_forecast",
        "location_filter": None,
    },
}

# Average fossil fuel intensity (gCO2eq/kWh) used to estimate carbon intensity
# from renewable percentage. Based on typical US fossil mix (~60% gas, ~30% coal, ~10% oil).
FOSSIL_AVG_INTENSITY = 550


def get_required_env(name):
    """Get a required environment variable or exit with an error."""
    value = os.environ.get(name)
    if not value:
        print(f"::error::Required environment variable {name} is not set or empty.")
        sys.exit(EXIT_FAILURE)
    return value


def api_request(url, api_key=None, timeout=DEFAULT_TIMEOUT):
    """Make a GET request with retries.

    Returns the parsed JSON on success, or None on failure.
    """
    headers = {}
    if api_key:
        headers["auth-token"] = api_key

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            print(f"::warning::Network error (attempt {attempt + 1}): {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
                continue
            return None

        if response.status_code == 200:
            try:
                return response.json()
            except (ValueError, requests.exceptions.JSONDecodeError):
                print(f"::warning::Invalid JSON response: {response.text[:200]}")
                return None

        print(f"::warning::API returned {response.status_code} (attempt {attempt + 1}): {response.text[:200]}")
        if response.status_code in (401, 403):
            print("::error::Authentication failed. Check your API token.")
            return None
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    return None


# ---------------------------------------------------------------------------
# Provider: UK Carbon Intensity API (no auth required, GB only)
# ---------------------------------------------------------------------------

def uk_check_carbon_intensity(zone, max_carbon):
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


def uk_get_forecast(zone, max_carbon):
    """Fetch 48h forecast from UK Carbon Intensity API.

    Returns (forecast_green_at, forecast_intensity) or (None, None).
    """
    if zone in ("GB", "GB-national"):
        url = f"{UK_API_BASE}/intensity/date"
    else:
        region_id = UK_REGION_IDS.get(zone)
        if region_id is None:
            return None, None
        url = f"{UK_API_BASE}/regional/intensity/{_iso_now()}/fw48h/regionid/{region_id}"

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


def uk_get_history_trend(zone):
    """Fetch past 24h history from UK Carbon Intensity API and compute trend.

    Returns one of: "decreasing", "increasing", "stable", or None.
    """
    if zone in ("GB", "GB-national"):
        url = f"{UK_API_BASE}/intensity/date"
    else:
        region_id = UK_REGION_IDS.get(zone)
        if region_id is None:
            return None
        url = f"{UK_API_BASE}/regional/intensity/{_iso_now()}/pt24h/regionid/{region_id}"

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

    return _compute_trend(points)


def _iso_now():
    """Return current UTC time in ISO 8601 format for the UK API."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


# ---------------------------------------------------------------------------
# Provider: EIA (U.S. Energy Information Administration) — no auth required
# ---------------------------------------------------------------------------

def _eia_fuel_mix_to_intensity(fuel_data):
    """Calculate carbon intensity from EIA fuel mix data.

    fuel_data: list of dicts with 'fueltype' and 'value' keys.
    Returns carbon intensity in gCO2eq/kWh, or None on error.
    """
    total_generation = 0
    total_co2 = 0

    for row in fuel_data:
        fuel_type = row.get("fueltype", "")
        value = row.get("value")
        if value is None:
            continue
        mwh = float(value)
        if mwh <= 0:
            continue
        total_generation += mwh
        ef = EIA_EMISSION_FACTORS.get(fuel_type, 200)
        total_co2 += mwh * ef

    if total_generation == 0:
        return None

    return round(total_co2 / total_generation)


def eia_check_carbon_intensity(zone, max_carbon, eia_api_key=""):
    """Check carbon intensity using the EIA API (hourly fuel mix).

    Returns (is_green, intensity) or (None, None) on error.
    """
    api_key = eia_api_key or "DEMO_KEY"
    url = (
        f"{EIA_API_BASE}/electricity/rto/fuel-type-data/data"
        f"?api_key={api_key}"
        f"&frequency=hourly"
        f"&data[0]=value"
        f"&facets[respondent][]={zone}"
        f"&sort[0][column]=period"
        f"&sort[0][direction]=desc"
        f"&length=10"
    )

    print(f"Checking carbon intensity for zone: {zone} (EIA API)...")
    data = api_request(url)
    if data is None:
        return None, None

    rows = data.get("response", {}).get("data", [])
    if not rows:
        print(f"::warning::No fuel mix data returned for zone {zone}")
        return None, None

    # Group by the most recent period
    latest_period = rows[0].get("period")
    latest_rows = [r for r in rows if r.get("period") == latest_period]

    intensity = _eia_fuel_mix_to_intensity(latest_rows)
    if intensity is None:
        print(f"::warning::Could not calculate carbon intensity for zone {zone}")
        return None, None

    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


def eia_get_history_trend(zone, eia_api_key=""):
    """Fetch recent hourly fuel mix history from EIA and compute trend.

    Returns one of: "decreasing", "increasing", "stable", or None.
    """
    api_key = eia_api_key or "DEMO_KEY"
    url = (
        f"{EIA_API_BASE}/electricity/rto/fuel-type-data/data"
        f"?api_key={api_key}"
        f"&frequency=hourly"
        f"&data[0]=value"
        f"&facets[respondent][]={zone}"
        f"&sort[0][column]=period"
        f"&sort[0][direction]=desc"
        f"&length=100"
    )

    print(f"  Fetching history trend for zone: {zone}...")
    data = api_request(url)
    if data is None:
        return None

    rows = data.get("response", {}).get("data", [])
    if not rows:
        return None

    # Group by period and calculate intensity for each
    from collections import OrderedDict
    periods = OrderedDict()
    for row in rows:
        p = row.get("period")
        if p not in periods:
            periods[p] = []
        periods[p].append(row)

    intensities = []
    for period_rows in periods.values():
        intensity = _eia_fuel_mix_to_intensity(period_rows)
        if intensity is not None:
            intensities.append(intensity)

    # Reverse so oldest is first (API returns newest first)
    intensities.reverse()
    return _compute_trend(intensities)


# ---------------------------------------------------------------------------
# Provider: GridStatus.io (US forecast — requires free API key)
# ---------------------------------------------------------------------------

def gridstatus_api_request(url, api_key, timeout=DEFAULT_TIMEOUT):
    """Make a GET request to GridStatus API with the x-api-key header."""
    headers = {"x-api-key": api_key}

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            print(f"::warning::GridStatus network error (attempt {attempt + 1}): {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
                continue
            return None

        if response.status_code == 200:
            try:
                return response.json()
            except (ValueError, requests.exceptions.JSONDecodeError):
                print(f"::warning::GridStatus invalid JSON: {response.text[:200]}")
                return None

        print(f"::warning::GridStatus API returned {response.status_code} (attempt {attempt + 1}): {response.text[:200]}")
        if response.status_code in (401, 403):
            print("::error::GridStatus authentication failed. Check your GRID_STATUS_API_KEY.")
            return None
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    return None


def _gridstatus_query_dataset(dataset, api_key, start_time, limit=48):
    """Query a GridStatus dataset and return the data rows."""
    url = (
        f"{GRIDSTATUS_API_BASE}/datasets/{dataset}/query"
        f"?start_time={start_time}&limit={limit}"
    )
    result = gridstatus_api_request(url, api_key)
    if result is None:
        return []
    return result.get("data", [])


def _gridstatus_get_renewable_forecast(iso_config, api_key, start_time):
    """Fetch solar+wind forecast data for a given ISO.

    Returns list of dicts with keys: interval_start_utc, solar_mw, wind_mw.
    """
    results = {}

    if iso_config.get("renewable_dataset"):
        # Single dataset with both solar and wind
        rows = _gridstatus_query_dataset(iso_config["renewable_dataset"], api_key, start_time)
        loc_filter = iso_config.get("location_filter")

        for row in rows:
            if loc_filter and row.get("location") != loc_filter:
                continue
            ts = row.get("interval_start_utc")
            if ts not in results:
                results[ts] = {"solar_mw": 0, "wind_mw": 0}
            solar = row.get(iso_config.get("solar_col", ""), 0) or 0
            wind = row.get(iso_config.get("wind_col", ""), 0) or 0
            # Keep the latest publish_time for each interval
            results[ts]["solar_mw"] = float(solar)
            results[ts]["wind_mw"] = float(wind)
    else:
        # Separate solar and wind datasets
        solar_dataset = iso_config.get("solar_dataset")
        wind_dataset = iso_config.get("wind_dataset")

        if solar_dataset:
            solar_rows = _gridstatus_query_dataset(solar_dataset, api_key, start_time)
            for row in solar_rows:
                ts = row.get("interval_start_utc")
                if ts not in results:
                    results[ts] = {"solar_mw": 0, "wind_mw": 0}
                if iso_config.get("sum_columns"):
                    # Sum all numeric columns except timestamps
                    total = 0
                    for k, v in row.items():
                        if k.startswith("interval_") or k.startswith("publish_"):
                            continue
                        if isinstance(v, (int, float)) and v > 0:
                            total += v
                    results[ts]["solar_mw"] = float(total)
                else:
                    col = iso_config.get("solar_col", "solar_forecast")
                    results[ts]["solar_mw"] = float(row.get(col, 0) or 0)

        if wind_dataset:
            wind_rows = _gridstatus_query_dataset(wind_dataset, api_key, start_time)
            for row in wind_rows:
                ts = row.get("interval_start_utc")
                if ts not in results:
                    results[ts] = {"solar_mw": 0, "wind_mw": 0}
                if iso_config.get("sum_columns"):
                    total = 0
                    for k, v in row.items():
                        if k.startswith("interval_") or k.startswith("publish_"):
                            continue
                        if isinstance(v, (int, float)) and v > 0:
                            total += v
                    results[ts]["wind_mw"] = float(total)
                else:
                    col = iso_config.get("wind_col", "wind_forecast")
                    results[ts]["wind_mw"] = float(row.get(col, 0) or 0)

    return results


def _gridstatus_get_load_forecast(iso_config, api_key, start_time):
    """Fetch load forecast data for a given ISO.

    Returns dict: interval_start_utc -> load_mw.
    """
    dataset = iso_config.get("load_dataset")
    if not dataset:
        # Some ISOs (e.g., ERCOT net load) have load in the renewable dataset
        return None

    rows = _gridstatus_query_dataset(dataset, api_key, start_time)
    results = {}
    load_col = iso_config.get("load_col", "load_forecast")

    for row in rows:
        ts = row.get("interval_start_utc")
        load = row.get(load_col)
        if load is not None and ts:
            results[ts] = float(load)

    return results


def gridstatus_get_forecast(zone, max_carbon, gridstatus_api_key):
    """Get carbon intensity forecast for a US zone using GridStatus.io.

    Estimates future carbon intensity from renewable generation and load forecasts.
    Returns (forecast_green_at, forecast_intensity) or (None, None).
    """
    iso_config = GRIDSTATUS_ISO_MAP.get(zone)
    if not iso_config:
        print(f"  GridStatus forecast not available for zone {zone}")
        return None, None

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    start_time = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"  Fetching GridStatus.io forecast for zone: {zone}...")

    # Get renewable forecast
    renewables = _gridstatus_get_renewable_forecast(iso_config, gridstatus_api_key, start_time)
    if not renewables:
        print(f"::warning::No GridStatus renewable forecast data for zone {zone}")
        return None, None

    # Get load forecast
    if iso_config.get("load_dataset"):
        loads = _gridstatus_get_load_forecast(iso_config, gridstatus_api_key, start_time)
    else:
        # For ERCOT net load dataset, load is already in the renewable data
        loads = {}
        rows = _gridstatus_query_dataset(iso_config["renewable_dataset"], gridstatus_api_key, start_time)
        load_col = iso_config.get("load_col", "load_forecast")
        for row in rows:
            ts = row.get("interval_start_utc")
            load = row.get(load_col)
            if load is not None and ts:
                loads[ts] = float(load)

    if not loads:
        print(f"::warning::No GridStatus load forecast data for zone {zone}")
        return None, None

    # Calculate estimated carbon intensity for each forecast period
    for ts in sorted(renewables.keys()):
        if ts not in loads:
            continue

        load_mw = loads[ts]
        if load_mw <= 0:
            continue

        solar_mw = renewables[ts].get("solar_mw", 0)
        wind_mw = renewables[ts].get("wind_mw", 0)
        renewable_mw = solar_mw + wind_mw

        # Clamp renewable to load (can't exceed 100%)
        renewable_pct = min(renewable_mw / load_mw, 1.0)
        fossil_pct = 1.0 - renewable_pct

        # Estimate carbon intensity: fossil portion * avg fossil intensity
        estimated_intensity = round(fossil_pct * FOSSIL_AVG_INTENSITY)

        if estimated_intensity <= max_carbon:
            print(f"  Forecast: grid expected to be green at {ts} "
                  f"(~{estimated_intensity} gCO2eq/kWh, "
                  f"{renewable_pct:.0%} renewable)")
            return ts, estimated_intensity

    print(f"  Forecast: no green window found in GridStatus forecast horizon.")
    return "none_in_forecast", None


# ---------------------------------------------------------------------------
# Provider-agnostic helpers
# ---------------------------------------------------------------------------

def _compute_trend(points):
    """Compute trend direction from a list of intensity values.

    Returns "decreasing", "increasing", "stable", or None.
    """
    if len(points) < 6:
        return None

    recent = points[-3:]
    earlier = points[-6:-3]

    avg_recent = sum(recent) / len(recent)
    avg_earlier = sum(earlier) / len(earlier)

    pct_change = (avg_recent - avg_earlier) / max(avg_earlier, 1) * 100

    if pct_change < -5:
        trend = "decreasing"
    elif pct_change > 5:
        trend = "increasing"
    else:
        trend = "stable"

    print(f"  Trend: {trend} (recent avg: {avg_recent:.0f}, earlier avg: {avg_earlier:.0f} gCO2eq/kWh)")
    return trend


def detect_provider(zone):
    """Auto-detect the provider based on zone identifier."""
    if zone in UK_REGION_IDS:
        return PROVIDER_UK
    return PROVIDER_EIA


def check_carbon_intensity(zone, api_key, max_carbon, provider):
    """Check carbon intensity using the appropriate provider."""
    if provider == PROVIDER_UK:
        return uk_check_carbon_intensity(zone, max_carbon)
    return eia_check_carbon_intensity(zone, max_carbon, api_key)


def get_forecast(zone, api_key, max_carbon, provider, gridstatus_api_key=""):
    """Get forecast using the appropriate provider."""
    if provider == PROVIDER_UK:
        return uk_get_forecast(zone, max_carbon)
    # US zones: use GridStatus.io if API key is available
    if gridstatus_api_key:
        return gridstatus_get_forecast(zone, max_carbon, gridstatus_api_key)
    print("  No forecast available for US zones without a GridStatus API key.")
    return None, None


def get_history_trend(zone, api_key, provider):
    """Get history trend using the appropriate provider."""
    if provider == PROVIDER_UK:
        return uk_get_history_trend(zone)
    return eia_get_history_trend(zone, api_key)


def check_multiple_zones(zones_config, api_key, max_carbon):
    """Check carbon intensity for multiple zones, return the best green option.

    Returns (best_zone, best_intensity, best_runner_label) or (None, None, None).
    """
    best_zone = None
    best_intensity = None
    best_label = None

    for entry in zones_config:
        zone = entry["zone"]
        label = entry.get("runner_label")
        provider = detect_provider(zone)

        is_green, intensity = check_carbon_intensity(zone, api_key, max_carbon, provider)
        if is_green and intensity is not None:
            if best_intensity is None or intensity < best_intensity:
                best_zone = zone
                best_intensity = intensity
                best_label = label

    return best_zone, best_intensity, best_label


def parse_zones_input(zones_str):
    """Parse the zones input string into a list of zone configs.

    Supports two formats:
      - Simple comma-separated: "GB,CISO,ERCO"
      - With runner labels: "GB:runner-uk,CISO:runner-us-cal"
    """
    zones = []
    for part in zones_str.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            zone, label = part.split(":", 1)
            zones.append({"zone": zone.strip(), "runner_label": label.strip()})
        else:
            zones.append({"zone": part, "runner_label": None})
    return zones


def trigger_workflow(repo, workflow_id, token, ref):
    """Trigger a GitHub Actions workflow via the REST API."""
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_id}/dispatches"
    payload = {"ref": ref}

    print(f"Dispatching workflow '{workflow_id}' on ref '{ref}'...")
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        print(f"::error::Failed to dispatch workflow: {exc}")
        sys.exit(EXIT_FAILURE)

    if response.status_code == 204:
        print(f"Workflow '{workflow_id}' dispatched successfully.")
    else:
        print(f"::error::Failed to trigger workflow (HTTP {response.status_code}): {response.text}")
        sys.exit(EXIT_FAILURE)


def set_output(name, value):
    """Set a GitHub Actions output variable."""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"{name}={value}\n")
    print(f"  Output {name}={value}")


def handle_dirty_grid(zone, api_key, max_carbon, intensity, enable_forecast,
                      gridstatus_api_key=""):
    """When the grid is dirty, fetch forecast/trend info and set outputs."""
    provider = detect_provider(zone)

    set_output("grid_clean", "false")
    if intensity is not None:
        set_output("carbon_intensity", str(intensity))
    else:
        set_output("carbon_intensity", "unknown")

    # Always try history trend
    trend = get_history_trend(zone, api_key, provider)
    if trend:
        set_output("intensity_trend", trend)

    # Forecast — free for UK, GridStatus for US (requires key)
    if enable_forecast or provider == PROVIDER_UK:
        forecast_at, forecast_intensity = get_forecast(
            zone, api_key, max_carbon, provider, gridstatus_api_key
        )
        if forecast_at and forecast_at != "none_in_forecast":
            set_output("forecast_green_at", forecast_at)
            if forecast_intensity is not None:
                set_output("forecast_intensity", str(forecast_intensity))
            print(f"\n  Grid expected to be green at {forecast_at}")
        elif forecast_at == "none_in_forecast":
            set_output("forecast_green_at", "none_in_forecast")
            print("\n  No green window found in forecast horizon.")


def main():
    # Required inputs
    workflow_id = get_required_env("WORKFLOW_ID")
    token = get_required_env("GITHUB_TOKEN")
    repo = get_required_env("TARGET_REPO")

    # Optional inputs with defaults
    api_key = os.environ.get("EIA_API_KEY", "")
    gridstatus_api_key = os.environ.get("GRID_STATUS_API_KEY", "")
    ref = os.environ.get("TARGET_REF", DEFAULT_REF) or DEFAULT_REF
    max_carbon = float(os.environ.get("MAX_CARBON", DEFAULT_MAX_CARBON))
    fail_on_api_error = os.environ.get("FAIL_ON_API_ERROR", "false").lower() == "true"
    enable_forecast = os.environ.get("ENABLE_FORECAST", "false").lower() == "true"

    # Parse zone(s)
    grid_zones_str = os.environ.get("GRID_ZONES", "")
    grid_zone_str = os.environ.get("GRID_ZONE", "")

    if grid_zones_str:
        zones_config = parse_zones_input(grid_zones_str)
    elif grid_zone_str:
        zones_config = parse_zones_input(grid_zone_str)
    else:
        print("::error::Either GRID_ZONE or GRID_ZONES must be set.")
        sys.exit(EXIT_FAILURE)

    if not zones_config:
        print("::error::No valid zones provided.")
        sys.exit(EXIT_FAILURE)

    print(f"Carbon intensity threshold: {max_carbon} gCO2eq/kWh")
    print(f"Checking {len(zones_config)} zone(s)...\n")

    # Single zone mode
    if len(zones_config) == 1:
        entry = zones_config[0]
        provider = detect_provider(entry["zone"])
        is_green, intensity = check_carbon_intensity(entry["zone"], api_key, max_carbon, provider)

        if is_green is None:
            set_output("grid_clean", "false")
            set_output("carbon_intensity", "unknown")
            if fail_on_api_error:
                print("::error::API error and fail_on_api_error is enabled.")
                sys.exit(EXIT_FAILURE)
            print("\nAPI error occurred. Skipping dispatch to avoid dirty compute.")
            sys.exit(EXIT_SUCCESS)

        set_output("grid_zone", entry["zone"])

        if is_green:
            set_output("grid_clean", "true")
            set_output("carbon_intensity", str(intensity))
            if entry.get("runner_label"):
                set_output("runner_label", entry["runner_label"])
            print(f"\nGrid is clean! Triggering workflow...")
            trigger_workflow(repo, workflow_id, token, ref)
        else:
            handle_dirty_grid(entry["zone"], api_key, max_carbon, intensity, enable_forecast,
                              gridstatus_api_key)
            print(f"\nGrid is dirty ({intensity} gCO2eq/kWh > {max_carbon}). Will retry on next schedule.")
            sys.exit(EXIT_SUCCESS)

    # Multi-zone mode: pick the greenest zone
    else:
        best_zone, best_intensity, best_label = check_multiple_zones(
            zones_config, api_key, max_carbon
        )

        if best_zone is None:
            first_zone = zones_config[0]["zone"]
            handle_dirty_grid(first_zone, api_key, max_carbon, None, enable_forecast,
                              gridstatus_api_key)
            if fail_on_api_error:
                print("::error::No green zones found and fail_on_api_error is enabled.")
                sys.exit(EXIT_FAILURE)
            print("\nNo green zones available. Will retry on next schedule.")
            sys.exit(EXIT_SUCCESS)

        set_output("grid_clean", "true")
        set_output("grid_zone", best_zone)
        set_output("carbon_intensity", str(best_intensity))
        if best_label:
            set_output("runner_label", best_label)

        print(f"\nBest zone: {best_zone} ({best_intensity} gCO2eq/kWh)")
        print(f"Triggering workflow...")
        trigger_workflow(repo, workflow_id, token, ref)


if __name__ == "__main__":
    main()
