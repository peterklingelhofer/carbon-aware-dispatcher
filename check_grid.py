"""Carbon-Aware Dispatcher - checks grid carbon intensity and dispatches workflows."""

import os
import sys
import time as _time
from datetime import datetime, timezone

import requests

from providers import (
    AUTO_GREEN_ZONES,
    PROVIDER_EIA,
    PROVIDER_ELECTRICITY_MAPS,
    PROVIDER_UK,
    detect_provider,
)
from providers import eia, electricity_maps, gridstatus, uk
from providers.base import DEFAULT_TIMEOUT

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


def check_carbon_intensity(zone, max_carbon, provider, eia_api_key="",
                           emaps_api_key=""):
    """Check carbon intensity using the appropriate provider."""
    if provider == PROVIDER_UK:
        return uk.check_carbon_intensity(zone, max_carbon)
    if provider == PROVIDER_ELECTRICITY_MAPS:
        return electricity_maps.check_carbon_intensity(zone, max_carbon, emaps_api_key)
    return eia.check_carbon_intensity(zone, max_carbon, eia_api_key)


def get_forecast(zone, max_carbon, provider, gridstatus_api_key="",
                 emaps_api_key=""):
    """Get forecast using the appropriate provider."""
    if provider == PROVIDER_UK:
        return uk.get_forecast(zone, max_carbon)
    if provider == PROVIDER_ELECTRICITY_MAPS:
        return electricity_maps.get_forecast(zone, max_carbon, emaps_api_key)
    # US zones: use GridStatus.io if API key is available
    if gridstatus_api_key:
        return gridstatus.get_forecast(zone, max_carbon, gridstatus_api_key)
    print("  No forecast available for US zones without a GridStatus API key.")
    return None, None


def get_history_trend(zone, provider, eia_api_key="", emaps_api_key=""):
    """Get history trend using the appropriate provider."""
    if provider == PROVIDER_UK:
        return uk.get_history_trend(zone)
    if provider == PROVIDER_ELECTRICITY_MAPS:
        return electricity_maps.get_history_trend(zone, emaps_api_key)
    return eia.get_history_trend(zone, eia_api_key)


def check_multiple_zones(zones_config, max_carbon, eia_api_key="",
                         emaps_api_key=""):
    """Check carbon intensity for multiple zones, return the best green option.

    Returns (best_zone, best_intensity, best_runner_label, skipped) where
    skipped is a list of (zone, reason) for zones that could not be checked.
    """
    best_zone = None
    best_intensity = None
    best_label = None
    skipped = []

    for entry in zones_config:
        zone = entry["zone"]
        label = entry.get("runner_label")
        provider = detect_provider(zone)

        # Warn about missing API keys before checking
        if provider == PROVIDER_ELECTRICITY_MAPS and not emaps_api_key:
            reason = "no electricity_maps_token"
            print(f"::warning::Skipping zone {zone}: {reason}")
            skipped.append((zone, reason))
            continue

        is_green, intensity = check_carbon_intensity(
            zone, max_carbon, provider, eia_api_key, emaps_api_key
        )
        if is_green is None:
            skipped.append((zone, "API error"))
        elif is_green and intensity is not None:
            if best_intensity is None or intensity < best_intensity:
                best_zone = zone
                best_intensity = intensity
                best_label = label

    return best_zone, best_intensity, best_label, skipped


def expand_auto_zones(zones_str):
    """Expand 'auto:green' preset into curated green zone list.

    Returns the expanded zones_config list, or None if not an auto preset.
    """
    normalized = zones_str.strip().lower()
    if normalized == "auto:green":
        return list(AUTO_GREEN_ZONES)
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


def write_job_summary(zone, intensity, is_green, max_carbon, trend=None,
                      forecast_at=None, forecast_intensity=None,
                      waited_minutes=0, skipped=None):
    """Write a GitHub Actions job summary with carbon intensity results."""
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return

    lines = ["## Carbon-Aware Dispatcher\n"]

    if is_green:
        lines.append("| | |")
        lines.append("|---|---|")
        lines.append(f"| **Status** | Grid is clean — workflow dispatched |")
    else:
        lines.append("| | |")
        lines.append("|---|---|")
        lines.append(f"| **Status** | Grid is dirty — waiting for clean energy |")

    lines.append(f"| **Zone** | `{zone}` |")

    if intensity is not None:
        lines.append(f"| **Carbon Intensity** | {intensity} gCO2eq/kWh |")
    else:
        lines.append("| **Carbon Intensity** | unknown |")

    lines.append(f"| **Threshold** | {max_carbon} gCO2eq/kWh |")

    if trend:
        lines.append(f"| **Trend** | {trend} |")

    if forecast_at and forecast_at != "none_in_forecast":
        lines.append(f"| **Next Green Window** | {forecast_at} |")
        if forecast_intensity is not None:
            lines.append(f"| **Forecast Intensity** | {forecast_intensity} gCO2eq/kWh |")
    elif forecast_at == "none_in_forecast":
        lines.append("| **Forecast** | No green window in forecast horizon |")

    if waited_minutes > 0:
        lines.append(f"| **Waited** | {waited_minutes:.0f} minutes |")

    if skipped:
        skipped_str = ", ".join(f"`{z}` ({r})" for z, r in skipped)
        lines.append(f"| **Skipped Zones** | {skipped_str} |")

    lines.append("")

    with open(summary_file, "a") as f:
        f.write("\n".join(lines))


def handle_dirty_grid(zone, max_carbon, intensity, enable_forecast,
                      eia_api_key="", gridstatus_api_key="", emaps_api_key=""):
    """When the grid is dirty, fetch forecast/trend info and set outputs.

    Returns (trend, forecast_at, forecast_intensity) for use in job summary.
    """
    provider = detect_provider(zone)

    set_output("grid_clean", "false")
    if intensity is not None:
        set_output("carbon_intensity", str(intensity))
    else:
        set_output("carbon_intensity", "unknown")

    # Always try history trend
    trend = get_history_trend(zone, provider, eia_api_key, emaps_api_key)
    if trend:
        set_output("intensity_trend", trend)

    forecast_at = None
    forecast_intensity = None

    # Forecast — free for UK and Electricity Maps, GridStatus for US (requires key)
    if enable_forecast or provider in (PROVIDER_UK, PROVIDER_ELECTRICITY_MAPS):
        forecast_at, forecast_intensity = get_forecast(
            zone, max_carbon, provider, gridstatus_api_key, emaps_api_key
        )
        if forecast_at and forecast_at != "none_in_forecast":
            set_output("forecast_green_at", forecast_at)
            if forecast_intensity is not None:
                set_output("forecast_intensity", str(forecast_intensity))
            print(f"\n  Grid expected to be green at {forecast_at}")
        elif forecast_at == "none_in_forecast":
            set_output("forecast_green_at", "none_in_forecast")
            print("\n  No green window found in forecast horizon.")

    return trend, forecast_at, forecast_intensity


def smart_wait_single(zone, max_carbon, max_wait_minutes, provider,
                      eia_api_key="", gridstatus_api_key="", emaps_api_key=""):
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
            zone, max_carbon, provider, gridstatus_api_key, emaps_api_key
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
                    print(f"  Forecast: green at {forecast_at}. "
                          f"Sleeping {sleep_seconds / 60:.0f}m...")
            except (ValueError, TypeError):
                pass

        if sleep_seconds <= 0:
            break

        remaining_min = remaining / 60
        print(f"  Sleeping {sleep_seconds / 60:.0f}m... "
              f"({remaining_min:.0f}m remaining of {max_wait_minutes}m)")
        _time.sleep(sleep_seconds)

        # Re-check
        is_green, intensity = check_carbon_intensity(
            zone, max_carbon, provider, eia_api_key, emaps_api_key
        )
        if is_green:
            waited = (_time.time() - start) / 60
            print(f"  Grid is now clean after {waited:.0f}m wait!")
            return True, intensity, waited

    waited = (_time.time() - start) / 60
    # Final check
    is_green, intensity = check_carbon_intensity(
        zone, max_carbon, provider, eia_api_key, emaps_api_key
    )
    return bool(is_green), intensity, waited


def smart_wait_multi(zones_config, max_carbon, max_wait_minutes,
                     eia_api_key="", emaps_api_key=""):
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
        print(f"  Sleeping {sleep_seconds / 60:.0f}m... "
              f"({remaining_min:.0f}m remaining of {max_wait_minutes}m)")
        _time.sleep(sleep_seconds)

        best_zone, best_intensity, best_label, skipped = check_multiple_zones(
            zones_config, max_carbon, eia_api_key, emaps_api_key
        )
        if best_zone is not None:
            waited = (_time.time() - start) / 60
            print(f"  Zone {best_zone} is now clean after {waited:.0f}m wait!")
            return best_zone, best_intensity, best_label, waited, skipped

    waited = (_time.time() - start) / 60
    best_zone, best_intensity, best_label, skipped = check_multiple_zones(
        zones_config, max_carbon, eia_api_key, emaps_api_key
    )
    return best_zone, best_intensity, best_label, waited, skipped


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

    # Optional inputs with defaults
    eia_api_key = os.environ.get("EIA_API_KEY", "")
    gridstatus_api_key = os.environ.get("GRID_STATUS_API_KEY", "")
    emaps_api_key = os.environ.get("ELECTRICITY_MAPS_TOKEN", "")
    ref = os.environ.get("TARGET_REF", DEFAULT_REF) or DEFAULT_REF
    max_carbon = float(os.environ.get("MAX_CARBON", DEFAULT_MAX_CARBON))
    fail_on_api_error = os.environ.get("FAIL_ON_API_ERROR", "false").lower() == "true"
    enable_forecast = os.environ.get("ENABLE_FORECAST", "false").lower() == "true"
    max_wait = min(int(os.environ.get("MAX_WAIT", "0")), MAX_WAIT_CAP)

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

    # Show auto:green expansion
    if grid_zones_str.strip().lower() == "auto:green" or grid_zone_str.strip().lower() == "auto:green":
        zone_names = [z["zone"] for z in zones_config]
        print(f"auto:green expanded to {len(zones_config)} zones: {', '.join(zone_names)}")

    print(f"Carbon intensity threshold: {max_carbon} gCO2eq/kWh")
    if max_wait > 0:
        print(f"Smart wait: up to {max_wait} minutes")
    print(f"Checking {len(zones_config)} zone(s)...\n")

    # Single zone mode
    if len(zones_config) == 1:
        entry = zones_config[0]
        provider = detect_provider(entry["zone"])
        is_green, intensity = check_carbon_intensity(
            entry["zone"], max_carbon, provider, eia_api_key, emaps_api_key
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
                entry["zone"], max_carbon, max_wait, provider,
                eia_api_key, gridstatus_api_key, emaps_api_key
            )

        if is_green:
            set_output("grid_clean", "true")
            set_output("carbon_intensity", str(intensity))
            if entry.get("runner_label"):
                set_output("runner_label", entry["runner_label"])
            write_job_summary(entry["zone"], intensity, True, max_carbon,
                              waited_minutes=waited_minutes)
            if dispatch_mode:
                print(f"\nGrid is clean! Triggering workflow...")
                trigger_workflow(repo, workflow_id, token, ref)
            else:
                print(f"\nGrid is clean! ({intensity} gCO2eq/kWh)")
        else:
            trend, forecast_at, forecast_intensity = handle_dirty_grid(
                entry["zone"], max_carbon, intensity, enable_forecast,
                eia_api_key, gridstatus_api_key, emaps_api_key
            )
            write_job_summary(entry["zone"], intensity, False, max_carbon,
                              trend=trend, forecast_at=forecast_at,
                              forecast_intensity=forecast_intensity,
                              waited_minutes=waited_minutes)
            wait_msg = f" (waited {waited_minutes:.0f}m)" if waited_minutes > 0 else ""
            print(f"\nGrid is dirty ({intensity} gCO2eq/kWh > {max_carbon}){wait_msg}. "
                  "Will retry on next schedule.")
            sys.exit(EXIT_SUCCESS)

    # Multi-zone mode: pick the greenest zone
    else:
        best_zone, best_intensity, best_label, skipped = check_multiple_zones(
            zones_config, max_carbon, eia_api_key, emaps_api_key
        )

        waited_minutes = 0

        # Smart wait if no green zone and max_wait configured
        if best_zone is None and max_wait > 0:
            best_zone, best_intensity, best_label, waited_minutes, skipped = (
                smart_wait_multi(
                    zones_config, max_carbon, max_wait,
                    eia_api_key, emaps_api_key
                )
            )

        if best_zone is None:
            first_zone = zones_config[0]["zone"]
            trend, forecast_at, forecast_intensity = handle_dirty_grid(
                first_zone, max_carbon, None, enable_forecast,
                eia_api_key, gridstatus_api_key, emaps_api_key
            )
            write_job_summary(first_zone, None, False, max_carbon,
                              trend=trend, forecast_at=forecast_at,
                              forecast_intensity=forecast_intensity,
                              waited_minutes=waited_minutes, skipped=skipped)
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

        write_job_summary(best_zone, best_intensity, True, max_carbon,
                          waited_minutes=waited_minutes, skipped=skipped)

        if dispatch_mode:
            print(f"\nBest zone: {best_zone} ({best_intensity} gCO2eq/kWh)")
            print(f"Triggering workflow...")
            trigger_workflow(repo, workflow_id, token, ref)
        else:
            print(f"\nBest zone: {best_zone} ({best_intensity} gCO2eq/kWh)")


if __name__ == "__main__":
    main()
