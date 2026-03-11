#!/usr/bin/env python3
"""Carbon-Aware Dispatcher — Setup Wizard.

Validates API keys, tests zone connectivity, and prints a configuration summary.
Run locally or in CI to verify your setup before using the action.

Usage:
    python setup_wizard.py                          # Interactive — prompts for zones/keys
    python setup_wizard.py --zone CISO              # Test a single zone
    python setup_wizard.py --zones "CISO,GB,DE"     # Test multiple zones
    python setup_wizard.py --auto-green             # Test the auto:green preset

Environment variables (alternative to flags):
    EIA_API_KEY, ELECTRICITY_MAPS_TOKEN, GRID_STATUS_API_KEY
"""

import argparse
import os
import sys

# Add the repo root to the path so we can import providers
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from providers import (
    AEMO_ZONE_IDS,
    AUTO_GREEN_ZONES,
    EIA_BALANCING_AUTHORITIES,
    PROVIDER_AEMO,
    PROVIDER_EIA,
    PROVIDER_ELECTRICITY_MAPS,
    PROVIDER_UK,
    UK_REGION_IDS,
    detect_provider,
)
from providers import aemo, eia, electricity_maps, uk


def test_zone(zone, eia_api_key="", emaps_api_key=""):
    """Test connectivity and data retrieval for a single zone.

    Returns a dict with test results.
    """
    provider = detect_provider(zone)
    provider_name = {
        PROVIDER_UK: "UK Carbon Intensity API (free, no key)",
        PROVIDER_EIA: "EIA API (US)",
        PROVIDER_AEMO: "AEMO NEM API (free, no key)",
        PROVIDER_ELECTRICITY_MAPS: "Electricity Maps (requires token)",
    }.get(provider, provider)

    result = {
        "zone": zone,
        "provider": provider_name,
        "status": "unknown",
        "intensity": None,
        "error": None,
    }

    # Check if API key is available for zones that need it
    if provider == PROVIDER_ELECTRICITY_MAPS and not emaps_api_key:
        result["status"] = "skipped"
        result["error"] = "No electricity_maps_token. Get free token at https://portal.electricitymaps.com/"
        return result

    # Test the API
    try:
        if provider == PROVIDER_UK:
            is_green, intensity = uk.check_carbon_intensity(zone, 9999)
        elif provider == PROVIDER_AEMO:
            is_green, intensity = aemo.check_carbon_intensity(zone, 9999)
        elif provider == PROVIDER_ELECTRICITY_MAPS:
            is_green, intensity = electricity_maps.check_carbon_intensity(zone, 9999, emaps_api_key)
        else:
            is_green, intensity = eia.check_carbon_intensity(zone, 9999, eia_api_key)

        if intensity is not None:
            result["status"] = "ok"
            result["intensity"] = intensity
        else:
            result["status"] = "error"
            result["error"] = "API returned no data — zone code may be invalid"
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)

    return result


def print_results(results, eia_api_key="", emaps_api_key="", gridstatus_api_key=""):
    """Print a formatted summary of test results."""
    print("\n" + "=" * 60)
    print("  Carbon-Aware Dispatcher — Setup Wizard")
    print("=" * 60)

    # API key status
    print("\n  API Keys:")
    if eia_api_key and eia_api_key != "DEMO_KEY":
        print("    EIA API Key:           custom key configured")
    else:
        print("    EIA API Key:           using DEMO_KEY (rate limited)")
        print("                           Register free: https://www.eia.gov/opendata/register.php")

    if emaps_api_key:
        print("    Electricity Maps:      token configured")
    else:
        print("    Electricity Maps:      not configured (global zones unavailable)")
        print("                           Register free: https://portal.electricitymaps.com/")

    if gridstatus_api_key:
        print("    GridStatus.io:         key configured (US forecasts enabled)")
    else:
        print("    GridStatus.io:         not configured (US forecasts unavailable)")
        print("                           Register free: https://www.gridstatus.io")

    print("    UK Carbon Intensity:   no key needed")
    print("    AEMO (Australia):      no key needed")

    # Zone results
    print(f"\n  Zone Tests ({len(results)} zones):")
    print("  " + "-" * 56)

    ok_count = 0
    skip_count = 0
    err_count = 0

    for r in results:
        zone = r["zone"]
        if r["status"] == "ok":
            ok_count += 1
            intensity = r["intensity"]
            print(f"    {zone:<12} {r['provider']:<35} {intensity} gCO2eq/kWh")
        elif r["status"] == "skipped":
            skip_count += 1
            print(f"    {zone:<12} SKIPPED — {r['error']}")
        else:
            err_count += 1
            print(f"    {zone:<12} ERROR — {r['error']}")

    print("  " + "-" * 56)
    print(f"    {ok_count} ok, {skip_count} skipped, {err_count} errors")

    # Recommendations
    print("\n  Recommendations:")
    if err_count == 0 and skip_count == 0:
        print("    All zones working! Your configuration is ready to use.")
    else:
        if skip_count > 0 and not emaps_api_key:
            print("    - Add electricity_maps_token to enable global zones")
        if err_count > 0:
            print("    - Check zone codes match your provider (see README)")
        if not eia_api_key or eia_api_key == "DEMO_KEY":
            has_eia = any(detect_provider(r["zone"]) == PROVIDER_EIA for r in results)
            if has_eia:
                print("    - Register a free EIA API key for higher rate limits")

    # Example workflow snippet
    zones_str = ",".join(r["zone"] for r in results if r["status"] == "ok")
    if zones_str:
        print(f"\n  Example workflow config:")
        print(f"    grid_zones: '{zones_str}'")
        greenest = min((r for r in results if r["status"] == "ok"),
                       key=lambda r: r["intensity"])
        print(f"    Greenest zone right now: {greenest['zone']} ({greenest['intensity']} gCO2eq/kWh)")

    print("\n" + "=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Carbon-Aware Dispatcher — Setup Wizard"
    )
    parser.add_argument("--zone", help="Test a single zone")
    parser.add_argument("--zones", help="Test comma-separated zones")
    parser.add_argument("--auto-green", action="store_true",
                        help="Test the auto:green preset zones")
    parser.add_argument("--eia-api-key", default=os.environ.get("EIA_API_KEY", ""))
    parser.add_argument("--electricity-maps-token",
                        default=os.environ.get("ELECTRICITY_MAPS_TOKEN", ""))
    parser.add_argument("--gridstatus-api-key",
                        default=os.environ.get("GRID_STATUS_API_KEY", ""))

    args = parser.parse_args()

    # Determine zones to test
    zones = []
    if args.auto_green:
        zones = [z["zone"] for z in AUTO_GREEN_ZONES]
    elif args.zones:
        zones = [z.strip() for z in args.zones.split(",") if z.strip()]
    elif args.zone:
        zones = [args.zone]
    else:
        # Interactive: suggest common zones
        print("No zones specified. Testing common zones from each provider...\n")
        zones = ["CISO", "GB", "AU-NSW"]
        if args.electricity_maps_token:
            zones.extend(["DE", "NO-NO1"])

    results = []
    for zone in zones:
        result = test_zone(zone, args.eia_api_key, args.electricity_maps_token)
        results.append(result)

    print_results(results, args.eia_api_key, args.electricity_maps_token,
                  args.gridstatus_api_key)

    # Exit code: 1 if any errors
    has_errors = any(r["status"] == "error" for r in results)
    sys.exit(1 if has_errors else 0)


if __name__ == "__main__":
    main()
