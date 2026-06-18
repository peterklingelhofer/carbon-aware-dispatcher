#!/usr/bin/env python3
"""Carbon-Aware Dispatcher: Setup Wizard.

Validates API keys, tests zone connectivity, and prints a configuration summary.
Run locally or in CI to verify your setup before using the action.

Usage:
    python setup_wizard.py                          # Interactive: prompts for zones/keys
    python setup_wizard.py --zone CISO              # Test a single zone
    python setup_wizard.py --zones "CISO,GB,DE"     # Test multiple zones
    python setup_wizard.py --auto-green             # Test the auto:green preset
    python setup_wizard.py --auto-cleanest          # Test all free-provider zones

Environment variables (alternative to flags):
    EIA_API_KEY, ELECTRICITY_MAPS_TOKEN, GRID_STATUS_API_KEY, ENTSOE_TOKEN
"""

import argparse
import os
import sys

# Add the repo root to the path so we can import providers
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from providers import (
    AUTO_CLEANEST_ZONES,
    AUTO_GREEN_ZONES,
    PROVIDER_AEMO,
    PROVIDER_CANADA,
    PROVIDER_EIA,
    PROVIDER_EIRGRID,
    PROVIDER_ELECTRICITY_MAPS,
    PROVIDER_ENERGINET,
    PROVIDER_ENTSOE,
    PROVIDER_ESKOM,
    PROVIDER_GRID_INDIA,
    PROVIDER_ONS_BRAZIL,
    PROVIDER_OPEN_METEO,
    PROVIDER_TAIWAN,
    PROVIDER_UK,
    aemo,
    canada,
    detect_provider,
    eia,
    eirgrid,
    electricity_maps,
    energinet,
    entsoe,
    eskom,
    grid_india,
    ons_brazil,
    open_meteo,
    taiwan,
    uk,
)

# Provider display names for the wizard
_PROVIDER_NAMES = {
    PROVIDER_UK: "UK Carbon Intensity (free, no key)",
    PROVIDER_EIA: "EIA API (US, free)",
    PROVIDER_AEMO: "AEMO NEM (Australia, free)",
    PROVIDER_GRID_INDIA: "Grid India (free, no key)",
    PROVIDER_ONS_BRAZIL: "ONS Brazil (free, no key)",
    PROVIDER_ESKOM: "Eskom South Africa (free, no key)",
    PROVIDER_CANADA: "Canada IESO/AESO/Quebec (free, no key)",
    PROVIDER_TAIWAN: "Taipower Taiwan (free, no key)",
    PROVIDER_EIRGRID: "EirGrid Ireland (free, no key)",
    PROVIDER_ENERGINET: "Energinet Denmark (free, no key)",
    PROVIDER_ENTSOE: "ENTSO-E (EU, free token)",
    PROVIDER_OPEN_METEO: "Open-Meteo estimate (free)",
    PROVIDER_ELECTRICITY_MAPS: "Electricity Maps (free token)",
}

# Provider modules and their auth argument needs
_PROVIDER_MODULES = {
    PROVIDER_UK: uk,
    PROVIDER_EIA: eia,
    PROVIDER_AEMO: aemo,
    PROVIDER_GRID_INDIA: grid_india,
    PROVIDER_ONS_BRAZIL: ons_brazil,
    PROVIDER_ESKOM: eskom,
    PROVIDER_CANADA: canada,
    PROVIDER_TAIWAN: taiwan,
    PROVIDER_EIRGRID: eirgrid,
    PROVIDER_ENERGINET: energinet,
    PROVIDER_ENTSOE: entsoe,
    PROVIDER_OPEN_METEO: open_meteo,
    PROVIDER_ELECTRICITY_MAPS: electricity_maps,
}

_PROVIDER_AUTH_ARGS = {
    PROVIDER_EIA: lambda keys: [keys.get("eia_api_key", "")],
    PROVIDER_ENTSOE: lambda keys: [keys.get("entsoe_token", "")],
    PROVIDER_ELECTRICITY_MAPS: lambda keys: [keys.get("emaps_api_key", "")],
}


def test_zone(zone, eia_api_key="", emaps_api_key="", entsoe_token=""):
    """Test connectivity and data retrieval for a single zone.

    Returns a dict with test results.
    """
    provider = detect_provider(zone, entsoe_token)
    provider_name = _PROVIDER_NAMES.get(provider, provider)

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
        result["error"] = (
            "No electricity_maps_token. Get free at https://portal.electricitymaps.com/"
        )
        return result

    if provider == PROVIDER_ENTSOE and not entsoe_token:
        result["status"] = "skipped"
        result["error"] = "No entsoe_token. Get free at https://transparency.entsoe.eu/"
        return result

    # Test the API using the registry
    try:
        module = _PROVIDER_MODULES.get(provider)
        if module is None:
            result["status"] = "error"
            result["error"] = f"Unknown provider: {provider}"
            return result

        keys = {
            "eia_api_key": eia_api_key,
            "emaps_api_key": emaps_api_key,
            "entsoe_token": entsoe_token,
        }
        resolver = _PROVIDER_AUTH_ARGS.get(provider)
        extra = resolver(keys) if resolver else []

        is_green, intensity = module.check_carbon_intensity(zone, 9999, *extra)

        if intensity is not None:
            result["status"] = "ok"
            result["intensity"] = intensity
        else:
            result["status"] = "error"
            result["error"] = "API returned no data; zone code may be invalid"
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)

    return result


def print_results(
    results, eia_api_key="", emaps_api_key="", gridstatus_api_key="", entsoe_token=""
):
    """Print a formatted summary of test results."""
    print("\n" + "=" * 64)
    print("  Carbon-Aware Dispatcher: Setup Wizard")
    print("=" * 64)

    # API key status
    print("\n  API Keys & Tokens:")
    print("  " + "-" * 60)

    # Free providers (no key needed)
    print("    UK Carbon Intensity:   no key needed")
    print("    AEMO (Australia):      no key needed")
    print("    Grid India:            no key needed")
    print("    ONS Brazil:            no key needed")
    print("    Eskom (South Africa):  no key needed")
    print("    Open-Meteo:            no key needed")

    # EIA
    if eia_api_key and eia_api_key != "DEMO_KEY":
        print("    EIA (US):              custom key configured")
    else:
        print("    EIA (US):              using DEMO_KEY (rate limited)")
        print("      Register free: https://www.eia.gov/opendata/register.php")

    # ENTSO-E
    if entsoe_token:
        print("    ENTSO-E (EU):          token configured")
    else:
        print("    ENTSO-E (EU):          not configured (36 EU countries unavailable)")
        print("      Register free: https://transparency.entsoe.eu/")

    # Electricity Maps
    if emaps_api_key:
        print("    Electricity Maps:      token configured")
    else:
        print("    Electricity Maps:      not configured (200+ global zones unavailable)")
        print("      Register free: https://portal.electricitymaps.com/")

    # GridStatus
    if gridstatus_api_key:
        print("    GridStatus.io:         key configured (US forecasts enabled)")
    else:
        print("    GridStatus.io:         not configured (US forecasts unavailable)")
        print("      Register free: https://www.gridstatus.io")

    # Zone results
    print(f"\n  Zone Tests ({len(results)} zones):")
    print("  " + "-" * 60)

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
            print(f"    {zone:<12} SKIPPED: {r['error']}")
        else:
            err_count += 1
            print(f"    {zone:<12} ERROR: {r['error']}")

    print("  " + "-" * 60)
    print(f"    {ok_count} ok, {skip_count} skipped, {err_count} errors")

    # Recommendations
    print("\n  Recommendations:")
    if err_count == 0 and skip_count == 0:
        print("    All zones working! Your configuration is ready to use.")
    else:
        if skip_count > 0:
            if not emaps_api_key:
                print("    - Add electricity_maps_token to enable 200+ global zones")
            if not entsoe_token:
                has_eu = any(
                    detect_provider(r["zone"]) == PROVIDER_ENTSOE
                    for r in results
                    if r["status"] == "skipped"
                )
                if has_eu:
                    print("    - Add entsoe_token to enable 36 EU country zones")
        if err_count > 0:
            print("    - Check zone codes match your provider (see README)")
        if not eia_api_key or eia_api_key == "DEMO_KEY":
            has_eia = any(detect_provider(r["zone"]) == PROVIDER_EIA for r in results)
            if has_eia:
                print("    - Register a free EIA API key for higher rate limits")

    # Zero-config suggestion
    print("\n  Quick Start (zero config):")
    print("    grid_zone: 'auto:cleanest'   # Tests 17 zones across 8 free providers")
    print("    grid_zone: 'auto:green'      # 15 curated green-energy zones")

    # Example workflow snippet
    zones_str = ",".join(r["zone"] for r in results if r["status"] == "ok")
    if zones_str:
        print("\n  Custom config from your test:")
        print(f"    grid_zones: '{zones_str}'")
        greenest = min((r for r in results if r["status"] == "ok"), key=lambda r: r["intensity"])
        print(
            f"    Greenest zone right now: {greenest['zone']} ({greenest['intensity']} gCO2eq/kWh)"
        )

    print("\n" + "=" * 64)


def main():
    parser = argparse.ArgumentParser(description="Carbon-Aware Dispatcher: Setup Wizard")
    parser.add_argument("--zone", help="Test a single zone")
    parser.add_argument("--zones", help="Test comma-separated zones")
    parser.add_argument(
        "--auto-green", action="store_true", help="Test the auto:green preset zones"
    )
    parser.add_argument(
        "--auto-cleanest",
        action="store_true",
        help="Test the auto:cleanest preset (all free providers)",
    )
    parser.add_argument("--eia-api-key", default=os.environ.get("EIA_API_KEY", ""))
    parser.add_argument(
        "--electricity-maps-token", default=os.environ.get("ELECTRICITY_MAPS_TOKEN", "")
    )
    parser.add_argument("--gridstatus-api-key", default=os.environ.get("GRID_STATUS_API_KEY", ""))
    parser.add_argument("--entsoe-token", default=os.environ.get("ENTSOE_TOKEN", ""))

    args = parser.parse_args()

    # Determine zones to test
    zones = []
    if args.auto_cleanest:
        zones = [z["zone"] for z in AUTO_CLEANEST_ZONES]
    elif args.auto_green:
        zones = [z["zone"] for z in AUTO_GREEN_ZONES]
    elif args.zones:
        zones = [z.strip() for z in args.zones.split(",") if z.strip()]
    elif args.zone:
        zones = [args.zone]
    else:
        # Interactive: test one zone from each free provider
        print("No zones specified. Testing one zone from each free provider...\n")
        zones = ["CISO", "GB", "AU-NSW", "IN-SO", "BR-S", "ZA"]
        if args.entsoe_token:
            zones.append("DE")
        if args.electricity_maps_token:
            zones.append("NO-NO1")

    results = []
    for zone in zones:
        result = test_zone(zone, args.eia_api_key, args.electricity_maps_token, args.entsoe_token)
        results.append(result)

    print_results(
        results,
        args.eia_api_key,
        args.electricity_maps_token,
        args.gridstatus_api_key,
        args.entsoe_token,
    )

    # Exit code: 1 if any errors
    has_errors = any(r["status"] == "error" for r in results)
    sys.exit(1 if has_errors else 0)


if __name__ == "__main__":
    main()
