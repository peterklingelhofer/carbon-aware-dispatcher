"""Carbon-Aware Dispatcher - checks grid carbon intensity and dispatches workflows."""

import os
import sys

import requests

from providers import (
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

    Returns (best_zone, best_intensity, best_runner_label) or (None, None, None).
    """
    best_zone = None
    best_intensity = None
    best_label = None

    for entry in zones_config:
        zone = entry["zone"]
        label = entry.get("runner_label")
        provider = detect_provider(zone)

        is_green, intensity = check_carbon_intensity(
            zone, max_carbon, provider, eia_api_key, emaps_api_key
        )
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


def handle_dirty_grid(zone, max_carbon, intensity, enable_forecast,
                      eia_api_key="", gridstatus_api_key="", emaps_api_key=""):
    """When the grid is dirty, fetch forecast/trend info and set outputs."""
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


def main():
    # Required inputs
    workflow_id = get_required_env("WORKFLOW_ID")
    token = get_required_env("GITHUB_TOKEN")
    repo = get_required_env("TARGET_REPO")

    # Optional inputs with defaults
    eia_api_key = os.environ.get("EIA_API_KEY", "")
    gridstatus_api_key = os.environ.get("GRID_STATUS_API_KEY", "")
    emaps_api_key = os.environ.get("ELECTRICITY_MAPS_TOKEN", "")
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
        is_green, intensity = check_carbon_intensity(
            entry["zone"], max_carbon, provider, eia_api_key, emaps_api_key
        )

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
            handle_dirty_grid(
                entry["zone"], max_carbon, intensity, enable_forecast,
                eia_api_key, gridstatus_api_key, emaps_api_key
            )
            print(f"\nGrid is dirty ({intensity} gCO2eq/kWh > {max_carbon}). Will retry on next schedule.")
            sys.exit(EXIT_SUCCESS)

    # Multi-zone mode: pick the greenest zone
    else:
        best_zone, best_intensity, best_label = check_multiple_zones(
            zones_config, max_carbon, eia_api_key, emaps_api_key
        )

        if best_zone is None:
            first_zone = zones_config[0]["zone"]
            handle_dirty_grid(
                first_zone, max_carbon, None, enable_forecast,
                eia_api_key, gridstatus_api_key, emaps_api_key
            )
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
