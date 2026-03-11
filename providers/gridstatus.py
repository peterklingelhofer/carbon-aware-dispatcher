"""GridStatus.io US forecast provider — requires free API key."""

from providers.base import FOSSIL_AVG_INTENSITY, api_request_with_header

GRIDSTATUS_API_BASE = "https://api.gridstatus.io/v1"

# GridStatus.io ISO mapping: EIA BA code -> dataset configuration
GRIDSTATUS_ISO_MAP = {
    "CISO": {
        "renewable_dataset": "caiso_solar_and_wind_forecast_dam",
        "load_dataset": "caiso_load_forecast",
        "solar_col": "solar_mw",
        "wind_col": "wind_mw",
        "load_col": "load_forecast",
        "location_filter": "CAISO",
    },
    "ERCO": {
        "renewable_dataset": "ercot_net_load_forecast",
        "load_dataset": None,
        "solar_col": "solar_forecast",
        "wind_col": "wind_forecast",
        "load_col": "load_forecast",
        "location_filter": None,
    },
    "ISNE": {
        "renewable_dataset": None,
        "solar_dataset": "isone_solar_forecast_hourly",
        "wind_dataset": "isone_wind_forecast_hourly",
        "load_dataset": "isone_load_forecast",
        "solar_col": "solar_forecast",
        "wind_col": "wind_forecast",
        "load_col": "load_forecast",
        "location_filter": None,
    },
    "MISO": {
        "renewable_dataset": None,
        "solar_dataset": "miso_solar_forecast_hourly",
        "wind_dataset": "miso_wind_forecast_hourly",
        "load_dataset": "miso_load_forecast",
        "solar_col": None,
        "wind_col": None,
        "load_col": "load_forecast",
        "location_filter": None,
        "sum_columns": True,
    },
    "NYIS": {
        "renewable_dataset": "nyiso_btm_solar_forecast",
        "load_dataset": "nyiso_load_forecast",
        "solar_col": "system_btm_solar_forecast",
        "wind_col": None,
        "load_col": "load_forecast",
        "location_filter": None,
    },
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
    "SWPP": {
        "renewable_dataset": "spp_solar_and_wind_forecast_mid_term",
        "load_dataset": "spp_load_forecast",
        "solar_col": "solar_forecast_mw",
        "wind_col": "wind_forecast_mw",
        "load_col": "load_forecast",
        "location_filter": None,
    },
}


def _query_dataset(dataset, api_key, start_time, limit=48):
    """Query a GridStatus dataset and return the data rows."""
    url = (
        f"{GRIDSTATUS_API_BASE}/datasets/{dataset}/query"
        f"?start_time={start_time}&limit={limit}"
    )
    result = api_request_with_header(url, "x-api-key", api_key)
    if result is None:
        return []
    return result.get("data", [])


def _get_renewable_forecast(iso_config, api_key, start_time):
    """Fetch solar+wind forecast data for a given ISO.

    Returns dict: interval_start_utc -> {solar_mw, wind_mw}.
    """
    results = {}

    if iso_config.get("renewable_dataset"):
        rows = _query_dataset(iso_config["renewable_dataset"], api_key, start_time)
        loc_filter = iso_config.get("location_filter")

        for row in rows:
            if loc_filter and row.get("location") != loc_filter:
                continue
            ts = row.get("interval_start_utc")
            if ts not in results:
                results[ts] = {"solar_mw": 0, "wind_mw": 0}
            solar = row.get(iso_config.get("solar_col", ""), 0) or 0
            wind = row.get(iso_config.get("wind_col", ""), 0) or 0
            results[ts]["solar_mw"] = float(solar)
            results[ts]["wind_mw"] = float(wind)
    else:
        solar_dataset = iso_config.get("solar_dataset")
        wind_dataset = iso_config.get("wind_dataset")

        if solar_dataset:
            for row in _query_dataset(solar_dataset, api_key, start_time):
                ts = row.get("interval_start_utc")
                if ts not in results:
                    results[ts] = {"solar_mw": 0, "wind_mw": 0}
                if iso_config.get("sum_columns"):
                    total = sum(
                        v for k, v in row.items()
                        if not k.startswith("interval_") and not k.startswith("publish_")
                        and isinstance(v, (int, float)) and v > 0
                    )
                    results[ts]["solar_mw"] = float(total)
                else:
                    col = iso_config.get("solar_col", "solar_forecast")
                    results[ts]["solar_mw"] = float(row.get(col, 0) or 0)

        if wind_dataset:
            for row in _query_dataset(wind_dataset, api_key, start_time):
                ts = row.get("interval_start_utc")
                if ts not in results:
                    results[ts] = {"solar_mw": 0, "wind_mw": 0}
                if iso_config.get("sum_columns"):
                    total = sum(
                        v for k, v in row.items()
                        if not k.startswith("interval_") and not k.startswith("publish_")
                        and isinstance(v, (int, float)) and v > 0
                    )
                    results[ts]["wind_mw"] = float(total)
                else:
                    col = iso_config.get("wind_col", "wind_forecast")
                    results[ts]["wind_mw"] = float(row.get(col, 0) or 0)

    return results


def _get_load_forecast(iso_config, api_key, start_time):
    """Fetch load forecast for a given ISO. Returns dict: timestamp -> load_mw."""
    dataset = iso_config.get("load_dataset")
    if not dataset:
        return None

    rows = _query_dataset(dataset, api_key, start_time)
    results = {}
    load_col = iso_config.get("load_col", "load_forecast")

    for row in rows:
        ts = row.get("interval_start_utc")
        load = row.get(load_col)
        if load is not None and ts:
            results[ts] = float(load)

    return results


def get_forecast(zone, max_carbon, gridstatus_api_key):
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

    renewables = _get_renewable_forecast(iso_config, gridstatus_api_key, start_time)
    if not renewables:
        print(f"::warning::No GridStatus renewable forecast data for zone {zone}")
        return None, None

    if iso_config.get("load_dataset"):
        loads = _get_load_forecast(iso_config, gridstatus_api_key, start_time)
    else:
        loads = {}
        for row in _query_dataset(iso_config["renewable_dataset"], gridstatus_api_key, start_time):
            ts = row.get("interval_start_utc")
            load = row.get(iso_config.get("load_col", "load_forecast"))
            if load is not None and ts:
                loads[ts] = float(load)

    if not loads:
        print(f"::warning::No GridStatus load forecast data for zone {zone}")
        return None, None

    for ts in sorted(renewables.keys()):
        if ts not in loads:
            continue

        load_mw = loads[ts]
        if load_mw <= 0:
            continue

        solar_mw = renewables[ts].get("solar_mw", 0)
        wind_mw = renewables[ts].get("wind_mw", 0)
        renewable_mw = solar_mw + wind_mw

        renewable_pct = min(renewable_mw / load_mw, 1.0)
        fossil_pct = 1.0 - renewable_pct
        estimated_intensity = round(fossil_pct * FOSSIL_AVG_INTENSITY)

        if estimated_intensity <= max_carbon:
            print(f"  Forecast: grid expected to be green at {ts} "
                  f"(~{estimated_intensity} gCO2eq/kWh, "
                  f"{renewable_pct:.0%} renewable)")
            return ts, estimated_intensity

    print(f"  Forecast: no green window found in GridStatus forecast horizon.")
    return "none_in_forecast", None
