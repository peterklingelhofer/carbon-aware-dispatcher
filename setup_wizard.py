#!/usr/bin/env python3
"""Carbon-Aware Dispatcher: Setup Wizard.

Validates API keys, tests zone connectivity, and prints a configuration summary.
Run locally or in CI to verify your setup before using the action.

Usage:
    python setup_wizard.py                          # Default: tests one zone per free provider
    python setup_wizard.py --zone CISO              # Test a single zone
    python setup_wizard.py --zones "CISO,GB,DE"     # Test multiple zones
    python setup_wizard.py --auto-green             # Test the auto:green preset
    python setup_wizard.py --auto-cleanest          # Test the auto:cleanest curated zones

Environment variables (alternative to flags):
    EIA_API_KEY, ELECTRICITY_MAPS_TOKEN, GRID_STATUS_API_KEY, ENTSOE_TOKEN
"""

import argparse
import os
import sys

import check_grid
from providers import (
    AUTO_CLEANEST_ZONES,
    AUTO_GREEN_ZONES,
    PROVIDER_AEMO,
    PROVIDER_CAMMESA,
    PROVIDER_CANADA,
    PROVIDER_EIA,
    PROVIDER_EIRGRID,
    PROVIDER_ELECTRICITY_MAPS,
    PROVIDER_ENERGINET,
    PROVIDER_ENERGY_CHARTS,
    PROVIDER_ENTSOE,
    PROVIDER_ESKOM,
    PROVIDER_GRID_INDIA,
    PROVIDER_ONS_BRAZIL,
    PROVIDER_OPEN_METEO,
    PROVIDER_RTE,
    PROVIDER_TAIWAN,
    PROVIDER_UK,
    aemo,
    cammesa,
    canada,
    detect_provider,
    eia,
    eirgrid,
    electricity_maps,
    energinet,
    energy_charts,
    entsoe,
    eskom,
    grid_india,
    ons_brazil,
    open_meteo,
    rte,
    taiwan,
    uk,
)

_PROVIDER_NAMES = {
    PROVIDER_UK: "UK Carbon Intensity (free, no key)",
    PROVIDER_EIA: "EIA API (US, free)",
    PROVIDER_AEMO: "AEMO NEM (Australia, free)",
    PROVIDER_GRID_INDIA: "Grid India (free, no key)",
    PROVIDER_ONS_BRAZIL: "ONS Brazil (free, no key)",
    PROVIDER_ESKOM: "Eskom South Africa (free, no key)",
    PROVIDER_CANADA: "Canada IESO/AESO/Quebec (free, no key)",
    PROVIDER_CAMMESA: "CAMMESA Argentina (free, no key)",
    PROVIDER_TAIWAN: "Taipower Taiwan (free, no key)",
    PROVIDER_EIRGRID: "EirGrid Ireland (free, no key)",
    PROVIDER_ENERGINET: "Energinet Denmark (free, no key)",
    PROVIDER_RTE: "RTE eco2mix France (free, no key)",
    PROVIDER_ENERGY_CHARTS: "Energy-Charts EU (free, no key)",
    PROVIDER_ENTSOE: "ENTSO-E (EU, free token)",
    PROVIDER_OPEN_METEO: "Open-Meteo estimate (free)",
    PROVIDER_ELECTRICITY_MAPS: "Electricity Maps (free token)",
}

_PROVIDER_MODULES = {
    PROVIDER_UK: uk,
    PROVIDER_EIA: eia,
    PROVIDER_AEMO: aemo,
    PROVIDER_GRID_INDIA: grid_india,
    PROVIDER_ONS_BRAZIL: ons_brazil,
    PROVIDER_ESKOM: eskom,
    PROVIDER_CANADA: canada,
    PROVIDER_CAMMESA: cammesa,
    PROVIDER_TAIWAN: taiwan,
    PROVIDER_EIRGRID: eirgrid,
    PROVIDER_ENERGINET: energinet,
    PROVIDER_RTE: rte,
    PROVIDER_ENERGY_CHARTS: energy_charts,
    PROVIDER_ENTSOE: entsoe,
    PROVIDER_OPEN_METEO: open_meteo,
    PROVIDER_ELECTRICITY_MAPS: electricity_maps,
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

    try:
        extra = check_grid._get_extra_args(
            provider,
            eia_api_key=eia_api_key,
            emaps_api_key=emaps_api_key,
            entsoe_token=entsoe_token,
        )
        _is_green, intensity = _PROVIDER_MODULES[provider].check_carbon_intensity(
            zone, 9999, *extra
        )
        if intensity is not None:
            result["status"] = "ok"
            result["intensity"] = intensity
        else:
            result["status"] = "error"
            result["error"] = "API returned no data. Zone code may be invalid"
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)

    return result


def print_results(
    results,
    eia_api_key="",
    emaps_api_key="",
    gridstatus_api_key="",
    entsoe_token="",
    routing="gate",
):
    """Print a formatted summary of test results."""
    print("\n" + "=" * 64)
    print("  Carbon-Aware Dispatcher: Setup Wizard")
    print("=" * 64)

    print("\n  API Keys & Tokens:")
    print("  " + "-" * 60)

    print("    UK Carbon Intensity:   no key needed")
    print("    AEMO (Australia):      no key needed")
    print("    Grid India:            no key needed")
    print("    ONS Brazil:            no key needed")
    print("    Eskom (South Africa):  no key needed")
    print("    Open-Meteo:            no key needed")

    if eia_api_key and eia_api_key != "DEMO_KEY":
        print("    EIA (US):              custom key configured")
    else:
        print("    EIA (US):              using DEMO_KEY (rate limited)")
        print("      Register free: https://www.eia.gov/opendata/register.php")

    if entsoe_token:
        print("    ENTSO-E (EU):          token configured")
    else:
        print("    ENTSO-E (EU):          not configured (36 EU countries unavailable)")
        print("      Register free: https://transparency.entsoe.eu/")

    if emaps_api_key:
        print("    Electricity Maps:      token configured")
    else:
        print("    Electricity Maps:      not configured (200+ global zones unavailable)")
        print("      Register free: https://portal.electricitymaps.com/")

    if gridstatus_api_key:
        print("    GridStatus.io:         key configured (US forecasts enabled)")
    else:
        print("    GridStatus.io:         not configured (US forecasts unavailable)")
        print("      Register free: https://www.gridstatus.io")

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

    print("\n  Quick Start (zero config):")
    n_clean, n_green = len(AUTO_CLEANEST_ZONES), len(AUTO_GREEN_ZONES)
    print(f"    grid_zone: 'auto:cleanest'   # Tests {n_clean} zones across free providers")
    print(f"    grid_zone: 'auto:green'      # {n_green} curated green-energy zones")

    ok = [r for r in results if r["status"] == "ok"]
    zones_str = ",".join(r["zone"] for r in ok)
    if ok:
        print("\n  Custom config from your test:")
        print(f"    grid_zones: '{zones_str}'")
        greenest = min(ok, key=lambda r: r["intensity"])
        print(
            f"    Greenest zone right now: {greenest['zone']} ({greenest['intensity']} gCO2eq/kWh)"
        )

    zones_snippet = zones_str or "CISO,GB"
    print(f"\n  Routing mode: {routing}")
    print("  " + "-" * 60)
    if routing == "runner":
        print("    Requires RunsOn (https://runs-on.com) installed in your org.")
        print("    Greenest AWS regions: eu-north-1 (SE-SE3), eu-west-1 (IE), us-west-2 (BPAT)")
        print("    AWS commits to 100% renewable for all three: cleanest year-round.\n")
        green_runner_zones = "SE-SE3,IE,BPAT"
        print("    jobs:")
        print("      pick-region:")
        print("        runs-on: ubuntu-latest")
        print("        outputs:")
        print("          runner: ${{ steps.carbon.outputs.runner_label }}")
        print("        steps:")
        print("          - uses: peterklingelhofer/carbon-aware-dispatcher@v1")
        print("            id: carbon")
        print("            with:")
        print(f"              grid_zones: '{green_runner_zones}'  # Stockholm, Ireland, Oregon")
        print("              runner_provider: 'runson'")
        print("              runner_spec: '2cpu-linux-x64'")
        print("      build:")
        print("        needs: pick-region")
        print("        runs-on: ${{ needs.pick-region.outputs.runner }}")
        print("        steps:")
        print("          - uses: actions/checkout@v5")
        print("          - run: echo 'your build here'")
    elif routing == "deploy":
        print("    Set the region env var for your cloud CLI. Unused vars are harmless.\n")
        print("    jobs:")
        print("      find-region:")
        print("        runs-on: ubuntu-latest")
        print("        outputs:")
        print("          aws:   ${{ steps.carbon.outputs.cloud_region }}")
        print("          gcp:   ${{ steps.carbon.outputs.gcp_region }}")
        print("          azure: ${{ steps.carbon.outputs.azure_region }}")
        print("        steps:")
        print("          - uses: peterklingelhofer/carbon-aware-dispatcher@v1")
        print("            id: carbon")
        print("            with:")
        print(f"              grid_zones: '{zones_snippet}'")
        print("      deploy:")
        print("        needs: find-region")
        print("        runs-on: ubuntu-latest")
        print("        env:")
        print("          AWS_DEFAULT_REGION:      ${{ needs.find-region.outputs.aws }}")
        print("          CLOUDSDK_COMPUTE_REGION: ${{ needs.find-region.outputs.gcp }}")
        print("          AZURE_DEFAULTS_LOCATION: ${{ needs.find-region.outputs.azure }}")
        print("        steps:")
        print("          - uses: actions/checkout@v5")
        print("          - run: aws s3 sync ./dist s3://my-bucket/")
        print("          # - run: gcloud run deploy my-service ...")
        print("          # - run: az webapp up --name my-app")
    else:
        print("    Use --routing=runner or --routing=deploy to see routing snippets.\n")
        print("    jobs:")
        print("      build:")
        print("        runs-on: ubuntu-latest")
        print("        steps:")
        print("          - uses: peterklingelhofer/carbon-aware-dispatcher@v1")
        print("            id: carbon")
        print("            with:")
        print(f"              grid_zones: '{zones_snippet}'")
        print("          - if: steps.carbon.outputs.grid_clean == 'true'")
        print("            uses: actions/checkout@v5")
        print("          - if: steps.carbon.outputs.grid_clean == 'true'")
        print("            run: echo 'your build here'")

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
        help=f"Test the auto:cleanest preset ({len(AUTO_CLEANEST_ZONES)} curated zones)",
    )
    parser.add_argument("--eia-api-key", default=os.environ.get("EIA_API_KEY", ""))
    parser.add_argument(
        "--electricity-maps-token", default=os.environ.get("ELECTRICITY_MAPS_TOKEN", "")
    )
    parser.add_argument("--gridstatus-api-key", default=os.environ.get("GRID_STATUS_API_KEY", ""))
    parser.add_argument("--entsoe-token", default=os.environ.get("ENTSOE_TOKEN", ""))
    parser.add_argument(
        "--routing",
        choices=["gate", "runner", "deploy"],
        default="gate",
        help="Workflow snippet to emit: gate (default), runner (RunsOn), deploy (region env vars)",
    )

    args = parser.parse_args()

    if args.auto_cleanest:
        zones = [z["zone"] for z in AUTO_CLEANEST_ZONES]
    elif args.auto_green:
        zones = [z["zone"] for z in AUTO_GREEN_ZONES]
    elif args.zones:
        zones = [z.strip() for z in args.zones.split(",") if z.strip()]
    elif args.zone:
        zones = [args.zone]
    else:
        print("No zones specified. Testing one zone from each free provider...\n")
        zones = ["CISO", "GB", "AU-NSW", "IN-SO", "BR-S", "ZA"]
        if args.entsoe_token:
            zones.append("DE")
        if args.electricity_maps_token:
            zones.append("NO-NO1")

    results = [
        test_zone(
            zone,
            eia_api_key=args.eia_api_key,
            emaps_api_key=args.electricity_maps_token,
            entsoe_token=args.entsoe_token,
        )
        for zone in zones
    ]
    print_results(
        results,
        args.eia_api_key,
        args.electricity_maps_token,
        args.gridstatus_api_key,
        args.entsoe_token,
        args.routing,
    )

    sys.exit(1 if any(r["status"] == "error" for r in results) else 0)


if __name__ == "__main__":
    main()
