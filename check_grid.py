"""Carbon-Aware Dispatcher - checks grid carbon intensity and dispatches workflows."""

import os
import sys
import time as _time
from datetime import datetime, timezone

import requests

import ledger
import pr_comment
from providers import (
    AUTO_CLEANEST_ZONES,
    AUTO_ESCAPE_COAL_ZONES,
    AUTO_GREEN_ZONES,
    AUTO_GREEN_ZONES_FULL,
    ESCAPE_COAL_MAPPINGS,
    HEURISTIC_FORECAST_PROVIDERS,
    NEAREST_ZONES_BY_OFFSET,
    PROVIDER_AEMO,
    PROVIDER_CANADA,
    PROVIDER_EIA,
    PROVIDER_ELECTRICITY_MAPS,
    PROVIDER_ENTSOE,
    PROVIDER_ESKOM,
    PROVIDER_GRID_INDIA,
    PROVIDER_ONS_BRAZIL,
    PROVIDER_OPEN_METEO,
    PROVIDER_TAIWAN,
    PROVIDER_UK,
    aemo,
    azure_pricing,
    canada,
    detect_provider,
    eia,
    electricity_maps,
    entsoe,
    eskom,
    grid_india,
    gridstatus,
    ons_brazil,
    open_meteo,
    sort_auto_green_by_time,
    taiwan,
    uk,
)
from providers.base import (
    CI_JOB_POWER_KW,
    CO2_GRAMS_PER_KM_DRIVEN,
    CO2_GRAMS_PER_PHONE_CHARGE,
    CO2_GRAMS_PER_TREE_YEAR,
    DEFAULT_JOB_DURATION_HOURS,
    DEFAULT_TIMEOUT,
    GLOBAL_AVG_INTENSITY,
    last_failure_reason,
)
from providers.runners import (
    detect_cloud_zone,
    format_runner_label,
    get_azure_region,
    get_cloud_region,
    get_gcp_region,
)

# Exit codes
EXIT_SUCCESS = 0
EXIT_FAILURE = 1

# Defaults
DEFAULT_MAX_CARBON = 250
DEFAULT_REF = "main"
DEFAULT_WAIT_INTERVAL = 300  # 5 minutes between re-checks
MAX_WAIT_CAP = 360  # GitHub Actions max job timeout is 6 hours


def get_required_env(name):
    """Get a required environment variable or exit with an error."""
    value = os.environ.get(name)
    if not value:
        print(f"::error::Required environment variable {name} is not set or empty.")
        sys.exit(EXIT_FAILURE)
    return value


def _convert_env(name, raw, default, converter):
    """Convert a raw env value via converter, exiting on malformed input.

    Falls back to default when raw is unset/empty so existing default
    behavior (including policy-file fallbacks) is preserved
    """
    if raw is None or raw == "":
        return default
    try:
        return converter(raw)
    except (ValueError, TypeError):
        print(f"::error::Invalid {name} value '{raw}', expected a number")
        sys.exit(EXIT_FAILURE)


def _env_float(name, default, raw=None):
    """Read name from the environment as a float, exiting on malformed input.

    Pass an already-resolved raw string to honor policy-file precedence
    """
    if raw is None:
        raw = os.environ.get(name)
    return _convert_env(name, raw, default, float)


def _env_int(name, default, raw=None):
    """Read name from the environment as an int, exiting on malformed input.

    Pass an already-resolved raw string to honor policy-file precedence
    """
    if raw is None:
        raw = os.environ.get(name)
    return _convert_env(name, raw, default, int)


def _soft_float(name, default=None):
    """Read name as a float, warning and returning default on malformed input.

    Unlike _env_float, this never exits: it is for optional tuning inputs where a
    bad value should degrade gracefully rather than break CI.
    """
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"::warning::Invalid {name} '{raw}'; ignoring")
        return default


# ---------------------------------------------------------------------------
# Provider dispatch registry: maps provider IDs to modules.
# Each module must have: check_carbon_intensity(zone, max_carbon, *extra_args)
# Optional: get_forecast(zone, max_carbon, *extra_args), get_history_trend(zone, *extra_args)
# ---------------------------------------------------------------------------
_PROVIDER_MODULES = {
    PROVIDER_UK: uk,
    PROVIDER_AEMO: aemo,
    PROVIDER_ENTSOE: entsoe,
    PROVIDER_OPEN_METEO: open_meteo,
    PROVIDER_GRID_INDIA: grid_india,
    PROVIDER_ONS_BRAZIL: ons_brazil,
    PROVIDER_ESKOM: eskom,
    PROVIDER_CANADA: canada,
    PROVIDER_TAIWAN: taiwan,
    PROVIDER_ELECTRICITY_MAPS: electricity_maps,
    PROVIDER_EIA: eia,
}

# Providers that need an extra auth token passed to their functions.
# Maps (provider, function_type) -> env-key-based extra arg.
_PROVIDER_AUTH_ARGS = {
    PROVIDER_ENTSOE: lambda keys: [keys.get("entsoe_token", "")],
    PROVIDER_ELECTRICITY_MAPS: lambda keys: [keys.get("emaps_api_key", "")],
    PROVIDER_EIA: lambda keys: [keys.get("eia_api_key", "")],
}


def _get_extra_args(provider, api_keys):
    """Get extra auth arguments for a provider, if any."""
    resolver = _PROVIDER_AUTH_ARGS.get(provider)
    return resolver(api_keys) if resolver else []


def check_carbon_intensity(
    zone, max_carbon, provider, eia_api_key="", emaps_api_key="", entsoe_token=""
):
    """Check carbon intensity using the appropriate provider.

    If the primary provider fails and Open-Meteo has coordinates for the zone,
    automatically falls back to Open-Meteo weather-based estimation.
    """
    module = _PROVIDER_MODULES.get(provider)
    if module is None:
        print(f"::warning::Unknown provider '{provider}' for zone '{zone}'")
        return None, None
    extra = _get_extra_args(
        provider,
        {
            "eia_api_key": eia_api_key,
            "emaps_api_key": emaps_api_key,
            "entsoe_token": entsoe_token,
        },
    )
    result = module.check_carbon_intensity(zone, max_carbon, *extra)

    # Fallback: if primary provider failed, try Open-Meteo estimation
    if result == (None, None) and provider != PROVIDER_OPEN_METEO:
        from providers.open_meteo import ZONE_COORDINATES

        if zone in ZONE_COORDINATES:
            print(f"  Falling back to Open-Meteo estimate for zone {zone}...")
            result = open_meteo.check_carbon_intensity(zone, max_carbon)

    return result


def _apply_consumption_intensity(zone, max_carbon, is_green, intensity, entsoe_token):
    """Replace production intensity with flow-traced consumption intensity.

    Only applies to EU zones in the traced network with an ENTSO-E token. On any
    failure (untraceable zone, no token, computation error) returns the original
    (is_green, intensity) unchanged, so consumption mode never degrades results.
    """
    from providers.flow_tracing import TRACED_ZONES, compute_consumption_intensities

    if zone not in TRACED_ZONES:
        print(f"  Consumption mode: zone {zone} not in the traced EU network, using production.")
        return is_green, intensity

    consumption = compute_consumption_intensities(entsoe_token)
    cons_intensity = consumption.get(zone)
    if cons_intensity is None:
        print(f"  Consumption mode: no flow-traced value for {zone}, using production.")
        return is_green, intensity

    cons_rounded = round(cons_intensity)
    new_green = cons_rounded <= max_carbon
    delta = cons_rounded - intensity
    print(
        f"  Consumption-based: {cons_rounded} gCO2eq/kWh "
        f"(production was {intensity}, {delta:+d} from imports/exports)"
    )
    return new_green, cons_rounded


def get_forecast(
    zone,
    max_carbon,
    provider,
    gridstatus_api_key="",
    emaps_api_key="",
    entsoe_token="",
    eia_api_key="",
):
    """Get forecast using the appropriate provider."""
    module = _PROVIDER_MODULES.get(provider)
    if module is None:
        return None, None
    extra = _get_extra_args(
        provider,
        {
            "eia_api_key": eia_api_key,
            "emaps_api_key": emaps_api_key,
            "entsoe_token": entsoe_token,
        },
    )
    result = module.get_forecast(zone, max_carbon, *extra)
    # EIA doesn't have its own forecast; use GridStatus if available
    if provider == PROVIDER_EIA and result == (None, None) and gridstatus_api_key:
        return gridstatus.get_forecast(zone, max_carbon, gridstatus_api_key)
    if provider == PROVIDER_EIA and not gridstatus_api_key:
        print(
            "  No forecast available for US zones without a GridStatus API key. "
            "Register free at https://www.gridstatus.io"
        )
    return result


def get_history_trend(zone, provider, eia_api_key="", emaps_api_key="", entsoe_token=""):
    """Get history trend using the appropriate provider."""
    module = _PROVIDER_MODULES.get(provider)
    if module is None:
        return None
    extra = _get_extra_args(
        provider,
        {
            "eia_api_key": eia_api_key,
            "emaps_api_key": emaps_api_key,
            "entsoe_token": entsoe_token,
        },
    )
    return module.get_history_trend(zone, *extra)


def _emit_token_warnings(zones_config, emaps_api_key, entsoe_token):
    """Emit upfront warnings about missing tokens for zones that need them.

    Tells users exactly what tokens to add and which zones they enable.
    Only warns once per missing token type.
    """
    from providers.open_meteo import ZONE_COORDINATES

    needs_emaps = []
    needs_entsoe = []

    for entry in zones_config:
        zone = entry["zone"]
        provider = detect_provider(zone, entsoe_token)
        if provider == PROVIDER_ELECTRICITY_MAPS and not emaps_api_key:
            if zone not in ZONE_COORDINATES:
                needs_emaps.append(zone)
        # Also warn if ENTSO-E zones would work with a token but aren't
        if not entsoe_token:
            from providers.entsoe import ENTSOE_AREA_CODES

            if zone in ENTSOE_AREA_CODES and provider != PROVIDER_ENTSOE:
                needs_entsoe.append(zone)

    if needs_emaps:
        zones_str = ", ".join(needs_emaps[:5])
        extra = f" (+{len(needs_emaps) - 5} more)" if len(needs_emaps) > 5 else ""
        print(
            f"::notice::Zones [{zones_str}{extra}] need electricity_maps_token. "
            f"Get free at https://portal.electricitymaps.com/"
        )

    if needs_entsoe:
        zones_str = ", ".join(needs_entsoe[:5])
        extra = f" (+{len(needs_entsoe) - 5} more)" if len(needs_entsoe) > 5 else ""
        print(
            f"::notice::Zones [{zones_str}{extra}] would use ENTSO-E with entsoe_token. "
            f"Get free at https://transparency.entsoe.eu/"
        )


def _cost_weight():
    """Read COST_WEIGHT (0..1) from the environment, clamped. 0 = carbon only."""
    return max(0.0, min(1.0, _soft_float("COST_WEIGHT", 0.0)))


def rank_by_cost_carbon(candidates, cost_weight):
    """Pick the best (zone, intensity, label) by a cost+carbon blend.

    candidates: list of (zone, intensity, label). Fetches a representative
    regional VM price per zone, min-max normalizes price and carbon across the
    candidates, and minimizes cost_weight*price + (1 - cost_weight)*carbon.
    Returns None to fall back to carbon-only ranking when any price is missing.
    """
    if not candidates:
        return None
    prices = []
    for zone, _, _ in candidates:
        price = azure_pricing.get_region_price(get_azure_region(zone))
        if price is None:
            print(f"::warning::No price for zone '{zone}'; using carbon-only ranking")
            return None
        prices.append(price)

    intensities = [c[1] for c in candidates]

    def _norm(vals):
        lo, hi = min(vals), max(vals)
        span = hi - lo
        return [0.0 if span == 0 else (v - lo) / span for v in vals]

    norm_price = _norm(prices)
    norm_carbon = _norm(intensities)

    best_i = 0
    best_score = None
    for i in range(len(candidates)):
        score = cost_weight * norm_price[i] + (1 - cost_weight) * norm_carbon[i]
        if best_score is None or score < best_score:
            best_score, best_i = score, i

    zone, intensity, label = candidates[best_i]
    set_output("selected_cost_usd_hr", str(prices[best_i]))
    print(
        f"  Cost+carbon pick: {zone} (intensity {intensity}, "
        f"${prices[best_i]:.4f}/hr, cost_weight {cost_weight})"
    )
    return zone, intensity, label


def check_multiple_zones(
    zones_config, max_carbon, eia_api_key="", emaps_api_key="", entsoe_token="", collect=None
):
    """Check carbon intensity for multiple zones, return the best green option.

    Checks free-provider zones first (to avoid exhausting paid API rate limits),
    then token-requiring zones. Falls back to Open-Meteo for zones without tokens.

    Returns (best_zone, best_intensity, best_runner_label, skipped) where
    skipped is a list of (zone, reason) for zones that could not be checked.

    If collect is a list, every successfully measured zone is appended as a
    (zone, intensity) tuple (including zones over the threshold), so callers
    can build a routing comparison without extra API calls.
    """
    best_zone = None
    best_intensity = None
    best_label = None
    skipped = []
    green_candidates = []

    # Sort: free providers first, then token-requiring ones.
    # This avoids exhausting Electricity Maps rate limits (50 req/hr)
    # when free providers could have answered the question.
    from providers.open_meteo import ZONE_COORDINATES

    def _provider_cost(entry):
        provider = detect_provider(entry["zone"], entsoe_token)
        if provider in (
            PROVIDER_UK,
            PROVIDER_EIA,
            PROVIDER_AEMO,
            PROVIDER_GRID_INDIA,
            PROVIDER_ONS_BRAZIL,
            PROVIDER_ESKOM,
        ):
            return 0  # Free, no rate limit concern
        if provider == PROVIDER_OPEN_METEO:
            return 1  # Free, high rate limit
        if provider == PROVIDER_ENTSOE:
            return 2  # Free token, 400 req/min
        return 3  # Electricity Maps: 50 req/hr

    sorted_zones = sorted(zones_config, key=_provider_cost)

    for entry in sorted_zones:
        zone = entry["zone"]
        label = entry.get("runner_label")
        provider = detect_provider(zone, entsoe_token)

        # Fall back to Open-Meteo if no Electricity Maps token
        if provider == PROVIDER_ELECTRICITY_MAPS and not emaps_api_key:
            if zone in ZONE_COORDINATES:
                provider = PROVIDER_OPEN_METEO
                print(f"  Zone {zone}: no electricity_maps_token, using Open-Meteo estimate")
            else:
                reason = "no electricity_maps_token"
                skipped.append((zone, reason))
                continue

        is_green, intensity = check_carbon_intensity(
            zone, max_carbon, provider, eia_api_key, emaps_api_key, entsoe_token
        )
        if is_green is None:
            # Surface why it failed (auth failed / rate limited / network error /
            # ...) instead of a flat "API error", so users can act on it
            skipped.append((zone, last_failure_reason() or "API error"))
        elif intensity is not None:
            # Record every measured zone (green or not) for the comparison panel
            if collect is not None:
                collect.append((zone, intensity))
            if is_green:
                green_candidates.append((zone, intensity, label))
                if best_intensity is None or intensity < best_intensity:
                    best_zone = zone
                    best_intensity = intensity
                    best_label = label

    # When cost weighting is on, re-rank the green zones by a cost+carbon blend
    cost_weight = _cost_weight()
    if cost_weight > 0 and green_candidates:
        ranked = rank_by_cost_carbon(green_candidates, cost_weight)
        if ranked:
            best_zone, best_intensity, best_label = ranked

    return best_zone, best_intensity, best_label, skipped


def _detect_utc_offset():
    """Detect the UTC offset from environment or system timezone.

    Checks TZ env var first, then falls back to system local time offset.
    Returns a numeric offset (e.g., -8, 5.5) or None.
    """
    import math

    # Try TZ env var (common on CI runners)
    tz_env = os.environ.get("TZ", "")
    if tz_env:
        # Handle common offset formats: UTC+5, UTC-8, GMT+5:30, Etc/GMT-5
        for prefix in ("UTC", "GMT", "Etc/GMT"):
            if tz_env.upper().startswith(prefix.upper()):
                rest = tz_env[len(prefix) :]
                if not rest:
                    return 0
                try:
                    # Handle +5:30 format
                    if ":" in rest:
                        parts = rest.split(":")
                        hours = int(parts[0])
                        minutes = int(parts[1]) if len(parts) > 1 else 0
                        sign = -1 if hours < 0 else 1
                        # Etc/GMT offsets are inverted (Etc/GMT-5 = UTC+5)
                        if prefix.upper() == "ETC/GMT":
                            return -(hours + sign * minutes / 60)
                        return hours + sign * minutes / 60
                    offset = float(rest)
                    if prefix.upper() == "ETC/GMT":
                        return -offset
                    return offset
                except (ValueError, IndexError):
                    pass

    # Fall back to system local time
    try:
        local_offset_seconds = datetime.now().astimezone().utcoffset().total_seconds()
        offset_hours = local_offset_seconds / 3600
        # Round to nearest 0.5 (handles India's +5:30, etc.)
        return math.floor(offset_hours * 2 + 0.5) / 2
    except (AttributeError, TypeError):
        return None


def expand_auto_zones(zones_str):
    """Expand auto presets into curated zone lists.

    Supported presets:
      - auto:green: 15 curated zones frequently powered by clean energy
      - auto:cleanest: ALL free-provider zones, picks the single cleanest
      - auto:escape-coal: Routes dirty-grid users to nearest clean alternatives

    Sorts zones by time-of-day priority so the most likely green zones
    are checked first (e.g., solar zones during their daytime).
    Returns the expanded zones_config list, or None if not an auto preset.
    """
    normalized = zones_str.strip().lower()
    utc_hour = datetime.now(timezone.utc).hour

    if normalized == "auto:detect":
        zone, source = detect_cloud_zone()
        if zone:
            print(f"Auto-detected grid zone: {zone} (from {source})")
            return [{"zone": zone, "runner_label": None}]
        print(
            "::notice::No cloud region detected (expected on GitHub-hosted runners, "
            "which don't expose one). Falling back to auto:cleanest, the greenest free "
            "zone worldwide. Set grid_zones explicitly to pin a region."
        )
        return sort_auto_green_by_time(list(AUTO_CLEANEST_ZONES), utc_hour)

    if normalized == "auto:nearest":
        offset = _detect_utc_offset()
        if offset is not None:
            zones = NEAREST_ZONES_BY_OFFSET.get(offset)
            if zones:
                print(f"auto:nearest: UTC offset {offset:+g} -> checking {', '.join(zones)}")
                return [{"zone": z, "runner_label": None} for z in zones]
        print("::notice::Could not detect timezone. Falling back to auto:cleanest.")
        return sort_auto_green_by_time(list(AUTO_CLEANEST_ZONES), utc_hour)

    if normalized == "auto:green":
        return sort_auto_green_by_time(list(AUTO_GREEN_ZONES), utc_hour)

    if normalized == "auto:green:full":
        return sort_auto_green_by_time(list(AUTO_GREEN_ZONES_FULL), utc_hour)

    if normalized == "auto:cleanest":
        return sort_auto_green_by_time(list(AUTO_CLEANEST_ZONES), utc_hour)

    if normalized == "auto:escape-coal":
        return sort_auto_green_by_time(list(AUTO_ESCAPE_COAL_ZONES), utc_hour)

    # auto:escape-coal:ZONE escapes from a specific dirty zone
    if normalized.startswith("auto:escape-coal:"):
        dirty_zone = zones_str.strip().split(":", 2)[2].strip()
        alternatives = ESCAPE_COAL_MAPPINGS.get(dirty_zone)
        if alternatives:
            zones = [{"zone": z, "runner_label": None} for z in alternatives]
            return sort_auto_green_by_time(zones, utc_hour)
        # Unknown dirty zone: use default escape zones
        print(f"::warning::No escape mapping for '{dirty_zone}', using default clean zones")
        return sort_auto_green_by_time(list(AUTO_ESCAPE_COAL_ZONES), utc_hour)

    return None


def parse_zones_input(zones_str):
    """Parse the zones input string into a list of zone configs.

    Supports three formats:
      - Simple comma-separated: "GB,CISO,ERCO"
      - With runner labels: "GB:runner-uk,CISO:runner-us-cal"
      - Auto preset: "auto:green"
    """
    # Check for auto presets first
    auto = expand_auto_zones(zones_str)
    if auto is not None:
        return auto

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


def estimate_carbon_savings(intensity, job_minutes=None):
    """Estimate CO2 saved by running on a clean grid vs. the global average.

    Returns (co2_saved_grams, badge_url) or (0, badge_url) if no savings.
    co2_saved_grams: estimated grams of CO2 avoided.
    badge_url: shields.io badge URL for embedding in READMEs.
    """
    if intensity is None:
        return 0, None

    duration_hours = (job_minutes / 60) if job_minutes else DEFAULT_JOB_DURATION_HOURS
    energy_kwh = CI_JOB_POWER_KW * duration_hours

    actual_co2 = intensity * energy_kwh  # gCO2 from clean grid
    baseline_co2 = GLOBAL_AVG_INTENSITY * energy_kwh  # gCO2 from average grid

    saved = max(0, round(baseline_co2 - actual_co2, 1))

    # Generate shields.io badge
    if saved > 1000:
        label = f"{saved / 1000:.1f}kg"
    else:
        label = f"{saved:.0f}g"

    color = "brightgreen" if saved > 0 else "yellow"
    badge_url = (
        f"https://img.shields.io/badge/CO2_saved-{label}_CO2-{color}"
        f"?style=flat&logo=leaf&logoColor=white"
    )

    return saved, badge_url


def estimate_emissions(intensity, job_minutes=None):
    """Estimate gCO2 emitted by this CI run on a grid of the given intensity.

    The counterpart to estimate_carbon_savings: what the run actually produced,
    used for carbon budgeting. Returns grams, or 0 when intensity is unknown.
    """
    if intensity is None:
        return 0.0
    duration_hours = (job_minutes / 60) if job_minutes else DEFAULT_JOB_DURATION_HOURS
    duration_hours = max(0.0, duration_hours)
    return round(max(0.0, intensity) * CI_JOB_POWER_KW * duration_hours, 1)


def carbon_equivalents(grams):
    """Translate grams of CO2 into relatable real-world equivalents.

    Numbers like "12 g CO2" mean little on their own, so we convert to units a
    non-engineer can feel. Returns a dict with the numeric equivalents plus a
    short human-readable "phrase" that picks the most relatable unit for the
    magnitude (phone charges for tiny amounts, km not driven for larger ones).
    Returns zeros and an empty phrase when there is nothing to report.
    """
    if not grams or grams <= 0:
        return {"km_driven": 0, "phone_charges": 0, "tree_years": 0, "phrase": ""}

    km = grams / CO2_GRAMS_PER_KM_DRIVEN
    charges = grams / CO2_GRAMS_PER_PHONE_CHARGE
    tree_years = grams / CO2_GRAMS_PER_TREE_YEAR

    if km >= 1:
        phrase = f"~{km:.1f} km not driven"
    else:
        phrase = f"~{charges:.0f} phone charges"

    return {
        "km_driven": round(km, 3),
        "phone_charges": round(charges, 1),
        "tree_years": round(tree_years, 4),
        "phrase": phrase,
    }


# Default gCO2eq/kWh boundaries for the carbon tier "dial": at or below the first
# is green, at or below the second is amber, above is red.
DEFAULT_TIER_THRESHOLDS = (150.0, 300.0)


def parse_tier_thresholds(raw):
    """Parse "green,amber" gCO2 boundaries; fall back to defaults on bad input."""
    if not raw:
        return DEFAULT_TIER_THRESHOLDS
    try:
        parts = [float(p.strip()) for p in raw.split(",")]
    except ValueError:
        return DEFAULT_TIER_THRESHOLDS
    if len(parts) != 2 or parts[0] >= parts[1]:
        return DEFAULT_TIER_THRESHOLDS
    return (parts[0], parts[1])


def classify_tier(intensity, thresholds):
    """Classify carbon intensity into an adaptive-CI tier.

    Returns (tier, reason) where tier is "green", "amber", "red", or "unknown".
    Downstream jobs read carbon_tier to right-size their work to the grid instead
    of skipping entirely: full workloads on green, trimmed on amber, deferred on
    red.
    """
    if intensity is None:
        return "unknown", "carbon intensity unavailable"
    green, amber = thresholds
    if intensity <= green:
        return "green", f"{intensity:.0f} gCO2eq/kWh: run full workloads"
    if intensity <= amber:
        return "amber", f"{intensity:.0f} gCO2eq/kWh: trim non-critical work"
    return "red", f"{intensity:.0f} gCO2eq/kWh: defer heavy work"


# Set once per process when the cumulative ledger is updated, so write_job_summary
# can append a lifetime row. None when no ledger is configured or the update fails.
_lifetime_summary = None
_ledger_recorded = False
# Carbon-budget status for the current month, when a budget is configured.
_budget_summary = None


def _emit_budget_outputs(summary):
    """Compute and emit carbon-budget outputs from this month's emissions.

    Reads MONTHLY_BUDGET_GRAMS; no-op when unset. Sets budget_used_pct,
    budget_remaining_grams, budget_exceeded, and budget_state, and stores the
    result so the summary and PR comment can show it. Workflows enforce by
    gating on the budget_exceeded output.
    """
    global _budget_summary
    budget = _soft_float("MONTHLY_BUDGET_GRAMS")
    if not budget or budget <= 0:
        return

    mtd = float(summary.get("emitted_mtd", 0))
    used_pct = round(mtd / budget * 100, 1)
    remaining = round(max(0.0, budget - mtd), 1)
    exceeded = mtd >= budget
    state = "exceeded" if exceeded else ("warning" if used_pct >= 80 else "ok")

    set_output("budget_used_pct", str(used_pct))
    set_output("budget_remaining_grams", str(remaining))
    set_output("budget_exceeded", "true" if exceeded else "false")
    set_output("budget_state", state)
    _budget_summary = {
        "budget": budget,
        "mtd": mtd,
        "used_pct": used_pct,
        "remaining": remaining,
        "exceeded": exceeded,
        "state": state,
    }
    if exceeded:
        print(
            f"::warning::Carbon budget exceeded: {mtd:.0f} of {budget:.0f} gCO2eq this "
            "month. Gate on the budget_exceeded output to defer non-essential builds."
        )


def record_lifetime_savings(saved_grams, emitted_grams=0):
    """Append this run to the cumulative ledger (at most once/process).

    Reads the LEDGER config and optional GIST_TOKEN from the environment, records
    savings and emissions via the ledger module, then emits the lifetime and
    carbon-budget outputs. No-op when no ledger is configured. Never raises:
    bookkeeping must not break CI.
    """
    global _lifetime_summary, _ledger_recorded
    if _ledger_recorded:
        return
    config = os.environ.get("LEDGER", "")
    if not config:
        if os.environ.get("MONTHLY_BUDGET_GRAMS", ""):
            print("::warning::MONTHLY_BUDGET_GRAMS needs the ledger input to track spend")
        return
    _ledger_recorded = True

    token = os.environ.get("GIST_TOKEN", "")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary = ledger.record_savings(config, token, saved_grams, date_str, emitted_grams)
    if not summary:
        return

    _lifetime_summary = summary
    set_output("co2_saved_total_grams", str(round(summary["total_grams"], 1)))
    equiv = carbon_equivalents(summary["total_grams"])
    if equiv["phrase"]:
        set_output("co2_saved_total_equivalent", equiv["phrase"])
    if summary.get("badge_url"):
        set_output("lifetime_badge_url", summary["badge_url"])

    _emit_budget_outputs(summary)


# Fire the sticky PR comment at most once per process.
_pr_comment_done = False


def post_pr_comment_once(
    zone, intensity, is_green, max_carbon, co2_saved=0, dry_run=False, tier="unknown"
):
    """Post/update the sticky PR comment with the verdict (at most once/process).

    No-op unless PR_COMMENT is enabled. Reads the GitHub event context from the
    environment and degrades to a warning on any failure so CI is never broken.
    """
    global _pr_comment_done
    if _pr_comment_done:
        return
    if os.environ.get("PR_COMMENT", "").lower() != "true":
        return
    _pr_comment_done = True

    equiv = carbon_equivalents(co2_saved)["phrase"] if co2_saved else ""
    lifetime = _lifetime_summary["message"] if _lifetime_summary else ""
    body = pr_comment.build_comment(
        is_green=is_green,
        zone=zone,
        intensity=intensity,
        max_carbon=max_carbon,
        co2_saved=co2_saved,
        equivalent=equiv,
        lifetime=lifetime,
        dry_run=dry_run,
        tier=tier,
        budget=_budget_summary,
    )
    pr_comment.post_comment(
        os.environ.get("TARGET_REPO", ""),
        os.environ.get("GITHUB_TOKEN", ""),
        os.environ.get("GITHUB_EVENT_NAME", ""),
        os.environ.get("GITHUB_EVENT_PATH", ""),
        body,
    )


def set_savings_outputs(co2_saved, badge_url, intensity=None):
    """Emit the CO2 savings, shields badge, and human-equivalent outputs."""
    if co2_saved and co2_saved > 0:
        set_output("co2_saved_grams", str(co2_saved))
        equiv = carbon_equivalents(co2_saved)
        if equiv["phrase"]:
            set_output("co2_saved_equivalent", equiv["phrase"])
    if badge_url:
        set_output("carbon_badge_url", badge_url)
    record_lifetime_savings(co2_saved or 0, estimate_emissions(intensity))


def run_dry_run(
    zones_config,
    max_carbon,
    enable_forecast,
    eia_api_key,
    gridstatus_api_key,
    emaps_api_key,
    entsoe_token,
    runner_provider,
    runner_spec,
    github_run_id,
):
    """Report-only mode: measure the grid and report, but never gate the build.

    This is the zero-risk on-ramp. The action runs exactly as before, but
    instead of blocking on a dirty grid it always sets grid_clean=true (so any
    existing `if: grid_clean == 'true'` step still runs) and reports what it
    *would* have done. The honest verdict is exposed via the would_defer output
    and a clearly-labeled job summary, so teams can watch the savings for a
    while before turning on enforcement.
    """
    measured = []
    best_zone, best_intensity, best_label, skipped = check_multiple_zones(
        zones_config, max_carbon, eia_api_key, emaps_api_key, entsoe_token, collect=measured
    )

    # Pick a representative zone for reporting: the greenest measured one
    if measured:
        report_zone, report_intensity = min(measured, key=lambda r: r[1])
    elif best_zone is not None:
        report_zone, report_intensity = best_zone, best_intensity
    else:
        report_zone = zones_config[0]["zone"]
        report_intensity = None

    would_defer = report_intensity is not None and report_intensity > max_carbon

    # Never gate: downstream `if: grid_clean == 'true'` steps still run
    set_output("grid_clean", "true")
    set_output("dry_run", "true")
    set_output("would_defer", "true" if would_defer else "false")
    set_output("grid_zone", report_zone)
    if report_intensity is not None:
        set_output("carbon_intensity", str(report_intensity))
    set_runner_outputs(report_zone, best_label, runner_provider, runner_spec, github_run_id)

    co2_saved, badge_url = estimate_carbon_savings(report_intensity)
    set_savings_outputs(co2_saved, badge_url, report_intensity)

    forecast_at = None
    forecast_intensity = None
    forecast_heuristic = False
    if would_defer:
        provider = detect_provider(report_zone, entsoe_token)
        forecast_heuristic = provider in HEURISTIC_FORECAST_PROVIDERS
        forecast_at, forecast_intensity = get_forecast(
            report_zone,
            max_carbon,
            provider,
            gridstatus_api_key,
            emaps_api_key,
            entsoe_token,
            eia_api_key,
        )
        if forecast_at and forecast_at != "none_in_forecast":
            set_output("forecast_green_at", forecast_at)
            if forecast_intensity is not None:
                set_output("forecast_intensity", str(forecast_intensity))

    if would_defer:
        print(
            f"\n[dry-run] Would DEFER: cleanest zone {report_zone} is "
            f"{report_intensity} gCO2eq/kWh (over threshold {max_carbon}). "
            "Build proceeds anyway (report-only mode)."
        )
    else:
        intensity_str = report_intensity if report_intensity is not None else "unknown"
        print(
            f"\n[dry-run] Would DISPATCH: zone {report_zone} is "
            f"{intensity_str} gCO2eq/kWh (within threshold {max_carbon})."
        )

    write_job_summary(
        report_zone,
        report_intensity,
        not would_defer,
        max_carbon,
        forecast_at=forecast_at,
        forecast_intensity=forecast_intensity,
        skipped=skipped,
        co2_saved=co2_saved,
        comparison=measured,
        dry_run=True,
        forecast_heuristic=forecast_heuristic,
    )


def suggest_green_cron(zone):
    """Suggest the optimal cron schedule for a zone based on its energy type.

    Returns a cron expression string (e.g., '0 18 * * *') and a human
    description, or (None, None) if no suggestion is available.
    """
    from providers import (
        AUTO_CLEANEST_ZONES,
        AUTO_GREEN_ZONES,
        AUTO_GREEN_ZONES_FULL,
    )

    # Find zone metadata from our curated lists
    zone_meta = None
    for zone_list in (AUTO_GREEN_ZONES, AUTO_GREEN_ZONES_FULL, AUTO_CLEANEST_ZONES):
        for entry in zone_list:
            if entry["zone"] == zone:
                zone_meta = entry
                break
        if zone_meta:
            break

    if not zone_meta:
        return None, None

    energy_type = zone_meta.get("type", "unknown")
    utc_offset = zone_meta.get("utc_offset", 0)

    # NOTE: these time-of-day heuristics are rough generalizations and the
    # suggestion is advisory only. Real renewable output varies by region,
    # season, and weather (e.g. wind tends to be stronger at night in many
    # regions). Treat the cron as a starting point to tune
    if energy_type == "solar":
        # Best during local noon (12pm local = 12 - offset UTC)
        best_utc_hour = int((12 - utc_offset) % 24)
        cron = f"0 {best_utc_hour} * * *"
        desc = f"daily at {best_utc_hour}:00 UTC (solar peak ~12pm local in {zone})"
    elif energy_type == "wind":
        # Wind is stronger at night; target 2am local
        best_utc_hour = int((2 - utc_offset) % 24)
        cron = f"0 {best_utc_hour} * * *"
        desc = f"daily at {best_utc_hour}:00 UTC (wind peak ~2am local in {zone})"
    elif energy_type in ("hydro", "nuclear"):
        # Always-on, but off-peak demand = higher renewable share; target 3am local
        best_utc_hour = int((3 - utc_offset) % 24)
        cron = f"0 {best_utc_hour} * * *"
        desc = f"daily at {best_utc_hour}:00 UTC (off-peak ~3am local in {zone})"
    else:
        return None, None

    return cron, desc


def set_runner_outputs(zone, user_label, runner_provider, runner_spec, github_run_id):
    """Set runner-related outputs: runner_label, cloud_region, gcp_region, azure_region.

    If runner_provider is set (e.g., 'runson'), formats a provider-specific
    runner label. Otherwise uses the user-provided label from grid_zones.
    Always sets cloud regions for all three major providers.
    """
    # Always output cloud regions for all providers
    cloud_region = get_cloud_region(zone)
    set_output("cloud_region", cloud_region)
    set_output("gcp_region", get_gcp_region(zone))
    set_output("azure_region", get_azure_region(zone))

    # Provider-formatted label takes precedence over user label
    if runner_provider:
        formatted = format_runner_label(zone, runner_provider, github_run_id, runner_spec)
        if formatted:
            set_output("runner_label", formatted)
            return
        # Fall through to user label if formatting failed

    if user_label:
        set_output("runner_label", user_label)


def _emit_green_result(
    zone,
    intensity,
    label,
    max_carbon,
    dispatch_mode,
    repo,
    workflow_id,
    token,
    ref,
    runner_provider,
    runner_spec,
    github_run_id,
    *,
    waited_minutes=0,
    skipped=None,
    comparison=None,
):
    """Emit the shared 'grid is green' outputs, summary, and dispatch/print.

    Shared by the queue, single-zone, and multi-zone strategy branches:
    sets grid_clean/carbon_intensity outputs, runner outputs, carbon savings
    outputs, writes the job summary, then either dispatches the workflow (when
    dispatch_mode) or prints the inline green message
    """
    set_output("grid_clean", "true")
    set_output("carbon_intensity", str(intensity))
    set_runner_outputs(zone, label, runner_provider, runner_spec, github_run_id)
    co2_saved, badge_url = estimate_carbon_savings(intensity)
    set_savings_outputs(co2_saved, badge_url, intensity)
    write_job_summary(
        zone,
        intensity,
        True,
        max_carbon,
        waited_minutes=waited_minutes,
        skipped=skipped,
        co2_saved=co2_saved,
        comparison=comparison,
    )
    if dispatch_mode:
        print(f"\nGrid is clean! Zone {zone} ({intensity} gCO2eq/kWh). Triggering workflow...")
        trigger_workflow(repo, workflow_id, token, ref)
    else:
        print(f"\nGrid is clean! Zone {zone} ({intensity} gCO2eq/kWh)")


def render_routing_comparison(measured, chosen_zone):
    """Render an ASCII bar chart comparing carbon intensity across zones.

    measured: list of (zone, intensity) tuples for every zone we checked.
    chosen_zone: the zone the workflow was routed to.

    Returns a list of text lines (a fenced code block) showing each zone as a
    proportional bar, marking the chosen zone and the dirtiest avoided one,
    plus the delta vs that dirtiest candidate and vs the global average.
    Returns None when there is nothing meaningful to compare (fewer than two
    zones with data).
    """
    rows = [(z, i) for z, i in measured if i is not None]
    if len(rows) < 2:
        return None

    rows.sort(key=lambda r: r[1])
    cleanest = rows[0]
    dirtiest = rows[-1]
    chosen = next((r for r in rows if r[0] == chosen_zone), cleanest)

    peak = dirtiest[1] or 1
    name_width = max(len(z) for z, _ in rows)
    bar_width = 10

    lines = ["```text", "Carbon-aware routing"]
    lines.append("-" * (name_width + 32))
    for zone, intensity in rows:
        filled = max(1, round((intensity / peak) * bar_width))
        bar = "#" * filled + "." * (bar_width - filled)
        marker = "  <- routed here (cleanest)" if zone == chosen[0] else ""
        lines.append(f"  {zone:<{name_width}}  {bar}  {intensity:>4} gCO2eq/kWh{marker}")
    lines.append("-" * (name_width + 32))

    delta = dirtiest[1] - chosen[1]
    if dirtiest[1] > 0:
        pct_vs_worst = round(delta / dirtiest[1] * 100)
        lines.append(
            f"  Avoided up to {delta} gCO2eq/kWh vs the dirtiest candidate "
            f"({dirtiest[0]}), ~{pct_vs_worst}% lower"
        )
    if GLOBAL_AVG_INTENSITY > 0:
        pct_vs_avg = round((GLOBAL_AVG_INTENSITY - chosen[1]) / GLOBAL_AVG_INTENSITY * 100)
        lines.append(
            f"  Routed zone is ~{pct_vs_avg}% below the {GLOBAL_AVG_INTENSITY} "
            f"gCO2eq/kWh global average"
        )
    lines.append("```")
    return lines


def write_job_summary(
    zone,
    intensity,
    is_green,
    max_carbon,
    trend=None,
    forecast_at=None,
    forecast_intensity=None,
    waited_minutes=0,
    skipped=None,
    co2_saved=0,
    comparison=None,
    dry_run=False,
    forecast_heuristic=False,
):
    """Write a GitHub Actions job summary with carbon intensity results.

    comparison: optional list of (zone, intensity) measurements; when two or
    more zones were checked, an ASCII routing comparison panel is appended.
    dry_run: when True, the status reflects report-only mode (the build was
    never gated) and states what the action *would* have done.
    forecast_heuristic: when True, the forecast is a time-of-day estimate (not a
    measured day-ahead forecast), so the row is labeled accordingly.
    """
    # Classify the carbon tier (the adaptive-CI "dial") and emit it as an output
    # before any early return, so downstream jobs can read it even with no summary
    thresholds = parse_tier_thresholds(os.environ.get("TIER_THRESHOLDS", ""))
    tier, tier_reason = classify_tier(intensity, thresholds)
    if tier != "unknown":
        set_output("carbon_tier", tier)
        set_output("carbon_tier_reason", tier_reason)

    # Ensure budget outputs are emitted on every path (including dirty-grid
    # deferrals where no savings were recorded), so gating on budget_exceeded
    # works regardless of the dispatch decision. Idempotent: a no-op once the
    # green/report path has already recorded this run.
    if os.environ.get("MONTHLY_BUDGET_GRAMS", ""):
        record_lifetime_savings(0, 0)

    # Fire the sticky PR comment first so it posts even when there is no job
    # summary file (e.g. local runs); it is a no-op unless opted in and on a PR
    post_pr_comment_once(zone, intensity, is_green, max_carbon, co2_saved, dry_run, tier)

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return

    lines = ["## Carbon-Aware Dispatcher\n"]

    lines.append("| | |")
    lines.append("|---|---|")
    if dry_run and is_green:
        lines.append("| **Status** | Report-only: grid clean, would dispatch |")
    elif dry_run:
        lines.append("| **Status** | Report-only: grid dirty, would defer (build ran anyway) |")
    elif is_green:
        lines.append("| **Status** | Grid is clean, workflow dispatched |")
    else:
        lines.append("| **Status** | Grid is dirty, build skipped until clean |")

    lines.append(f"| **Zone** | `{zone}` |")

    if intensity is not None:
        lines.append(f"| **Carbon Intensity** | {intensity} gCO2eq/kWh |")
    else:
        lines.append("| **Carbon Intensity** | unknown |")

    if tier != "unknown":
        lines.append(f"| **Carbon Tier** | {tier} ({tier_reason}) |")

    lines.append(f"| **Threshold** | {max_carbon} gCO2eq/kWh |")

    if trend:
        lines.append(f"| **Trend** | {trend} |")

    if forecast_at and forecast_at != "none_in_forecast":
        label = "Next Green Window (estimated)" if forecast_heuristic else "Next Green Window"
        lines.append(f"| **{label}** | {forecast_at} |")
        if forecast_intensity is not None:
            note = " (estimate)" if forecast_heuristic else ""
            lines.append(f"| **Forecast Intensity** | {forecast_intensity} gCO2eq/kWh{note} |")
    elif forecast_at == "none_in_forecast":
        lines.append("| **Forecast** | No green window in forecast horizon |")

    if waited_minutes > 0:
        lines.append(f"| **Waited** | {waited_minutes:.0f} minutes |")

    if co2_saved and co2_saved > 0:
        if co2_saved > 1000:
            lines.append(f"| **Est. CO2 Saved** | {co2_saved / 1000:.1f} kg vs global avg |")
        else:
            lines.append(f"| **Est. CO2 Saved** | {co2_saved:.0f} g vs global avg |")
        equiv = carbon_equivalents(co2_saved)
        if equiv["phrase"]:
            lines.append(f"| **That's like** | {equiv['phrase']} |")

    if _lifetime_summary:
        lines.append(f"| **Lifetime CO2 Saved** | {_lifetime_summary['message']} |")

    if _budget_summary:
        b = _budget_summary
        lines.append(
            f"| **Carbon Budget** | {b['mtd']:.0f} / {b['budget']:.0f} gCO2eq this month "
            f"({b['used_pct']:.0f}%, {b['state']}) |"
        )

    if skipped:
        skipped_str = ", ".join(f"`{z}` ({r})" for z, r in skipped)
        lines.append(f"| **Skipped Zones** | {skipped_str} |")

    lines.append("")

    if comparison:
        panel = render_routing_comparison(comparison, zone)
        if panel:
            lines.extend(panel)
            lines.append("")

    with open(summary_file, "a") as f:
        f.write("\n".join(lines))


def handle_dirty_grid(
    zone,
    max_carbon,
    intensity,
    enable_forecast,
    eia_api_key="",
    gridstatus_api_key="",
    emaps_api_key="",
    entsoe_token="",
):
    """When the grid is dirty, fetch forecast/trend info and set outputs.

    Returns (trend, forecast_at, forecast_intensity) for use in job summary.
    """
    provider = detect_provider(zone, entsoe_token)

    set_output("grid_clean", "false")
    if intensity is not None:
        set_output("carbon_intensity", str(intensity))
    else:
        set_output("carbon_intensity", "unknown")

    # Always try history trend
    trend = get_history_trend(zone, provider, eia_api_key, emaps_api_key, entsoe_token)
    if trend:
        set_output("intensity_trend", trend)

    forecast_at = None
    forecast_intensity = None

    # Forecast: free for UK, Electricity Maps, ENTSO-E, Open-Meteo; GridStatus for US
    if enable_forecast or provider in (
        PROVIDER_UK,
        PROVIDER_ELECTRICITY_MAPS,
        PROVIDER_ENTSOE,
        PROVIDER_OPEN_METEO,
        PROVIDER_GRID_INDIA,
        PROVIDER_ONS_BRAZIL,
        PROVIDER_ESKOM,
    ):
        forecast_at, forecast_intensity = get_forecast(
            zone, max_carbon, provider, gridstatus_api_key, emaps_api_key, entsoe_token, eia_api_key
        )
        if forecast_at and forecast_at != "none_in_forecast":
            set_output("forecast_green_at", forecast_at)
            if forecast_intensity is not None:
                set_output("forecast_intensity", str(forecast_intensity))
            print(f"\n  Grid expected to be green at {forecast_at}")
        elif forecast_at == "none_in_forecast":
            set_output("forecast_green_at", "none_in_forecast")
            print("\n  No green window found in forecast horizon.")

    # Suggest optimal cron schedule
    cron, cron_desc = suggest_green_cron(zone)
    if cron:
        set_output("suggested_cron", cron)
        print(f"  Suggested cron schedule: '{cron}' ({cron_desc})")

    return trend, forecast_at, forecast_intensity


def smart_wait_single(
    zone,
    max_carbon,
    max_wait_minutes,
    provider,
    eia_api_key="",
    gridstatus_api_key="",
    emaps_api_key="",
    entsoe_token="",
):
    """Wait up to max_wait_minutes for a single zone to go green.

    Uses forecast data to sleep efficiently when possible.
    Returns (is_green, intensity, waited_minutes).
    """
    print(f"\n  Smart wait: will re-check for up to {max_wait_minutes} minutes.")
    print("  Note: GitHub Actions bills for wait time.")

    start = _time.time()
    deadline = start + max_wait_minutes * 60

    while _time.time() < deadline:
        remaining = deadline - _time.time()
        sleep_seconds = min(DEFAULT_WAIT_INTERVAL, remaining)

        # Try to use forecast for smarter sleep
        forecast_at, _ = get_forecast(
            zone, max_carbon, provider, gridstatus_api_key, emaps_api_key, entsoe_token, eia_api_key
        )

        if forecast_at and forecast_at not in (None, "none_in_forecast"):
            try:
                ft = datetime.fromisoformat(forecast_at.replace("Z", "+00:00"))
                wait_until = (ft - datetime.now(timezone.utc)).total_seconds()
                if wait_until > remaining:
                    print(f"  Forecast green at {forecast_at} but exceeds max wait.")
                    break
                if 0 < wait_until:
                    # Wake 30s before forecast, minimum 60s sleep
                    sleep_seconds = max(min(wait_until - 30, remaining), 60)
                    print(
                        f"  Forecast: green at {forecast_at}. Sleeping {sleep_seconds / 60:.0f}m..."
                    )
            except (ValueError, TypeError) as exc:
                print(f"::warning::Could not parse forecast time '{forecast_at}': {exc}")

        if sleep_seconds <= 0:
            break

        remaining_min = remaining / 60
        print(
            f"  Sleeping {sleep_seconds / 60:.0f}m... "
            f"({remaining_min:.0f}m remaining of {max_wait_minutes}m)"
        )
        _time.sleep(sleep_seconds)

        # Re-check
        is_green, intensity = check_carbon_intensity(
            zone, max_carbon, provider, eia_api_key, emaps_api_key, entsoe_token
        )
        if is_green:
            waited = (_time.time() - start) / 60
            print(f"  Grid is now clean after {waited:.0f}m wait!")
            return True, intensity, waited

    waited = (_time.time() - start) / 60
    # Final check
    is_green, intensity = check_carbon_intensity(
        zone, max_carbon, provider, eia_api_key, emaps_api_key, entsoe_token
    )
    return bool(is_green), intensity, waited


def smart_wait_multi(
    zones_config, max_carbon, max_wait_minutes, eia_api_key="", emaps_api_key="", entsoe_token=""
):
    """Wait up to max_wait_minutes for any zone in the list to go green.

    Returns (best_zone, best_intensity, best_label, waited_minutes, skipped).
    """
    print(f"\n  Smart wait: will re-check all zones for up to {max_wait_minutes} minutes.")
    print("  Note: GitHub Actions bills for wait time.")

    start = _time.time()
    deadline = start + max_wait_minutes * 60

    while _time.time() < deadline:
        remaining = deadline - _time.time()
        sleep_seconds = min(DEFAULT_WAIT_INTERVAL, remaining)

        if sleep_seconds <= 0:
            break

        remaining_min = remaining / 60
        print(
            f"  Sleeping {sleep_seconds / 60:.0f}m... "
            f"({remaining_min:.0f}m remaining of {max_wait_minutes}m)"
        )
        _time.sleep(sleep_seconds)

        best_zone, best_intensity, best_label, skipped = check_multiple_zones(
            zones_config, max_carbon, eia_api_key, emaps_api_key, entsoe_token
        )
        if best_zone is not None:
            waited = (_time.time() - start) / 60
            print(f"  Zone {best_zone} is now clean after {waited:.0f}m wait!")
            return best_zone, best_intensity, best_label, waited, skipped

    waited = (_time.time() - start) / 60
    best_zone, best_intensity, best_label, skipped = check_multiple_zones(
        zones_config, max_carbon, eia_api_key, emaps_api_key, entsoe_token
    )
    return best_zone, best_intensity, best_label, waited, skipped


def load_carbon_policy():
    """Load organization-wide carbon policy from .github/carbon-policy.yml.

    Returns a dict of policy settings, or empty dict if no policy file exists.
    The policy file provides defaults that action inputs can override.
    """
    policy_path = os.environ.get("CARBON_POLICY_PATH", ".github/carbon-policy.yml")

    if not os.path.isfile(policy_path):
        return {}

    print(f"Loading carbon policy from {policy_path}...")
    try:
        with open(policy_path) as f:
            content = f.read()
    except OSError as exc:
        print(f"::warning::Could not read carbon policy: {exc}")
        return {}

    # -----------------------------------------------------------------------
    # WARNING: this is a hand-rolled, intentionally minimal YAML parser.
    # It supports ONLY flat top-level scalar keys of the form `key: value`.
    # It does NOT support:
    #   - nested mappings or indentation
    #   - lists / sequences
    #   - multi-line or block scalars
    #   - values containing ':' (e.g. cron times like '0 18') will be
    #     mis-split on the first colon
    # This is deliberate to keep the project zero-runtime-dependency (no
    # PyYAML). Do not rely on it for complex YAML; add a real parser only if
    # the zero-dependency goal is dropped.
    # -----------------------------------------------------------------------
    policy = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("'\"")
            if value:
                policy[key] = value

    if policy:
        print(f"  Policy loaded: {', '.join(f'{k}={v}' for k, v in policy.items())}")
    return policy


def queue_find_optimal_window(
    zones_config,
    max_carbon,
    deadline_hours,
    eia_api_key="",
    gridstatus_api_key="",
    emaps_api_key="",
    entsoe_token="",
):
    """Find the optimal green window across all zones within a deadline.

    Instead of waiting in real-time, this checks forecasts for all zones
    and returns the earliest predicted green window.

    Returns (optimal_zone, optimal_time, optimal_intensity) or (None, None, None).
    """
    best_zone = None
    best_time = None
    best_intensity = None

    for entry in zones_config:
        zone = entry["zone"]
        provider = detect_provider(zone, entsoe_token)

        # Fall back to Open-Meteo if needed
        if provider == PROVIDER_ELECTRICITY_MAPS and not emaps_api_key:
            from providers.open_meteo import ZONE_COORDINATES

            if zone in ZONE_COORDINATES:
                provider = PROVIDER_OPEN_METEO
            else:
                continue

        forecast_at, forecast_intensity = get_forecast(
            zone, max_carbon, provider, gridstatus_api_key, emaps_api_key, entsoe_token, eia_api_key
        )

        if forecast_at and forecast_at != "none_in_forecast" and forecast_intensity is not None:
            # Parse forecast time and check if within deadline
            try:
                ft = datetime.fromisoformat(forecast_at.replace("Z", "+00:00"))
                hours_away = (ft - datetime.now(timezone.utc)).total_seconds() / 3600
                if hours_away > deadline_hours:
                    continue
            except (ValueError, TypeError) as exc:
                print(f"::warning::Could not parse forecast time '{forecast_at}': {exc}")

            if best_intensity is None or forecast_intensity < best_intensity:
                best_zone = zone
                best_time = forecast_at
                best_intensity = forecast_intensity

    return best_zone, best_time, best_intensity


def main():
    # Determine mode: dispatch (workflow_id set) or inline (just set outputs)
    workflow_id = os.environ.get("WORKFLOW_ID", "")
    dispatch_mode = bool(workflow_id)

    if dispatch_mode:
        token = get_required_env("GITHUB_TOKEN")
        repo = get_required_env("TARGET_REPO")
    else:
        token = os.environ.get("GITHUB_TOKEN", "")
        repo = os.environ.get("TARGET_REPO", "")
        print("Inline mode: no workflow_id set. Will check grid and set outputs only.")

    # Load org-wide carbon policy (defaults that action inputs can override)
    policy = load_carbon_policy()

    # Optional inputs with defaults (action inputs override policy)
    eia_api_key = os.environ.get("EIA_API_KEY", "")
    gridstatus_api_key = os.environ.get("GRID_STATUS_API_KEY", "")
    emaps_api_key = os.environ.get("ELECTRICITY_MAPS_TOKEN", "")
    entsoe_token = os.environ.get("ENTSOE_TOKEN", "")
    ref = os.environ.get("TARGET_REF", DEFAULT_REF) or DEFAULT_REF
    # Resolve raw string first (env overrides policy), then convert with a
    # numeric default so malformed input fails loudly instead of crashing
    max_carbon_raw = os.environ.get("MAX_CARBON", policy.get("max_carbon_intensity", ""))
    max_carbon = _env_float("MAX_CARBON", DEFAULT_MAX_CARBON, max_carbon_raw)
    fail_on_api_error = (
        os.environ.get("FAIL_ON_API_ERROR", policy.get("fail_on_api_error", "false")).lower()
        == "true"
    )
    enable_forecast = (
        os.environ.get("ENABLE_FORECAST", policy.get("enable_forecast", "false")).lower() == "true"
    )
    max_wait_raw = os.environ.get("MAX_WAIT", policy.get("max_wait", ""))
    max_wait = min(_env_int("MAX_WAIT", 0, max_wait_raw), MAX_WAIT_CAP)
    runner_provider = os.environ.get("RUNNER_PROVIDER", policy.get("runner_provider", ""))
    runner_spec = os.environ.get("RUNNER_SPEC", policy.get("runner_spec", ""))
    github_run_id = os.environ.get("GITHUB_RUN_ID", "")
    strategy = os.environ.get("STRATEGY", policy.get("strategy", "check")).lower()
    deadline_hours_raw = os.environ.get("DEADLINE_HOURS", policy.get("deadline_hours", ""))
    deadline_hours = _env_float("DEADLINE_HOURS", 24, deadline_hours_raw)
    dry_run = os.environ.get("DRY_RUN", policy.get("dry_run", "false")).lower() == "true"
    consumption_based = (
        os.environ.get("CONSUMPTION_BASED", policy.get("consumption_based", "false")).lower()
        == "true"
    )

    # Parse zone(s): action inputs override policy
    grid_zones_str = os.environ.get("GRID_ZONES", "")
    grid_zone_str = os.environ.get("GRID_ZONE", "")
    policy_zones = policy.get("grid_zones", "") or policy.get("grid_zone", "")

    if grid_zones_str:
        zones_config = parse_zones_input(grid_zones_str)
    elif grid_zone_str:
        zones_config = parse_zones_input(grid_zone_str)
    elif policy_zones:
        zones_config = parse_zones_input(policy_zones)
        print(f"Using zones from carbon policy: {policy_zones}")
    else:
        # Zero-config: try auto-detect from cloud env, fall back to auto:cleanest
        print("No zone specified, using auto:detect (zero-config mode).")
        zones_config = parse_zones_input("auto:detect")

    if not zones_config:
        print("::error::No valid zones provided.")
        sys.exit(EXIT_FAILURE)

    # Show auto preset expansion
    raw_input = (grid_zones_str or grid_zone_str).strip().lower()
    if raw_input.startswith("auto:"):
        zone_names = [z["zone"] for z in zones_config]
        print(f"{raw_input} expanded to {len(zones_config)} zones: {', '.join(zone_names)}")

    # Emit upfront warnings about missing tokens
    _emit_token_warnings(zones_config, emaps_api_key, entsoe_token)

    print(f"Carbon intensity threshold: {max_carbon} gCO2eq/kWh")
    if dry_run:
        print("Dry run: report-only mode, the build is never gated.")
    if strategy == "queue":
        print(f"Strategy: queue (find optimal green window within {deadline_hours}h)")
    if max_wait > 0:
        print(f"Smart wait: up to {max_wait} minutes")
    print(f"Checking {len(zones_config)} zone(s)...\n")

    # Dry run short-circuits every strategy: measure and report, never gate
    if dry_run:
        run_dry_run(
            zones_config,
            max_carbon,
            enable_forecast,
            eia_api_key,
            gridstatus_api_key,
            emaps_api_key,
            entsoe_token,
            runner_provider,
            runner_spec,
            github_run_id,
        )
        sys.exit(EXIT_SUCCESS)

    # Queue strategy: find the optimal green window across all zones
    # instead of just checking now. Outputs the best time to dispatch.
    if strategy == "queue":
        # First check if any zone is already green
        best_zone, best_intensity, best_label, skipped = check_multiple_zones(
            zones_config, max_carbon, eia_api_key, emaps_api_key, entsoe_token
        )

        if best_zone is not None:
            # Already green, so dispatch immediately
            set_output("grid_zone", best_zone)
            set_output("optimal_dispatch_at", "now")
            _emit_green_result(
                best_zone,
                best_intensity,
                best_label,
                max_carbon,
                dispatch_mode,
                repo,
                workflow_id,
                token,
                ref,
                runner_provider,
                runner_spec,
                github_run_id,
                skipped=skipped,
            )
            sys.exit(EXIT_SUCCESS)

        # No green zone now, so find the optimal future window via forecasts
        print("\nNo green zone right now. Searching forecasts for optimal window...")
        opt_zone, opt_time, opt_intensity = queue_find_optimal_window(
            zones_config,
            max_carbon,
            deadline_hours,
            eia_api_key,
            gridstatus_api_key,
            emaps_api_key,
            entsoe_token,
        )

        if opt_zone and opt_time:
            set_output("grid_clean", "false")
            set_output("optimal_dispatch_at", opt_time)
            set_output("optimal_zone", opt_zone)
            if opt_intensity is not None:
                set_output("forecast_intensity", str(opt_intensity))
            set_runner_outputs(opt_zone, None, runner_provider, runner_spec, github_run_id)
            print(
                f"\nQueue: optimal window at {opt_time} in zone {opt_zone} "
                f"(~{opt_intensity} gCO2eq/kWh)"
            )
            print("  Schedule your workflow to run at that time for green energy.")

            # If max_wait is set and the window is within range, actually wait
            if max_wait > 0:
                try:
                    ft = datetime.fromisoformat(opt_time.replace("Z", "+00:00"))
                    wait_minutes = (ft - datetime.now(timezone.utc)).total_seconds() / 60
                    if 0 < wait_minutes <= max_wait:
                        print(f"  Waiting {wait_minutes:.0f}m for optimal window...")
                        _time.sleep(wait_minutes * 60)
                        # Re-check
                        provider = detect_provider(opt_zone, entsoe_token)
                        is_green, intensity = check_carbon_intensity(
                            opt_zone, max_carbon, provider, eia_api_key, emaps_api_key, entsoe_token
                        )
                        if is_green:
                            set_output("grid_clean", "true")
                            set_output("carbon_intensity", str(intensity))
                            co2_saved, badge_url = estimate_carbon_savings(intensity)
                            set_savings_outputs(co2_saved, badge_url, intensity)
                            if dispatch_mode:
                                print("\nGrid is green after queue wait! Dispatching...")
                                trigger_workflow(repo, workflow_id, token, ref)
                except (ValueError, TypeError) as exc:
                    print(f"::warning::Could not parse forecast time '{opt_time}': {exc}")
        else:
            set_output("grid_clean", "false")
            set_output("optimal_dispatch_at", "none_in_deadline")
            print(f"\nQueue: no green window found within {deadline_hours}h deadline.")
            if fail_on_api_error:
                print(
                    "::error::No green window in queue deadline and fail_on_api_error is enabled."
                )
                write_job_summary(zones_config[0]["zone"], None, False, max_carbon, skipped=skipped)
                sys.exit(EXIT_FAILURE)

        write_job_summary(
            (opt_zone or zones_config[0]["zone"]),
            opt_intensity,
            False,
            max_carbon,
            forecast_at=opt_time,
            forecast_intensity=opt_intensity,
            skipped=skipped,
        )
        sys.exit(EXIT_SUCCESS)

    # Single zone mode
    if len(zones_config) == 1:
        entry = zones_config[0]
        provider = detect_provider(entry["zone"], entsoe_token)
        is_green, intensity = check_carbon_intensity(
            entry["zone"], max_carbon, provider, eia_api_key, emaps_api_key, entsoe_token
        )

        # Consumption-based override (opt-in, EU zones with an ENTSO-E token):
        # replace production intensity with flow-traced consumption intensity,
        # which accounts for imports/exports. Falls through silently if the zone
        # isn't traceable or the computation fails, so behavior is never worse.
        if consumption_based and intensity is not None:
            is_green, intensity = _apply_consumption_intensity(
                entry["zone"], max_carbon, is_green, intensity, entsoe_token
            )

        if is_green is None:
            set_output("grid_clean", "false")
            set_output("carbon_intensity", "unknown")
            write_job_summary(entry["zone"], None, False, max_carbon)
            if fail_on_api_error:
                print("::error::API error and fail_on_api_error is enabled.")
                sys.exit(EXIT_FAILURE)
            print("\nAPI error occurred. Skipping dispatch to avoid dirty compute.")
            sys.exit(EXIT_SUCCESS)

        set_output("grid_zone", entry["zone"])

        waited_minutes = 0

        # Smart wait if dirty and max_wait configured
        if not is_green and max_wait > 0:
            is_green, intensity, waited_minutes = smart_wait_single(
                entry["zone"],
                max_carbon,
                max_wait,
                provider,
                eia_api_key,
                gridstatus_api_key,
                emaps_api_key,
                entsoe_token,
            )

        if is_green:
            _emit_green_result(
                entry["zone"],
                intensity,
                entry.get("runner_label"),
                max_carbon,
                dispatch_mode,
                repo,
                workflow_id,
                token,
                ref,
                runner_provider,
                runner_spec,
                github_run_id,
                waited_minutes=waited_minutes,
            )
        else:
            trend, forecast_at, forecast_intensity = handle_dirty_grid(
                entry["zone"],
                max_carbon,
                intensity,
                enable_forecast,
                eia_api_key,
                gridstatus_api_key,
                emaps_api_key,
                entsoe_token,
            )
            write_job_summary(
                entry["zone"],
                intensity,
                False,
                max_carbon,
                trend=trend,
                forecast_at=forecast_at,
                forecast_intensity=forecast_intensity,
                waited_minutes=waited_minutes,
                forecast_heuristic=provider in HEURISTIC_FORECAST_PROVIDERS,
            )
            wait_msg = f" (waited {waited_minutes:.0f}m)" if waited_minutes > 0 else ""
            print(
                f"\nGrid is dirty ({intensity} gCO2eq/kWh > {max_carbon}){wait_msg}. "
                "Will retry on next schedule."
            )
            sys.exit(EXIT_SUCCESS)

    # Multi-zone mode: pick the greenest zone
    else:
        measured = []
        best_zone, best_intensity, best_label, skipped = check_multiple_zones(
            zones_config, max_carbon, eia_api_key, emaps_api_key, entsoe_token, collect=measured
        )

        waited_minutes = 0

        # Smart wait if no green zone and max_wait configured
        if best_zone is None and max_wait > 0:
            best_zone, best_intensity, best_label, waited_minutes, skipped = smart_wait_multi(
                zones_config, max_carbon, max_wait, eia_api_key, emaps_api_key, entsoe_token
            )

        if best_zone is None:
            first_zone = zones_config[0]["zone"]
            trend, forecast_at, forecast_intensity = handle_dirty_grid(
                first_zone,
                max_carbon,
                None,
                enable_forecast,
                eia_api_key,
                gridstatus_api_key,
                emaps_api_key,
                entsoe_token,
            )
            write_job_summary(
                first_zone,
                None,
                False,
                max_carbon,
                trend=trend,
                forecast_at=forecast_at,
                forecast_intensity=forecast_intensity,
                waited_minutes=waited_minutes,
                skipped=skipped,
                forecast_heuristic=detect_provider(first_zone, entsoe_token)
                in HEURISTIC_FORECAST_PROVIDERS,
            )
            if fail_on_api_error:
                print("::error::No green zones found and fail_on_api_error is enabled.")
                sys.exit(EXIT_FAILURE)
            print("\nNo green zones available. Will retry on next schedule.")
            sys.exit(EXIT_SUCCESS)

        set_output("grid_zone", best_zone)
        _emit_green_result(
            best_zone,
            best_intensity,
            best_label,
            max_carbon,
            dispatch_mode,
            repo,
            workflow_id,
            token,
            ref,
            runner_provider,
            runner_spec,
            github_run_id,
            waited_minutes=waited_minutes,
            skipped=skipped,
            comparison=measured,
        )


if __name__ == "__main__":
    main()
