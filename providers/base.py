"""Shared utilities for all providers."""

import hashlib
import json
import os
import tempfile
import threading
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
# 10s is ample for these small JSON grid endpoints; the old 30s meant a single
# hung host could stall a check for ~90s (3 attempts) of pure runner-on time
DEFAULT_TIMEOUT = 10
MAX_RETRIES = 2
RETRY_DELAY = 5

# Upper bound on pooled connections. Sized to cover a fanned-out multi-zone run
# (see MAX_ZONE_WORKERS) hitting several providers at once
MAX_POOL = 16

# A descriptive User-Agent. Some public grid APIs (notably AEMO) reject or
# silently empty the default python-requests UA, which breaks them on shared
# CI runner IPs, so identify ourselves with a real one
USER_AGENT = (
    "carbon-aware-dispatcher/1.1 (+https://github.com/peterklingelhofer/carbon-aware-dispatcher)"
)


def _build_session():
    """A shared session for connection pooling and keep-alive.

    Reusing TCP+TLS connections across a multi-zone run (and repeated calls to
    the same provider) avoids a fresh handshake per request, the single most
    CPU-expensive part of this otherwise I/O-bound process. We do our own
    retries in request(), so the adapter retries zero times itself.
    """
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=MAX_POOL, pool_maxsize=MAX_POOL, max_retries=0
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_SESSION = _build_session()

# Canonical lifecycle emission factors in gCO2eq/kWh by generic fuel name.
# IPCC AR5 (2014) lifecycle medians. This is the single source of truth: every
# provider maps its own fuel labels to these names instead of hardcoding values,
# so the numbers can never silently drift apart across providers.
FUEL_FACTORS = {
    "coal": 820,
    "lignite": 1050,  # brown coal / peat-class
    "gas": 490,
    "oil": 650,
    "nuclear": 12,
    "solar": 45,
    "wind": 12,
    "hydro": 24,
    "geothermal": 38,
    "biomass": 230,
    "marine": 17,
    "waste": 580,
    # Non-IPCC composite buckets used by some providers:
    "thermal_mix": 750,  # India "thermal" = blended coal+gas, no single class
    "other": 300,  # catch-all / imports / unknown-but-counted
}

# Lifecycle emission factors keyed by EIA fuel type code, sourced from the
# canonical table above.
EIA_EMISSION_FACTORS = {
    "COL": FUEL_FACTORS["coal"],
    "NG": FUEL_FACTORS["gas"],
    "OIL": FUEL_FACTORS["oil"],
    "NUC": FUEL_FACTORS["nuclear"],
    "SUN": FUEL_FACTORS["solar"],
    "WND": FUEL_FACTORS["wind"],
    "WAT": FUEL_FACTORS["hydro"],
    "GEO": FUEL_FACTORS["geothermal"],
    "BIO": FUEL_FACTORS["biomass"],
    "SNB": FUEL_FACTORS["solar"],  # solar non-billing (behind-the-meter)
    "OTH": FUEL_FACTORS["other"],
    # BAT and PS are storage, so they are excluded from the mix
}

# Fuel codes that represent storage
# Storage discharge is not zero-carbon and double-counts energy already
# counted at generation, so it is excluded from the weighted-average denominator
# BAT = battery storage, PS = pumped-storage hydro
EIA_STORAGE_FUELS = {"BAT", "PS"}

# Fallback factor for unknown fuel codes (warned about, then applied)
DEFAULT_FUEL_FACTOR = FUEL_FACTORS["other"]

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

# Real-world equivalence factors for translating grams of CO2 into relatable
# units. Sourced from the US EPA Greenhouse Gas Equivalencies Calculator.
# Average passenger car: ~400 gCO2/mile = ~250 gCO2/km.
CO2_GRAMS_PER_KM_DRIVEN = 250
# One smartphone charged: ~8.22 gCO2 (EPA).
CO2_GRAMS_PER_PHONE_CHARGE = 8.22
# CO2 sequestered by one tree in a year: ~21 kg = 21000 gCO2 (EPA, ~0.06 g/min).
CO2_GRAMS_PER_TREE_YEAR = 21000


# Status codes worth retrying: transient server errors and rate limiting
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Upper bound (seconds) we will honor from a Retry-After header, so a hostile
# or misconfigured server cannot stall a CI run for minutes
MAX_RETRY_AFTER = 60

# Category of the most recent request() failure, so the dispatcher can report
# *why* a zone was skipped (auth failed / rate limited / network error / ...)
# rather than a flat "API error". Stored thread-locally so concurrent zone
# checks (the dispatcher fans them out across a thread pool) never clobber each
# other's reason; each worker reads back the reason its own request() set
_failure_state = threading.local()


def _set_failure_reason(reason):
    _failure_state.reason = reason


def last_failure_reason():
    """Return the category of the most recent request() failure on this thread, or None."""
    return getattr(_failure_state, "reason", None)


# Short-lived disk cache for GET/JSON reads. Grid feeds only refresh every
# 5-30 min, so a job that composes several calls (or back-to-back CLI runs on the
# same host) can reuse a recent reading instead of re-fetching, saving energy on
# both ends, including the free grid-operator APIs we depend on. Opt-in via
# CARBON_CACHE_TTL (seconds; 0/unset disables), so direct library/test callers are
# unaffected; the container and action turn it on by default. Cache stores only
# the parsed public reading, never the request URL (which may carry a token); the
# filename is a hash of method+url+parse, so different tokens stay isolated.


def _cache_ttl() -> int:
    try:
        return int(os.environ.get("CARBON_CACHE_TTL", "0"))
    except ValueError:
        return 0


def _cache_dir() -> str:
    return os.environ.get("CARBON_CACHE_DIR") or os.path.join(
        tempfile.gettempdir(), "carbon-aware-cache"
    )


def _cache_path(method: str, url: str, parse: str) -> str:
    key = hashlib.sha256(f"{method}\0{url}\0{parse}".encode()).hexdigest()
    return os.path.join(_cache_dir(), key + ".json")


def _cache_read(method: str, url: str, parse: str) -> Any:
    """Return the full cache entry (data, ts, validators) regardless of freshness.

    Returns None on miss/error. The caller checks the timestamp against the TTL
    and, when stale, uses the stored ETag / Last-Modified to revalidate cheaply.
    """
    if method != "GET" or parse != "json":
        return None
    try:
        with open(_cache_path(method, url, parse)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _cache_put(
    method: str,
    url: str,
    parse: str,
    data: Any,
    etag: Any = None,
    last_modified: Any = None,
) -> None:
    """Best-effort write; cache failures must never break a request.

    Stores the validators (ETag / Last-Modified) alongside the data so a later
    stale read can ask the server "still current?" and accept a tiny 304 instead
    of refetching the whole payload.
    """
    if method != "GET" or parse != "json":
        return
    # Only persist validators that are real strings; a mock or odd header type
    # must not make the entry unserializable (json.dump would raise).
    etag = etag if isinstance(etag, str) else None
    last_modified = last_modified if isinstance(last_modified, str) else None
    path = _cache_path(method, url, parse)
    try:
        os.makedirs(_cache_dir(), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as fh:
            json.dump(
                {"ts": time.time(), "data": data, "etag": etag, "last_modified": last_modified},
                fh,
            )
        os.replace(tmp, path)  # atomic, so concurrent writers can't tear a file
    except (OSError, TypeError, ValueError):
        pass


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
    headers = dict(headers or {})
    # Identify ourselves unless the caller already set a UA
    headers.setdefault("User-Agent", USER_AGENT)
    # Reset the per-call failure reason; set on any failure path below so the
    # dispatcher can report *why* a zone was skipped (see _failure_state)
    _set_failure_reason(None)

    verb = method.upper()
    ttl = _cache_ttl()
    cache_entry = _cache_read(verb, url, parse) if ttl > 0 else None
    if cache_entry is not None and time.time() - cache_entry.get("ts", 0) <= ttl:
        return cache_entry["data"]
    # Stale (or absent): if we have a validator, ask the server to confirm the
    # cached copy is still current; a 304 avoids re-downloading the payload.
    if cache_entry is not None:
        if cache_entry.get("etag"):
            headers.setdefault("If-None-Match", cache_entry["etag"])
        if cache_entry.get("last_modified"):
            headers.setdefault("If-Modified-Since", cache_entry["last_modified"])

    for attempt in range(MAX_RETRIES + 1):
        try:
            # Dispatch to the verb-specific helper on the shared pooled session
            if verb == "POST":
                response = _SESSION.post(url, headers=headers, json=json_body, timeout=timeout)
            elif verb == "PATCH":
                response = _SESSION.patch(url, headers=headers, json=json_body, timeout=timeout)
            else:
                response = _SESSION.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            print(f"::warning::Network error (attempt {attempt + 1}): {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
                continue
            _set_failure_reason("network error")
            return None

        # Not Modified: our cached copy is still current. Refresh its freshness
        # and serve it without re-parsing a payload we never received.
        if response.status_code == 304 and cache_entry is not None:
            _cache_put(
                verb,
                url,
                parse,
                cache_entry["data"],
                cache_entry.get("etag"),
                cache_entry.get("last_modified"),
            )
            return cache_entry["data"]

        if response.status_code == 200:
            if parse == "response":
                return response
            if parse == "text":
                return response.text
            try:
                data = response.json()
            except (ValueError, requests.exceptions.JSONDecodeError):
                print(f"::warning::Invalid JSON response: {response.text[:200]}")
                _set_failure_reason("invalid data")
                return None
            if ttl > 0:
                headers_obj = getattr(response, "headers", None) or {}
                _cache_put(
                    verb,
                    url,
                    parse,
                    data,
                    headers_obj.get("ETag"),
                    headers_obj.get("Last-Modified"),
                )
            return data

        print(
            f"::warning::API returned {response.status_code} "
            f"(attempt {attempt + 1}): {response.text[:200]}"
        )
        if response.status_code in (401, 403):
            print("::error::Authentication failed. Check your API token.")
            _set_failure_reason("auth failed")
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
            _set_failure_reason(f"HTTP {response.status_code}")
            return None

    # Retries exhausted on a retryable status (5xx or 429)
    _set_failure_reason("rate limited" if response.status_code == 429 else "upstream error")
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


def github_headers(token: str) -> dict:
    """Standard bearer auth + accept headers for the GitHub REST API."""
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
