"""Shared utilities for all providers."""

import hashlib
import json
import os
import tempfile
import threading
import time
from typing import Any, Optional, Protocol

import requests

from providers.factor_corpus import (  # noqa: E402
    FUEL_FACTORS,
)

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
# These are NOT defined here. They load from the versioned corpus vendored at
# data/emission-factors.json, which carbon-lens owns and both projects consume, so
# the two can no longer publish different numbers for the same physical quantity
# under the same citation. Every provider maps its own fuel labels onto these
# names instead of hardcoding values. See providers/factor_corpus.py for the
# loader and docs/VERIFICATION.md section 1 for the audit that produced the corpus.
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
# The mix is an assertion without a source, and it moves year to year: see docs/VERIFICATION.md.
FOSSIL_AVG_INTENSITY = 550

# Global average grid carbon intensity, the baseline the co2_saved benchmark is
# measured against. 458 gCO2e/kWh is Ember's Global Electricity Review 2026 figure
# for 2025, which is CO2-equivalent and incorporates IPCC lifecycle intensities, so
# it shares a system boundary with FUEL_FACTORS above. The IEA's better-known
# number (435 gCO2/kWh for 2025) is DIRECT emissions at the point of generation,
# scoring renewables and nuclear at exactly zero, so benchmarking lifecycle
# intensities against it would compare two different quantities. Replaces an
# earlier uncited 450; derivation in docs/VERIFICATION.md section 4
GLOBAL_AVG_INTENSITY = 458

# Power draw of one CI job, in kW: the share of server power a runner's vCPU
# slice actually represents, a fraction of the whole machine's draw. GitHub-hosted
# standard runners are 4 vCPU / 16 GB on public repos and 2 vCPU / 8 GB on
# private ones, and GitHub runs them on AMD EPYC 7763 hosts that expose 128
# threads, so a 4-vCPU job is 4/128 of one server. 13 W is that share: Cloud
# Carbon Footprint's Average Watts formula with its Azure AMD-EPYC-3rd-Gen
# coefficients, plus memory and PUE, cross-checked against Green Coding's
# measured-hardware power curve for the same processor.
#
# Replaces an earlier 0.05 (50 W), which is roughly the idle draw of a whole
# server socket applied as though it were a four-thread slice of a 128-thread
# machine, overstating every emissions and savings figure by about 4x. See
# docs/VERIFICATION.md section 3
CI_JOB_POWER_KW = 0.013

# Plausible range for the same quantity, propagated into the reported figures so
# they carry an error bar instead of a false point estimate. Low end: the Green
# Coding EPYC 7763 curve at CI-typical utilization. High end: Cloud Carbon
# Footprint's generic Azure coefficients at 100% utilization. A caller who
# supplies JOB_ENERGY_KWH or JOB_POWER_WATTS has measured the thing this range
# exists to bound, so their figures collapse to a point.
CI_JOB_POWER_KW_RANGE = (0.006, 0.025)

# Default estimated CI job duration in hours (15 minutes).
DEFAULT_JOB_DURATION_HOURS = 0.25

# Real-world equivalence factors for translating grams of CO2 into relatable
# units, from the US EPA Greenhouse Gas Equivalencies Calculator, "Calculations
# and References" page as published 2026-08-04 (eGRID2022 / 2022 inventory
# vintage). All three were checked against that page and all three had drifted:
# see docs/VERIFICATION.md section 5.

# EPA publishes 4.29 metric tons CO2e per vehicle per year over 10,917 miles,
# i.e. 393 gCO2e/mile. EPA publishes no per-km figure, so this is our conversion.
CO2_GRAMS_PER_KM_DRIVEN = 244
# One smartphone charged: 1.24e-5 metric tons = 12.4 gCO2. Note this uses EPA's
# DELIVERED electricity rate, which includes transmission and distribution losses.
CO2_GRAMS_PER_PHONE_CHARGE = 12.4
# EPA: "0.060 metric ton CO2 per urban tree planted per year" = 60000 g. This is
# NOT a general tree's annual sequestration. It is a survival-weighted average
# over the first ten years of a newly planted medium-growth urban seedling, and
# EPA states it "is not appropriate for reforestation projects".
CO2_GRAMS_PER_TREE_YEAR = 60000


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


def green_result(zone: str, intensity: int, max_carbon: float) -> IntensityResult:
    """Standard (is_green, intensity) result plus the canonical one-line report.

    Every provider that resolves an integer intensity ends the same way: compare
    to the threshold, print the verdict, return the pair. Centralizing that tail
    keeps the wording identical across providers
    """
    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


def _fuel_matches(fuel: str, names: Any, substring: bool) -> bool:
    return any(n in fuel for n in names) if substring else fuel in names


def _fuel_factor(fuel: str, factors: dict, substring: bool) -> Optional[int]:
    if not substring:
        return factors.get(fuel)
    return next((factor for key, factor in factors.items() if key in fuel), None)


def mix_to_intensity(
    fuel_mix: dict,
    factors: dict,
    storage_fuels: Any = frozenset(),
    substring: bool = False,
    on_unknown: str = "fallback",
) -> Optional[int]:
    """Weighted-average gCO2eq/kWh from a {fuel: MW} mix, or None.

    factors maps a fuel name to its lifecycle factor. storage_fuels are dropped:
    storage discharge is not zero-carbon and double-counts energy already measured
    at generation. With substring=False a fuel must match a factors key exactly;
    with substring=True it need only contain a key, for feeds whose labels are
    free-form. on_unknown="fallback" counts unknown fuels at DEFAULT_FUEL_FACTOR
    after a warning; on_unknown="skip" drops them
    """
    total_gen = 0.0
    weighted = 0.0
    for fuel, mw in fuel_mix.items():
        if mw is None or mw <= 0:
            continue
        if _fuel_matches(fuel, storage_fuels, substring):
            continue
        factor = _fuel_factor(fuel, factors, substring)
        if factor is None:
            if on_unknown == "skip":
                continue
            print(
                f"::warning::Unknown fuel type '{fuel}', using fallback "
                f"{DEFAULT_FUEL_FACTOR} gCO2eq/kWh"
            )
            factor = DEFAULT_FUEL_FACTOR
        total_gen += mw
        weighted += mw * factor
    if total_gen <= 0:
        return None
    return round(weighted / total_gen)


def flatten_mix(data: Any) -> dict:
    """Coerce a free-form generation payload into a {lowercased_label: MW} dict.

    Accepts a flat {label: value} object or a list of such objects (the two
    shapes these keyless feeds return). Values may be numbers or numeric strings;
    non-string keys, non-numeric values, and non-positive amounts are dropped.
    Returns {} when nothing usable is present
    """
    if isinstance(data, dict):
        items = list(data.items())
    elif isinstance(data, list):
        items = [(k, v) for entry in data if isinstance(entry, dict) for k, v in entry.items()]
    else:
        return {}

    mix: dict = {}
    for key, value in items:
        if not isinstance(key, str):
            continue
        if isinstance(value, (int, float)):
            mw = float(value)
        elif isinstance(value, str):
            try:
                mw = float(value)
            except (ValueError, TypeError):
                continue
        else:
            continue
        if mw <= 0:
            continue
        label = key.lower().strip()
        mix[label] = mix.get(label, 0.0) + mw
    return mix


def iso_now() -> str:
    """Return current UTC time in ISO 8601 format."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def github_headers(token: str) -> dict:
    """Standard bearer auth + accept headers for the GitHub REST API."""
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


_CI_SECRET_INSTRUCTIONS = {
    "GITHUB_ACTIONS": "Settings -> Secrets and variables -> Actions",
    "GITLAB_CI": "Settings -> CI/CD -> Variables",
    "CIRCLECI": "Project Settings -> Environment Variables",
    "BITBUCKET_BUILD_NUMBER": "Repository settings -> Pipelines -> Repository variables",
    "TF_BUILD": "Pipeline -> Edit -> Variables",
    "TRAVIS": "Repository Settings -> Environment Variables",
    "JENKINS_URL": "Manage Jenkins -> Credentials (or pipeline environment block)",
}


def ci_secret_hint(secret_name: str) -> str:
    """Return a platform-specific hint for where to add a CI secret."""
    for env_var, location in _CI_SECRET_INSTRUCTIONS.items():
        if os.environ.get(env_var):
            return f"add it as a secret named {secret_name} ({location})"
    return f"set {secret_name} as a secret or environment variable in your CI/CD platform"
