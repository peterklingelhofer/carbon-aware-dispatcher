"""Shared utilities for all providers."""

import time

import requests

# Defaults
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 2
RETRY_DELAY = 5

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

# Average fossil fuel intensity (gCO2eq/kWh) used to estimate carbon intensity
# from renewable percentage. Based on typical US fossil mix (~60% gas, ~30% coal, ~10% oil).
FOSSIL_AVG_INTENSITY = 550


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


def api_request_with_header(url, header_name, api_key, timeout=DEFAULT_TIMEOUT):
    """Make a GET request with a custom auth header name. Used by GridStatus and Electricity Maps."""
    headers = {header_name: api_key}

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
            print(f"::error::Authentication failed. Check your API key.")
            return None
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    return None


def compute_trend(points):
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


def iso_now():
    """Return current UTC time in ISO 8601 format."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
