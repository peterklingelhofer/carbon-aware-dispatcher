"""Standalone carbon-aware CLI.

Run the same grid engine outside GitHub Actions — from cron, Kubernetes
CronJobs, Airflow, systemd timers, or any shell — to gate or schedule
deferrable work (batch jobs, ML training, ETL) on grid carbon intensity. This is
where the real energy lives: a nightly training run or data pipeline dwarfs CI.

Commands:
  check           exit 0 if the grid is green now; compose as `carbon-aware check && ./job.sh`
  wait-for-green  block until green (or a deadline), then exit 0 so the next command runs
  best-window     print the cleanest upcoming window from forecasts (for schedulers)
  suggest-cron    recommend a daily cron at the cleanest hour (history > forecast > heuristic)
  curve           print the hour-of-day carbon curve from historical data
  report          emit a Software Carbon Intensity (SCI) report as JSON

Exit codes: 0 = green/clean, 1 = dirty or timed out, 2 = no data/error, 3 = usage.
Info logs go to stderr; stdout carries only the result (or JSON with --json), so
the tool composes cleanly in pipes and scripts.
"""

import argparse
import contextlib
import json
import sys
import time

import check_grid

EXIT_GREEN = 0
EXIT_DIRTY = 1
EXIT_NODATA = 2
EXIT_USAGE = 3

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text):
    """Parse a duration like '6h', '15m', '30s', '2d', or bare seconds. Seconds (int)."""
    s = (text or "").strip().lower()
    if not s:
        raise ValueError("empty duration")
    if s[-1] in _UNITS:
        return int(float(s[:-1]) * _UNITS[s[-1]])
    return int(float(s))


def _tokens(args):
    import os

    return {
        "eia": args.eia_key or os.environ.get("EIA_API_KEY", ""),
        "emaps": args.electricity_maps_token or os.environ.get("ELECTRICITY_MAPS_TOKEN", ""),
        "entsoe": args.entsoe_token or os.environ.get("ENTSOE_TOKEN", ""),
        "gridstatus": args.gridstatus_key or os.environ.get("GRID_STATUS_API_KEY", ""),
    }


def evaluate(args):
    """Check the zones once and return a result dict. Engine logs go to stderr."""
    zones = check_grid.parse_zones_input(args.zones)
    tok = _tokens(args)
    measured = []
    # Send the engine's progress prints to stderr so stdout stays machine-clean
    with contextlib.redirect_stdout(sys.stderr):
        best_zone, best_intensity, _, skipped = check_grid.check_multiple_zones(
            zones, args.max_carbon, tok["eia"], tok["emaps"], tok["entsoe"], collect=measured
        )
    if best_zone is not None:
        return {"status": "green", "zone": best_zone, "intensity": best_intensity}
    if measured:
        cleanest = min(measured, key=lambda m: m[1])
        return {"status": "dirty", "zone": cleanest[0], "intensity": cleanest[1]}
    return {"status": "error", "skipped": len(skipped)}


def _report(args, result, prefix=""):
    """Print a result to stdout (JSON or human) and return its exit code."""
    status = result["status"]
    if args.json:
        print(json.dumps({**result, "max_carbon": args.max_carbon}))
    elif status == "green":
        print(
            f"{prefix}GREEN: {result['zone']} at {result['intensity']} gCO2eq/kWh "
            f"(<= {args.max_carbon})"
        )
    elif status == "dirty":
        print(
            f"{prefix}DIRTY: cleanest {result['zone']} at {result['intensity']} gCO2eq/kWh "
            f"(> {args.max_carbon})"
        )
    else:
        print(f"{prefix}NO DATA: could not read any zone")
    return {"green": EXIT_GREEN, "dirty": EXIT_DIRTY}.get(status, EXIT_NODATA)


def cmd_check(args):
    return _report(args, evaluate(args))


def cmd_wait(args):
    deadline = parse_duration(args.max_wait)
    poll = parse_duration(args.poll)
    # Honesty: blocking holds the machine powered on. For recurring jobs, shifting
    # the schedule (suggest-cron) saves more, with no idle-energy waste.
    print(
        "note: blocking keeps this machine running; for recurring jobs prefer "
        "`carbon-aware suggest-cron` to shift the schedule instead.",
        file=sys.stderr,
    )
    waited = 0
    while True:
        result = evaluate(args)
        if result["status"] == "green":
            return _report(args, result)
        if waited >= deadline:
            if not args.json:
                print(f"TIMEOUT: no green window within {args.max_wait}")
            else:
                print(json.dumps({"status": "timeout", "max_carbon": args.max_carbon}))
            return EXIT_DIRTY
        sleep_for = min(poll, deadline - waited) or poll
        print(
            f"  not green ({result.get('intensity', '?')} gCO2eq/kWh); "
            f"sleeping {sleep_for}s ({waited}/{deadline}s elapsed)",
            file=sys.stderr,
        )
        time.sleep(sleep_for)
        waited += sleep_for


def _apply_sci_env(args):
    """Push the report's energy/SCI knobs into the shared model via env."""
    import os

    for flag, var in (
        ("energy_kwh", "JOB_ENERGY_KWH"),
        ("power_watts", "JOB_POWER_WATTS"),
        ("duration_minutes", "JOB_DURATION_MINUTES"),
        ("pue", "PUE"),
        ("embodied_grams", "EMBODIED_GRAMS"),
    ):
        value = getattr(args, flag, None)
        if value is not None:
            os.environ[var] = str(value)


def cmd_report(args):
    """Emit a Software Carbon Intensity (SCI) report as JSON on stdout.

    Aggregates cleanly for CSRD / GHG-Protocol sustainability reporting: one
    object per run with energy, intensity, PUE, embodied, and total emitted.
    """
    from datetime import datetime, timezone

    _apply_sci_env(args)
    result = evaluate(args)
    zone, intensity = result.get("zone"), result.get("intensity")
    if intensity is None:
        print(json.dumps({"status": "error", "reason": "no data"}))
        return EXIT_NODATA

    emitted = check_grid.estimate_emissions(intensity)
    report = {
        "schema": "sci-report/1",
        "spec": "https://sci.greensoftware.foundation/",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "zone": zone,
        "carbon_intensity_g_per_kwh": intensity,
        "energy_kwh": round(check_grid.resolve_energy_kwh(), 6),
        "pue": check_grid._pue(),
        "embodied_grams": check_grid._embodied_grams(),
        "emitted_grams": emitted,
        "functional_unit": args.functional_unit,
        "sci_grams_per_unit": emitted,
    }
    print(json.dumps(report, indent=None if args.json else 2))
    return EXIT_GREEN


def _emit_cron(args, zone, hour, intensity, source):
    """Print a cron recommendation for the cleanest hour. Returns exit code."""
    if hour is None:
        if args.json:
            print(json.dumps({"status": "none", "zone": zone}))
        else:
            print(f"No schedule suggestion available for {zone}")
        return EXIT_NODATA
    cron = f"0 {hour} * * *"
    desc = f"daily at {hour:02d}:00 UTC (cleanest hour for {zone}, {source})"
    if args.json:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "zone": zone,
                    "cron": cron,
                    "source": source,
                    "intensity": intensity,
                    "description": desc,
                }
            )
        )
    else:
        print(f"Suggested schedule: {cron}")
        print(f"  {desc}")
        print("  Shift your recurring job to this time — it saves on every run, no idle wait.")
    return EXIT_GREEN


def cmd_suggest_cron(args):
    """Recommend a daily cron at the grid's cleanest hour.

    Shifting a recurring job to its cleanest hour saves on every future run with
    zero idle waste — far better than blocking a runner. Prefers a historical
    hour-of-day curve (stable, multi-day) where free history exists, then the
    live forecast, then a per-zone heuristic.
    """
    from datetime import datetime

    import carbon_curve

    zones = check_grid.parse_zones_input(args.zones)
    tok = _tokens(args)
    first = zones[0]["zone"] if zones else args.zones

    with contextlib.redirect_stdout(sys.stderr):
        profile = carbon_curve.build_profile(first)
    if profile:
        hour, intensity = carbon_curve.cleanest_hour(profile)
        return _emit_cron(args, first, hour, intensity, "history")

    with contextlib.redirect_stdout(sys.stderr):
        zone, when, intensity = check_grid.queue_find_optimal_window(
            zones, args.max_carbon, 24, tok["eia"], tok["gridstatus"], tok["emaps"], tok["entsoe"]
        )
    if zone and when:
        try:
            hour = datetime.fromisoformat(when.replace("Z", "+00:00")).hour
            return _emit_cron(args, zone, hour, intensity, "forecast")
        except (ValueError, TypeError):
            pass

    # Last resort: the per-zone energy-type heuristic
    cron, desc = check_grid.suggest_green_cron(first)
    if not cron:
        if args.json:
            print(json.dumps({"status": "none", "zone": first}))
        else:
            print(f"No schedule suggestion available for {first}")
        return EXIT_NODATA
    if args.json:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "zone": first,
                    "cron": cron,
                    "source": "heuristic",
                    "description": desc,
                }
            )
        )
    else:
        print(f"Suggested schedule: {cron}")
        print(f"  {desc} [heuristic]")
        print("  Shift your recurring job to this time — it saves on every run, no idle wait.")
    return EXIT_GREEN


def cmd_curve(args):
    """Print the hour-of-day carbon curve from historical data (where free)."""
    import carbon_curve

    zones = check_grid.parse_zones_input(args.zones)
    first = zones[0]["zone"] if zones else args.zones
    with contextlib.redirect_stdout(sys.stderr):
        profile = carbon_curve.build_profile(first)
    if not profile:
        if args.json:
            print(json.dumps({"status": "unavailable", "zone": first}))
        else:
            print(f"No free historical curve for {first} (GB has the richest free history)")
        return EXIT_NODATA

    hour, intensity = carbon_curve.cleanest_hour(profile)
    spread = carbon_curve.spread_pct(profile)
    if args.json:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "zone": first,
                    "cleanest_hour": hour,
                    "cleanest_intensity": intensity,
                    "spread_pct": spread,
                    "profile": profile,
                }
            )
        )
    else:
        print(f"Hour-of-day carbon curve for {first} (gCO2eq/kWh, UTC):")
        for h in sorted(profile):
            mark = "  <- cleanest" if h == hour else ""
            print(f"  {h:02d}:00  {profile[h]:.0f}{mark}")
        print(f"Cleanest hour: {hour:02d}:00 UTC ({intensity:.0f}); spread {spread:.0f}%")
    return EXIT_GREEN


def cmd_best_window(args):
    zones = check_grid.parse_zones_input(args.zones)
    tok = _tokens(args)
    with contextlib.redirect_stdout(sys.stderr):
        zone, when, intensity = check_grid.queue_find_optimal_window(
            zones,
            args.max_carbon,
            args.hours,
            tok["eia"],
            tok["gridstatus"],
            tok["emaps"],
            tok["entsoe"],
        )
    if zone is None:
        if args.json:
            print(json.dumps({"status": "none", "hours": args.hours}))
        else:
            print(f"No green window forecast within {args.hours}h")
        return EXIT_DIRTY
    if args.json:
        print(json.dumps({"status": "window", "zone": zone, "at": when, "intensity": intensity}))
    else:
        print(f"Cleanest window: {zone} at {when} ({intensity} gCO2eq/kWh)")
    return EXIT_GREEN


def build_parser():
    p = argparse.ArgumentParser(prog="carbon-aware", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument(
            "--zones",
            default="auto:green",
            help="Zones or preset (e.g. 'GB,CISO' or 'auto:green'). Default: auto:green",
        )
        sp.add_argument(
            "--max-carbon",
            type=float,
            default=200.0,
            help="Max gCO2eq/kWh to count as green. Default: 200",
        )
        sp.add_argument("--json", action="store_true", help="Emit a JSON result on stdout")
        sp.add_argument("--eia-key", default="")
        sp.add_argument("--electricity-maps-token", default="")
        sp.add_argument("--entsoe-token", default="")
        sp.add_argument("--gridstatus-key", default="")

    c = sub.add_parser("check", help="Exit 0 if the grid is green now")
    add_common(c)
    c.set_defaults(func=cmd_check)

    w = sub.add_parser("wait-for-green", help="Block until green or a deadline")
    add_common(w)
    w.add_argument("--max-wait", default="6h", help="Give up after this long. Default: 6h")
    w.add_argument("--poll", default="15m", help="How often to recheck. Default: 15m")
    w.set_defaults(func=cmd_wait)

    b = sub.add_parser("best-window", help="Print the cleanest upcoming forecast window")
    add_common(b)
    b.add_argument("--hours", type=int, default=24, help="Forecast horizon in hours. Default: 24")
    b.set_defaults(func=cmd_best_window)

    s = sub.add_parser("suggest-cron", help="Recommend a daily cron at the cleanest hour")
    add_common(s)
    s.set_defaults(func=cmd_suggest_cron)

    cv = sub.add_parser("curve", help="Print the hour-of-day carbon curve (historical)")
    add_common(cv)
    cv.set_defaults(func=cmd_curve)

    r = sub.add_parser("report", help="Emit an SCI (carbon) report as JSON for reporting")
    add_common(r)
    r.add_argument("--energy-kwh", type=float, help="Measured energy this run uses (kWh)")
    r.add_argument("--power-watts", type=float, help="Average power draw (W), used with duration")
    r.add_argument("--duration-minutes", type=float, help="Job duration (minutes)")
    r.add_argument("--pue", type=float, help="Datacenter PUE multiplier (e.g. 1.12)")
    r.add_argument("--embodied-grams", type=float, help="Amortized embodied gCO2 for this run")
    r.add_argument("--functional-unit", default="run", help="SCI functional unit label")
    r.set_defaults(func=cmd_report)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
