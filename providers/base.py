"""Shared utilities for all providers."""

import time
from typing import Any, Optional, Protocol

import requests

# Return contract shared by every provider module
IntensityResult = tuple[Optional[bool], Optional[int]]
ForecastResult = tuple[Optional[str], Optional[int]]


class CarbonProvider(Protocol):
    """Structural interface every provider module satisfies.

    Concrete providers may accept extra credential arguments (api key or
    token) after the first two positional arguments
    """

    def check_carbon_intensity(
        self, zone: str, max_carbon: float, *args: Any
    ) -> IntensityResult: ...

    def get_forecast(self, zone: str, max_carbon: float, *args: Any) -> ForecastResult: ...

    def get_history_trend(self, zone: str, *args: Any) -> Optional[str]: ...


# Defaults
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 2
RETRY_DELAY = 5

# Lifecycle emission factors in gCO2eq/kWh by EIA fuel type code
# IPCC AR5 (2014) lifecycle median gCO2eq/kWh
EIA_EMISSION_FACTORS = {
    "COL": 820,  # Coal
    "NG": 490,  # Natural Gas
    "OIL": 650,  # Petroleum
    "NUC": 12,  # Nuclear
    "SUN": 45,  # Solar
    "WND": 12,  # Wind
    "WAT": 24,  # Hydroelectric
    "GEO": 38,  # Geothermal
    "BIO": 230,  # Biomass (IPCC dedicated biomass median)
    "OTH": 300,  # Other / unknown-but-counted
    # BAT is battery storage, not generation, and is excluded from the mix
}

# Fuel codes that represent storage, not primary generation
# Storage discharge is not zero-carbon and double-counts energy already
# counted at generation, so it is excluded from the weighted-average denominator
EIA_STORAGE_FUELS = {"BAT"}

# Fallback factor for unknown fuel codes (warned about, then applied)
DEFAULT_FUEL_FACTOR = 300

# Average fossil fuel intensity (gCO2eq/kWh) used to estimate carbon intensity
# from renewable percentage. Based on typical US fossil mix (~60% gas, ~30% coal, ~10% oil).
FOSSIL_AVG_INTENSITY = 550

# Global average grid carbon intensity (~450 gCO2eq/kWh).
# Used as baseline for estimating carbon savings from green scheduling.
GLOBAL_AVG_INTENSITY = 450

# Average CI job power draw in kW. GitHub-hosted runners are 2-4 vCPU machines
# drawing roughly 30-60W. We use 0.05 kW as a conservative estimate.
CI_JOB_POWER_KW = 0.05

# Default estimated CI job duration in hours (15 minutes).
DEFAULT_JOB_DURATION_HOURS = 0.25


# Status codes worth retrying: transient server errors and rate limiting
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Upper bound (seconds) we will honor from a Retry-After header, so a hostile
# or misconfigured server cannot stall a CI run for minutes
MAX_RETRY_AFTER = 60


def _retry_after_seconds(response):
    """Return a capped sleep time from a 429 Retry-After header, or None.

    Only numeric (delta-seconds) values are honored; HTTP-date forms are
    ignored and the caller falls back to RETRY_DELAY
    """
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = int(str(raw).strip())
    except (ValueError, TypeError):
        return None
    if seconds < 0:
        return None
    return min(seconds, MAX_RETRY_AFTER)


def request(
    url, *, method="GET", headers=None, json_body=None, timeout=DEFAULT_TIMEOUT, parse="json"
):
    """Make an HTTP request with retries and consistent error handling.

    method: HTTP verb, GET by default (POST supported for AEMO)
    headers: optional dict of request headers
    json_body: optional JSON body to send (used with POST)
    parse: "json" returns parsed JSON, "response" returns the raw Response,
           "text" returns response text

    Retries on network errors and retryable status codes (>=500 and 429) up
    to MAX_RETRIES. On 429 honors a numeric Retry-After header (capped). On
    401/403 logs an auth error and returns None immediately. Returns None on
    failure. URLs are never logged because they may carry tokens
    """
    headers = headers or {}

    for attempt in range(MAX_RETRIES + 1):
        try:
            # Dispatch to the verb-specific helper so the mockable seam stays
            # requests.get / requests.post, matching the rest of the codebase
            if method.upper() == "POST":
                response = requests.post(url, headers=headers, json=json_body, timeout=timeout)
            else:
                response = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            print(f"::warning::Network error (attempt {attempt + 1}): {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
                continue
            return None

        if response.status_code == 200:
            if parse == "response":
                return response
            if parse == "text":
                return response.text
            try:
                return response.json()
            except (ValueError, requests.exceptions.JSONDecodeError):
                print(f"::warning::Invalid JSON response: {response.text[:200]}")
                return None

        print(
            f"::warning::API returned {response.status_code} "
            f"(attempt {attempt + 1}): {response.text[:200]}"
        )
        if response.status_code in (401, 403):
            print("::error::Authentication failed. Check your API token.")
            return None

        if attempt < MAX_RETRIES and response.status_code in RETRYABLE_STATUS:
            wait = RETRY_DELAY
            if response.status_code == 429:
                retry_after = _retry_after_seconds(response)
                if retry_after is not None:
                    wait = retry_after
                print(f"::warning::Rate limited (429), waiting {wait}s before retry")
            time.sleep(wait)
            continue

        if attempt < MAX_RETRIES:
            # non-retryable 4xx, do not hammer the endpoint
            return None

    return None


def api_request(url: str, api_key: Optional[str] = None, timeout: int = DEFAULT_TIMEOUT) -> Any:
    """Make a GET request with retries.

    Thin wrapper over request(). Sets the auth-token header when api_key is
    given. Returns parsed JSON on success, or None on failure
    """
    headers = {}
    if api_key:
        headers["auth-token"] = api_key
    return request(url, method="GET", headers=headers, timeout=timeout, parse="json")


def api_request_with_header(
    url: str, header_name: str, api_key: str, timeout: int = DEFAULT_TIMEOUT
) -> Any:
    """GET with a custom auth header name, used by GridStatus and Electricity Maps."""
    return request(
        url,
        method="GET",
        headers={header_name: api_key},
        timeout=timeout,
        parse="json",
    )


def compute_trend(points: list[float]) -> Optional[str]:
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

    print(
        f"  Trend: {trend} (recent avg: {avg_recent:.0f}, "
        f"earlier avg: {avg_earlier:.0f} gCO2eq/kWh)"
    )
    return trend


def iso_now() -> str:
    """Return current UTC time in ISO 8601 format."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
