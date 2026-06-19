"""Standalone carbon-aware CLI.

Run the same grid engine outside GitHub Actions (from cron, Kubernetes
CronJobs, Airflow, systemd timers, or any shell) to gate or schedule
deferrable work (batch jobs, ML training, ETL) on grid carbon intensity. This is
where the real energy lives: a nightly training run or data pipeline dwarfs CI.

Commands:
  check           exit 0 if the grid is green now; compose as `carbon-aware check && ./job.sh`
  wait-for-green  block until green (or a deadline), then exit 0 so the next command runs
  marginal        WattTime marginal-emissions signal (the real avoided-emissions metric)
  best-window     print the cleanest upcoming window from forecasts (for schedulers)
  suggest-cron    recommend a cron at the cleanest hour/window (--duration-hours for batch)
  suggest-region  recommend the cleanest region among candidates, with savings
  plan            combined when+where: the cleanest (region, hour) across zones
  audit           scan a repo's workflows and rank schedules worth shifting
  schedule-cost   rank scheduled workflows by annual emissions (run less often)
  score           grade the repo's scheduling carbon posture (A-F) + badge
  advise          one prioritized carbon action plan across every lever
  curve           print the hour-of-day carbon curve from historical data
  export-curves   export accumulated curves to share (community data commons)
  merge-curves    pool many exported curve files into one shared community curve
  validate-curves check contributed curve files before they enter the pool
  worth-it        say whether scheduling helps this zone (flat grids don't)
  sla             report Green SLA compliance (share of runs that ran clean)
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


def cmd_marginal(args):
    """Report the WattTime marginal-emissions signal for timing decisions.

    Marginal intensity (the generator that responds to YOUR added load) is the
    metric that reflects real avoided emissions from shifting, unlike average
    intensity. Free for CAISO_NORTH. A low percentile means a relatively clean
    margin right now. Exit 0 clean, 1 dirty, 2 no data/credentials.
    """
    import os

    from providers import watttime

    user = args.username or os.environ.get("WATTTIME_USERNAME", "")
    password = args.password or os.environ.get("WATTTIME_PASSWORD", "")
    if not user or not password:
        msg = "marginal needs WattTime credentials (--username/--password or env)"
        print(json.dumps({"status": "no_credentials"}) if args.json else msg, file=sys.stderr)
        return EXIT_NODATA

    with contextlib.redirect_stdout(sys.stderr):
        token = watttime.login(user, password)
        pct = watttime.get_marginal_index(args.region, token) if token else None
    if pct is None:
        print(
            json.dumps({"status": "no_data", "region": args.region})
            if args.json
            else f"No marginal signal for {args.region}",
            file=sys.stderr,
        )
        return EXIT_NODATA

    clean = pct <= args.max_percentile
    if args.json:
        print(
            json.dumps(
                {
                    "status": "clean" if clean else "dirty",
                    "region": args.region,
                    "percentile": pct,
                    "max_percentile": args.max_percentile,
                }
            )
        )
    else:
        verdict = "CLEAN" if clean else "DIRTY"
        print(
            f"{verdict}: {args.region} marginal at {pct}th percentile "
            f"(threshold {args.max_percentile}; lower = cleaner margin)"
        )
    return EXIT_GREEN if clean else EXIT_DIRTY


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


def _energy_kwh(args):
    """Resolve the run's energy (kWh) for savings math, honoring --energy-kwh."""
    import os

    value = getattr(args, "energy_kwh", None)
    if value is not None:
        os.environ["JOB_ENERGY_KWH"] = str(value)
    return check_grid.resolve_energy_kwh()


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


def _measure_all(args):
    """Measure every candidate zone once; return [(zone, intensity)] (engine logs to stderr)."""
    zones = check_grid.parse_zones_input(args.zones)
    tok = _tokens(args)
    measured = []
    with contextlib.redirect_stdout(sys.stderr):
        check_grid.check_multiple_zones(
            zones, args.max_carbon, tok["eia"], tok["emaps"], tok["entsoe"], collect=measured
        )
    return measured


def cmd_suggest_region(args):
    """Recommend the cleanest region among candidates, with quantified savings.

    WHERE you run usually beats WHEN: moving a flexible workload to a clean grid
    can cut emissions several-fold. Compares candidates' current intensity and
    quantifies the saving vs the dirtiest (or a stated --current region). Region
    moves carry latency/data-residency/egress costs, so the verdict says so.
    """
    measured = _measure_all(args)
    if not measured:
        if args.json:
            print(json.dumps({"status": "error", "reason": "no data"}))
        else:
            print("NO DATA: could not read any candidate zone")
        return EXIT_NODATA

    cleanest = min(measured, key=lambda m: m[1])
    current = [m for m in measured if m[0] == args.current] if args.current else []
    baseline = current[0] if current else max(measured, key=lambda m: m[1])
    baseline_label = baseline[0] if current else f"{baseline[0]} (dirtiest candidate)"

    energy = _energy_kwh(args)
    per_run = round(max(0.0, (baseline[1] - cleanest[1]) * energy), 1)
    annual_kg = round(per_run * 365 / 1000, 1)
    already = cleanest[0] == baseline[0] or per_run <= 0

    if args.json:
        print(
            json.dumps(
                {
                    "status": "already_cleanest" if already else "move",
                    "cleanest_zone": cleanest[0],
                    "cleanest_intensity": cleanest[1],
                    "baseline_zone": baseline[0],
                    "baseline_intensity": baseline[1],
                    "savings_g_per_run": per_run,
                    "savings_kg_per_year": annual_kg,
                    "candidates": [
                        {"zone": z, "intensity": i} for z, i in sorted(measured, key=lambda m: m[1])
                    ],
                }
            )
        )
    elif already:
        print(f"Already on the cleanest candidate: {cleanest[0]} ({cleanest[1]} gCO2eq/kWh).")
    else:
        print(
            f"Run in {cleanest[0]} ({cleanest[1]} gCO2eq/kWh) instead of "
            f"{baseline_label} ({baseline[1]} gCO2eq/kWh):"
        )
        print(f"  ~{per_run:.0f} g CO2/run saved (~{annual_kg:.1f} kg/yr at daily cadence).")
        print("  Verify latency, data residency, and egress cost before migrating.")
    return EXIT_GREEN if not already else EXIT_DIRTY


def _emit_cron(
    args,
    zone,
    hour,
    intensity,
    source,
    note=None,
    savings_g=None,
    window_hours=None,
    dow=None,
    day_name=None,
):
    """Print a cron recommendation for the cleanest hour. Returns exit code."""
    if hour is None:
        if args.json:
            print(json.dumps({"status": "none", "zone": zone}))
        else:
            print(f"No schedule suggestion available for {zone}")
        return EXIT_NODATA
    cron = f"0 {hour} * * {dow if dow is not None else '*'}"
    if dow is not None:
        desc = f"weekly on {day_name} at {hour:02d}:00 UTC (cleanest day+hour for {zone}, {source})"
    elif window_hours and window_hours > 1:
        desc = (
            f"start a {window_hours}h job at {hour:02d}:00 UTC "
            f"(cleanest {window_hours}h window for {zone}, {source})"
        )
    else:
        desc = f"daily at {hour:02d}:00 UTC (cleanest hour for {zone}, {source})"
    savings_line = None
    if savings_g and savings_g > 0:
        savings_line = (
            f"~{savings_g:.0f} g CO2/run cleaner than your average run time "
            f"(~{savings_g * 365 / 1000:.1f} kg/yr at daily cadence)"
        )
    if args.json:
        payload = {
            "status": "ok",
            "zone": zone,
            "cron": cron,
            "source": source,
            "intensity": intensity,
            "description": desc,
        }
        if note:
            payload["note"] = note
        if savings_g and savings_g > 0:
            payload["savings_g_per_run"] = savings_g
        print(json.dumps(payload))
    else:
        print(f"Suggested schedule: {cron}")
        print(f"  {desc}")
        if savings_line:
            print(f"  {savings_line}")
        if note:
            print(f"  note: {note}")
        print("  Shift your recurring job to this time: it saves on every run, no idle wait.")
    return EXIT_GREEN


def cmd_suggest_cron(args):
    """Recommend a daily cron at the grid's cleanest hour.

    Shifting a recurring job to its cleanest hour saves on every future run with
    zero idle waste, far better than blocking a runner. Prefers a historical
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
        note = None
        if not carbon_curve.is_worth_shifting(profile):
            note = (
                f"grid is fairly flat ({carbon_curve.spread_pct(profile):.0f}% spread); "
                "shifting saves little"
            )
        energy = _energy_kwh(args)
        mean = carbon_curve.mean_intensity(profile)
        duration = int(getattr(args, "duration_hours", 1) or 1)
        if duration > 1:
            # Batch jobs want the cleanest contiguous block of hours
            start, wavg = carbon_curve.cleanest_window(profile, duration)
            if start is not None:
                # energy is the whole job's energy, so no extra x duration here
                savings = round(max(0.0, (mean - wavg) * energy), 1)
                return _emit_cron(
                    args,
                    first,
                    start,
                    wavg,
                    "history",
                    note=note,
                    savings_g=savings,
                    window_hours=duration,
                )
        hour, intensity = carbon_curve.cleanest_hour(profile)
        savings = round(max(0.0, (mean - intensity) * energy), 1)
        dow = day_name = None
        if getattr(args, "weekly", False):
            wprofile = carbon_curve.build_weekday_profile(first)
            py_day, _ = carbon_curve.cleanest_weekday(wprofile)
            if py_day is not None:
                dow = (py_day + 1) % 7  # python Mon=0..Sun=6 -> cron Sun=0..Sat=6
                day_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][py_day]
        return _emit_cron(
            args,
            first,
            hour,
            intensity,
            "history",
            note=note,
            savings_g=savings,
            dow=dow,
            day_name=day_name,
        )

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
        print("  Shift your recurring job to this time: it saves on every run, no idle wait.")
    return EXIT_GREEN


def cmd_audit(args):
    """Scan a repo's workflow files and report every schedule worth shifting.

    One prioritized list across the whole repo, instead of running suggest-cron
    per workflow: for each simple daily cron, the cleanest hour and the estimated
    saving, sorted by impact, with a repo-wide annual total. Exit 0 if anything is
    actionable, 1 if all schedules are already optimal, 2 if no curve is available.
    """
    import glob

    import carbon_curve
    import suggest_pr

    zone = (check_grid.parse_zones_input(args.zones) or [{"zone": args.zones}])[0]["zone"]
    with contextlib.redirect_stdout(sys.stderr):
        profile = carbon_curve.build_profile(zone)
    if not profile:
        if args.json:
            print(json.dumps({"status": "no_curve", "zone": zone}))
        else:
            print(f"Can't audit {zone}: no hour-of-day curve available")
        return EXIT_NODATA

    clean_hour, _ = carbon_curve.cleanest_hour(profile)
    energy = _energy_kwh(args)
    files = sorted(set(glob.glob(f"{args.dir}/*.yml") + glob.glob(f"{args.dir}/*.yaml")))
    findings = []
    for path in files:
        try:
            with open(path) as fh:
                text = fh.read()
        except OSError:
            continue
        for match in suggest_pr.CRON_RE.finditer(text):
            cron = match.group(1)
            fields = cron.split()
            if len(fields) != 5 or not fields[1].isdigit():
                continue  # only simple daily crons can be safely shifted
            cur_hour = int(fields[1])
            if cur_hour == clean_hour:
                continue  # already optimal
            sav = carbon_curve.shift_savings_grams(profile, cur_hour, clean_hour, energy)
            findings.append(
                {
                    "file": path,
                    "current_cron": cron,
                    "suggested_cron": suggest_pr.swap_cron_hour(cron, clean_hour),
                    "savings_g_per_run": sav,
                    "savings_kg_per_year": round(sav * 365 / 1000, 1),
                }
            )

    findings.sort(key=lambda f: f["savings_g_per_run"], reverse=True)
    total_annual = round(sum(f["savings_kg_per_year"] for f in findings), 1)
    if args.json:
        print(
            json.dumps(
                {
                    "status": "ok" if findings else "all_optimal",
                    "zone": zone,
                    "cleanest_hour": clean_hour,
                    "total_savings_kg_per_year": total_annual,
                    "findings": findings,
                }
            )
        )
    elif not findings:
        print(f"All scheduled workflows in {args.dir} already run near the cleanest hour.")
    else:
        print(f"Carbon audit of {args.dir} (cleanest hour {clean_hour:02d}:00 UTC for {zone}):")
        for f in findings:
            print(
                f"  {f['file']}: `{f['current_cron']}` -> `{f['suggested_cron']}`  "
                f"(~{f['savings_kg_per_year']:.1f} kg/yr)"
            )
        print(f"Total potential: ~{total_annual:.1f} kg CO2/yr. Apply with `mode: suggest`.")
    return EXIT_GREEN if findings else EXIT_DIRTY


def cmd_schedule_cost(args):
    """Rank a repo's scheduled workflows by estimated annual emissions.

    Using less compute beats shifting it. This estimates each schedule's CO2/yr
    from how often it fires x per-run emissions, so the jobs worth running less
    often (or adding concurrency cancellation to) stand out. Exit 0 always.
    """
    import glob

    import carbon_curve
    import suggest_pr

    zone = (check_grid.parse_zones_input(args.zones) or [{"zone": args.zones}])[0]["zone"]
    with contextlib.redirect_stdout(sys.stderr):
        profile = carbon_curve.build_profile(zone)
    intensity = carbon_curve.mean_intensity(profile) if profile else None
    if not intensity:
        measured = _measure_all(args)
        match = [m for m in measured if m[0] == zone] or measured
        intensity = match[0][1] if match else None
    if not intensity:
        print(
            json.dumps({"status": "error", "reason": "no data"})
            if args.json
            else "NO DATA: could not read the zone"
        )
        return EXIT_NODATA

    _energy_kwh(args)  # sets JOB_ENERGY_KWH from --energy-kwh for estimate_emissions
    per_run = check_grid.estimate_emissions(intensity)
    files = sorted(set(glob.glob(f"{args.dir}/*.yml") + glob.glob(f"{args.dir}/*.yaml")))
    items = []
    for path in files:
        try:
            with open(path) as fh:
                text = fh.read()
        except OSError:
            continue
        for match in suggest_pr.CRON_RE.finditer(text):
            cron = match.group(1)
            rpd = suggest_pr.runs_per_day(cron)
            if rpd <= 0:
                continue
            annual_kg = round(rpd * 365 * per_run / 1000, 1)
            items.append({"file": path, "cron": cron, "runs_per_day": rpd, "annual_kg": annual_kg})

    items.sort(key=lambda i: i["annual_kg"], reverse=True)
    total = round(sum(i["annual_kg"] for i in items), 1)
    if args.json:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "zone": zone,
                    "per_run_grams": per_run,
                    "total_annual_kg": total,
                    "schedules": items,
                }
            )
        )
    elif not items:
        print(f"No scheduled workflows found in {args.dir}.")
    else:
        print(f"Scheduled-workflow emissions for {args.dir} (~{per_run:.0f} g/run in {zone}):")
        for i in items:
            print(
                f"  {i['file']}: `{i['cron']}`  {i['runs_per_day']:g}x/day  "
                f"~{i['annual_kg']:.1f} kg/yr"
            )
        print(
            f"Total: ~{total:.1f} kg CO2/yr. Throttle the heaviest or add "
            "concurrency cancel-in-progress."
        )
    return EXIT_GREEN


def cmd_advise(args):
    """One prioritized carbon action plan for a repo, across every lever.

    Combines worth-it, the schedule audit, frequency cost, and the posture grade
    into a single ranked list of concrete actions with kg/yr each, so a user
    runs one command and knows exactly what to do, instead of choosing among a
    dozen. Exit 0 with actions, 1 if nothing worth doing, 2 if no curve.
    """
    import glob

    import carbon_curve
    import suggest_pr

    zone = (check_grid.parse_zones_input(args.zones) or [{"zone": args.zones}])[0]["zone"]
    with contextlib.redirect_stdout(sys.stderr):
        profile = carbon_curve.build_profile(zone)
    if not profile:
        print(
            json.dumps({"status": "no_curve", "zone": zone})
            if args.json
            else f"Can't advise on {zone}: no hour-of-day curve available"
        )
        return EXIT_NODATA

    worth = carbon_curve.is_worth_shifting(profile)
    spread = carbon_curve.spread_pct(profile)
    clean_hour, clean_int = carbon_curve.cleanest_hour(profile)
    mean = carbon_curve.mean_intensity(profile)
    _energy_kwh(args)
    per_run_clean = check_grid.estimate_emissions(clean_int)

    actions = []
    heaviest = None
    total_current = total_avoidable = 0.0
    for path in sorted(set(glob.glob(f"{args.dir}/*.yml") + glob.glob(f"{args.dir}/*.yaml"))):
        try:
            text = open(path).read()
        except OSError:
            continue
        for match in suggest_pr.CRON_RE.finditer(text):
            cron = match.group(1)
            rpd = suggest_pr.runs_per_day(cron)
            if rpd <= 0:
                continue
            fields = cron.split()
            cur_hour = int(fields[1]) if len(fields) == 5 and fields[1].isdigit() else None
            cur_int = profile.get(cur_hour, mean) if cur_hour is not None else mean
            runs_year = rpd * 365
            cur_kg = round(check_grid.estimate_emissions(cur_int) * runs_year / 1000, 1)
            total_current += cur_kg
            if heaviest is None or cur_kg > heaviest["annual_kg"]:
                heaviest = {"file": path, "cron": cron, "annual_kg": cur_kg, "runs_per_day": rpd}
            if worth and cur_hour is not None:
                save = round(
                    max(0.0, (check_grid.estimate_emissions(cur_int) - per_run_clean))
                    * runs_year
                    / 1000,
                    1,
                )
                if save > 0:
                    new_cron = suggest_pr.swap_cron_hour(cron, clean_hour)
                    actions.append(
                        {
                            "type": "shift",
                            "file": path,
                            "detail": f"`{cron}` to `{new_cron}`",
                            "annual_kg": save,
                        }
                    )
                    total_avoidable += save

    # The heaviest job is a use-less candidate even when shifting won't help
    if heaviest and heaviest["runs_per_day"] >= 12 and heaviest["annual_kg"] > 0:
        actions.append(
            {
                "type": "throttle",
                "file": heaviest["file"],
                "detail": f"`{heaviest['cron']}` runs {heaviest['runs_per_day']:g}x/day "
                ": run less often or add concurrency cancel-in-progress",
                "annual_kg": heaviest["annual_kg"],
            }
        )
    actions.sort(key=lambda a: a["annual_kg"], reverse=True)
    captured = 1.0 if total_current <= 0 else max(0.0, 1 - total_avoidable / total_current)
    grade = _grade(captured)[0]

    if args.json:
        print(
            json.dumps(
                {
                    "status": "ok" if actions else "nothing",
                    "zone": zone,
                    "grade": grade,
                    "worth_shifting": worth,
                    "spread_pct": spread,
                    "total_avoidable_kg_per_year": round(total_avoidable, 1),
                    "actions": actions,
                }
            )
        )
        return EXIT_GREEN if actions else EXIT_DIRTY
    print(f"Carbon plan for {zone} (posture {grade}, grid spread {spread:.0f}%):")
    if not worth:
        print("  Grid is fairly flat here; time-shifting saves little. Focus on running less:")
    if not actions:
        print("  Nothing actionable: schedules already optimal or grid too flat.")
        return EXIT_DIRTY
    for i, a in enumerate(actions, 1):
        print(f"  {i}. [{a['type']}] {a['file']}: {a['detail']}  (~{a['annual_kg']:.1f} kg/yr)")
    print(
        f"Total avoidable by shifting: ~{total_avoidable:.1f} kg CO2/yr. "
        "Apply shifts with `mode: suggest`."
    )
    return EXIT_GREEN


def _grade(captured):
    """Letter grade + shields color from the captured-savings fraction."""
    for threshold, grade, color in (
        (0.95, "A", "brightgreen"),
        (0.80, "B", "green"),
        (0.60, "C", "yellow"),
        (0.40, "D", "orange"),
    ):
        if captured >= threshold:
            return grade, color
    return "F", "red"


def cmd_score(args):
    """Grade a repo's scheduling carbon posture (A-F) and emit a shareable badge.

    The grade is the share of schedulable emissions already captured
    (1 - avoidable/current), so it rewards shifting jobs to clean hours and stays
    honest: an all-flat-or-optimal repo scores A, one leaving big savings unclaimed
    scores low. Writes a shields.io badge JSON with --badge-file.
    """
    import glob

    import carbon_curve
    import suggest_pr

    zone = (check_grid.parse_zones_input(args.zones) or [{"zone": args.zones}])[0]["zone"]
    with contextlib.redirect_stdout(sys.stderr):
        profile = carbon_curve.build_profile(zone)
    if not profile:
        print(
            json.dumps({"status": "no_curve", "zone": zone})
            if args.json
            else f"Can't score {zone}: no hour-of-day curve available"
        )
        return EXIT_NODATA

    clean_hour, clean_int = carbon_curve.cleanest_hour(profile)
    mean = carbon_curve.mean_intensity(profile)
    _energy_kwh(args)
    per_run_clean = check_grid.estimate_emissions(clean_int)
    files = sorted(set(glob.glob(f"{args.dir}/*.yml") + glob.glob(f"{args.dir}/*.yaml")))
    current_kg = avoidable_kg = 0.0
    schedules = 0
    for path in files:
        try:
            with open(path) as fh:
                text = fh.read()
        except OSError:
            continue
        for match in suggest_pr.CRON_RE.finditer(text):
            cron = match.group(1)
            rpd = suggest_pr.runs_per_day(cron)
            if rpd <= 0:
                continue
            schedules += 1
            fields = cron.split()
            cur_hour = int(fields[1]) if len(fields) == 5 and fields[1].isdigit() else None
            cur_int = profile.get(cur_hour, mean) if cur_hour is not None else mean
            runs_year = rpd * 365
            per_run_cur = check_grid.estimate_emissions(cur_int)
            current_kg += per_run_cur * runs_year / 1000
            if cur_hour is not None:
                avoidable_kg += max(0.0, per_run_cur - per_run_clean) * runs_year / 1000

    captured = 1.0 if current_kg <= 0 else max(0.0, 1 - avoidable_kg / current_kg)
    grade, color = _grade(captured)
    pct = round(captured * 100, 1)
    current_kg, avoidable_kg = round(current_kg, 1), round(avoidable_kg, 1)

    if args.badge_file:
        try:
            with open(args.badge_file, "w") as fh:
                json.dump(
                    {
                        "schemaVersion": 1,
                        "label": "carbon posture",
                        "message": f"{grade} ({pct:.0f}%)",
                        "color": color,
                    },
                    fh,
                )
        except OSError as exc:
            print(f"::warning::could not write badge file: {exc}", file=sys.stderr)

    if args.json:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "zone": zone,
                    "grade": grade,
                    "captured_pct": pct,
                    "schedules": schedules,
                    "current_kg_per_year": current_kg,
                    "avoidable_kg_per_year": avoidable_kg,
                }
            )
        )
    elif schedules == 0:
        print(f"Carbon posture: {grade}; no scheduled workflows to optimize in {args.dir}.")
    else:
        print(f"Carbon posture: {grade} ({pct:.0f}% of schedulable savings captured)")
        print(
            f"  {schedules} schedule(s), ~{current_kg:.1f} kg/yr; "
            f"~{avoidable_kg:.1f} kg/yr still avoidable by shifting."
        )
    return EXIT_GREEN


def cmd_plan(args):
    """Combined when+where: the cleanest (region, hour) across candidate zones.

    Picks the single best action across both levers (which region and what hour
    minimize intensity) using each zone's hour-of-day curve, and quantifies the
    saving vs running in your current zone at a typical hour. Falls back to a
    region-only recommendation when no curves are available.
    """
    import carbon_curve

    zones = [z["zone"] for z in check_grid.parse_zones_input(args.zones)]
    energy = _energy_kwh(args)
    duration = int(getattr(args, "duration_hours", 1) or 1)
    options = []  # (zone, hour, intensity, mean)
    with contextlib.redirect_stdout(sys.stderr):
        for z in zones:
            profile = carbon_curve.build_profile(z)
            if not profile:
                continue
            if duration > 1:
                start, val = carbon_curve.cleanest_window(profile, duration)
                hour, intensity = (
                    (start, val) if start is not None else carbon_curve.cleanest_hour(profile)
                )
            else:
                hour, intensity = carbon_curve.cleanest_hour(profile)
            options.append((z, hour, intensity, carbon_curve.mean_intensity(profile)))

    if not options:
        if not args.json:
            print("No hour-of-day curves available; falling back to region only:", file=sys.stderr)
        return cmd_suggest_region(args)

    best = min(options, key=lambda o: o[2])
    current = [o for o in options if o[0] == args.current] if args.current else []
    if current:
        baseline_mean, baseline_label = current[0][3], args.current
    else:
        worst = max(options, key=lambda o: o[3])
        baseline_mean, baseline_label = worst[3], f"{worst[0]} (typical)"
    per_run = round(max(0.0, (baseline_mean - best[2]) * energy), 1)
    annual_kg = round(per_run * 365 / 1000, 1)
    cron = f"0 {best[1]} * * *"

    if args.json:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "zone": best[0],
                    "hour": best[1],
                    "cron": cron,
                    "intensity": best[2],
                    "baseline": baseline_label,
                    "savings_g_per_run": per_run,
                    "savings_kg_per_year": annual_kg,
                    "duration_hours": duration,
                }
            )
        )
    else:
        what = f"a {duration}h job" if duration > 1 else "your job"
        print(f"Run {what} in {best[0]} at {best[1]:02d}:00 UTC  (cron: {cron})")
        print(
            f"  {best[2]:.0f} gCO2eq/kWh vs {baseline_label} ~{baseline_mean:.0f}: "
            f"~{per_run:.0f} g/run (~{annual_kg:.1f} kg/yr daily)."
        )
        print("  Combines a region move (mind latency/egress) and a schedule shift.")
    return EXIT_GREEN


def cmd_sla(args):
    """Report Green SLA compliance from the ledger: the share of runs that ran clean.

    Commit to a target (e.g. 95% of runs on a clean grid) and prove it over a
    window, with an attestation. Exit 0 compliant/warning, 1 breached, 2 unknown.
    """
    import os

    import ledger

    backend, location = ledger.parse_config(os.environ.get("LEDGER", ""))
    if not backend or not location:
        print("Green SLA needs the ledger (set LEDGER=gist:<id> or file:<path>)", file=sys.stderr)
        return EXIT_NODATA
    if backend == "file":
        data = ledger._load_file(location)
    else:
        data, _ = ledger._gist_read(location, os.environ.get("GIST_TOKEN", ""))

    from datetime import datetime, timezone

    if args.window == "month":
        prefix = datetime.now(timezone.utc).strftime("%Y-%m")
    else:
        prefix = ""  # lifetime
    green, total = ledger.sla_window(data, prefix)
    if total < 5:
        print(
            json.dumps({"status": "unknown", "green": green, "total": total})
            if args.json
            else f"SLA: not enough data yet ({total} runs)"
        )
        return EXIT_NODATA

    compliance = round(green / total * 100, 1)
    target = args.target
    if compliance < target:
        status = "breached"
    elif compliance < target + 5:
        status = "warning"
    else:
        status = "compliant"
    if args.json:
        print(
            json.dumps(
                {
                    "status": status,
                    "compliance_pct": compliance,
                    "target": target,
                    "green_runs": green,
                    "total_runs": total,
                    "window": args.window,
                }
            )
        )
    else:
        print(
            f"Green SLA: {status}, {compliance:.0f}% of {total} runs clean this {args.window} "
            f"(target {target:.0f}%, {green}/{total} green)"
        )
    return EXIT_GREEN if status != "breached" else EXIT_DIRTY


def cmd_export_curves(args):
    """Export the ledger's accumulated hour-of-day curves to share (data commons).

    Writes a curve file (the per-zone hour aggregates) that can be pooled across
    users and fed back via COMMUNITY_CURVE, so zones nobody has a free historical
    API for still gain a diurnal profile as adoption grows. Exit 0 on export,
    2 when there's no ledger or no curve yet.
    """
    import os

    import ledger

    backend, location = ledger.parse_config(os.environ.get("LEDGER", ""))
    if not backend or not location:
        print("export-curves needs the ledger (LEDGER=gist:<id> or file:<path>)", file=sys.stderr)
        return EXIT_NODATA
    if backend == "file":
        data = ledger._load_file(location)
    else:
        data, _ = ledger._gist_read(location, os.environ.get("GIST_TOKEN", ""))

    curve = data.get("curve") or {}
    if not curve:
        print("No accumulated curve to export yet", file=sys.stderr)
        return EXIT_NODATA
    payload = json.dumps({"curve": curve}, indent=2)
    if args.output:
        with open(args.output, "w") as fh:
            fh.write(payload)
        print(f"Exported curves for {len(curve)} zone(s) to {args.output}", file=sys.stderr)
    else:
        print(payload)
    return EXIT_GREEN


def cmd_merge_curves(args):
    """Pool many exported curve files into one shared community curve.

    Reads each path (a file from `export-curves`), sums their per-hour sum/count
    so the merged mean is volume-weighted, and writes the pooled curve. This is
    the server side of the data commons: run it over contributors' files to
    publish one COMMUNITY_CURVE the whole community can point at. Exit 0 on
    merge, 2 when no readable input files were given.
    """
    import ledger

    docs = []
    for path in args.paths:
        try:
            with open(path) as fh:
                docs.append(json.load(fh))
        except (OSError, ValueError) as exc:
            print(f"skipping {path}: {exc}", file=sys.stderr)
    if not docs:
        print("merge-curves needs at least one readable curve file", file=sys.stderr)
        return EXIT_NODATA
    merged = ledger.merge_curves(docs, cap_n=args.cap_n or None)
    zones = len(merged.get("curve") or {})
    payload = json.dumps(merged, indent=2)
    if args.output:
        with open(args.output, "w") as fh:
            fh.write(payload)
        print(f"Merged {len(docs)} file(s) into {zones} zone(s) at {args.output}", file=sys.stderr)
    else:
        print(payload)
    return EXIT_GREEN


def cmd_validate_curves(args):
    """Validate contributed curve files before they enter the community pool.

    Checks each file's structure, hour ranges, counts/sums, plausible intensities,
    and that it is a real (non-sparse) contribution. Exit 0 when all pass, 1 when
    any file is unreadable or invalid. Wire this into a PR check on the
    community-curves directory so bad data never reaches the merged pool.
    """
    import ledger

    bad = 0
    for path in args.paths:
        try:
            with open(path) as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"{path}: unreadable ({exc})", file=sys.stderr)
            bad += 1
            continue
        problems = ledger.validate_curve_doc(
            doc, max_intensity=args.max_intensity, min_hours=args.min_hours
        )
        if problems:
            bad += 1
            for problem in problems:
                print(f"{path}: {problem}", file=sys.stderr)
        else:
            print(f"{path}: ok", file=sys.stderr)
    if bad:
        print(f"{bad} file(s) failed validation", file=sys.stderr)
        return EXIT_DIRTY
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


def cmd_worth_it(args):
    """Say whether carbon-aware scheduling is worth it for this zone.

    A flat, baseload-dominated grid barely varies by hour, so shifting saves
    little; skip the complexity. Exit 0 = worth it, 1 = not worth it,
    2 = can't assess (no free historical curve).
    """
    import carbon_curve

    zones = check_grid.parse_zones_input(args.zones)
    first = zones[0]["zone"] if zones else args.zones
    with contextlib.redirect_stdout(sys.stderr):
        profile = carbon_curve.build_profile(first)
    if not profile:
        if args.json:
            print(json.dumps({"status": "unknown", "zone": first}))
        else:
            print(f"Can't assess {first}: no free historical curve (GB has the richest history)")
        return EXIT_NODATA

    spread = carbon_curve.spread_pct(profile)
    hour, intensity = carbon_curve.cleanest_hour(profile)
    worth = spread >= args.min_spread
    best = carbon_curve.best_case_savings_grams(profile, _energy_kwh(args))
    annual_kg = best * 365 / 1000
    if args.json:
        print(
            json.dumps(
                {
                    "status": "worth" if worth else "not_worth",
                    "zone": first,
                    "spread_pct": spread,
                    "min_spread": args.min_spread,
                    "cleanest_hour": hour,
                    "best_case_savings_g_per_run": best,
                    "best_case_savings_kg_per_year": round(annual_kg, 2),
                }
            )
        )
    elif worth:
        print(
            f"Worth shifting: {first} varies {spread:.0f}% across the day "
            f"(cleanest {hour:02d}:00 UTC). Up to ~{best:.0f} g/run "
            f"(~{annual_kg:.1f} kg/yr daily). Use `suggest-cron`."
        )
    else:
        print(
            f"Not worth shifting: {first} is fairly flat ({spread:.0f}% spread, "
            f"~{best:.0f} g/run best case). Scheduling saves little; skip the complexity."
        )
    return EXIT_GREEN if worth else EXIT_DIRTY


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
    import ledger

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
    s.add_argument("--energy-kwh", type=float, help="Run energy (kWh) for the savings estimate")
    s.add_argument(
        "--duration-hours",
        type=int,
        default=1,
        help="Job length in hours; >1 targets the cleanest contiguous window",
    )
    s.add_argument(
        "--weekly", action="store_true", help="For weekly jobs: also pick the cleanest day of week"
    )
    s.set_defaults(func=cmd_suggest_cron)

    sr = sub.add_parser("suggest-region", help="Recommend the cleanest region among candidates")
    add_common(sr)
    sr.add_argument("--current", default="", help="Your current zone, to quantify the saving vs it")
    sr.add_argument("--energy-kwh", type=float, help="Run energy (kWh) for the savings estimate")
    sr.set_defaults(func=cmd_suggest_region)

    pl = sub.add_parser("plan", help="Combined when+where: cleanest (region, hour) across zones")
    add_common(pl)
    pl.add_argument("--current", default="", help="Your current zone, to quantify the saving vs it")
    pl.add_argument("--energy-kwh", type=float, help="Run energy (kWh) for the savings estimate")
    pl.add_argument("--duration-hours", type=int, default=1, help="Job length in hours")
    pl.set_defaults(func=cmd_plan)

    au = sub.add_parser("audit", help="Scan a repo's workflows for schedules worth shifting")
    add_common(au)
    au.add_argument("--dir", default=".github/workflows", help="Workflows directory to scan")
    au.add_argument("--energy-kwh", type=float, help="Run energy (kWh) for the savings estimate")
    au.set_defaults(func=cmd_audit)

    scost = sub.add_parser("schedule-cost", help="Rank scheduled workflows by annual emissions")
    add_common(scost)
    scost.add_argument("--dir", default=".github/workflows", help="Workflows directory to scan")
    scost.add_argument("--energy-kwh", type=float, help="Per-run energy (kWh)")
    scost.set_defaults(func=cmd_schedule_cost)

    sc = sub.add_parser("score", help="Grade a repo's scheduling carbon posture (A-F) + badge")
    add_common(sc)
    sc.add_argument("--dir", default=".github/workflows", help="Workflows directory to scan")
    sc.add_argument("--energy-kwh", type=float, help="Per-run energy (kWh)")
    sc.add_argument("--badge-file", default="", help="Write a shields.io badge JSON to this path")
    sc.set_defaults(func=cmd_score)

    ad = sub.add_parser("advise", help="One prioritized carbon action plan for the repo")
    add_common(ad)
    ad.add_argument("--dir", default=".github/workflows", help="Workflows directory to scan")
    ad.add_argument("--energy-kwh", type=float, help="Per-run energy (kWh)")
    ad.set_defaults(func=cmd_advise)

    mg = sub.add_parser(
        "marginal", help="WattTime marginal-emissions signal (real avoided-emissions metric)"
    )
    add_common(mg)
    mg.add_argument("--region", default="CAISO_NORTH", help="WattTime region. Default: CAISO_NORTH")
    mg.add_argument(
        "--max-percentile",
        type=float,
        default=33.0,
        help="Clean when at/below this co2_moer percentile. Default: 33",
    )
    mg.add_argument("--username", default="", help="WattTime username (or WATTTIME_USERNAME)")
    mg.add_argument("--password", default="", help="WattTime password (or WATTTIME_PASSWORD)")
    mg.set_defaults(func=cmd_marginal)

    sla = sub.add_parser("sla", help="Report Green SLA compliance from the ledger")
    add_common(sla)
    sla.add_argument(
        "--target",
        type=float,
        default=95.0,
        help="Percent of runs that must run clean. Default: 95",
    )
    sla.add_argument(
        "--window",
        choices=["month", "lifetime"],
        default="month",
        help="Compliance window. Default: month",
    )
    sla.set_defaults(func=cmd_sla)

    cv = sub.add_parser("curve", help="Print the hour-of-day carbon curve (historical)")
    add_common(cv)
    cv.set_defaults(func=cmd_curve)

    ec = sub.add_parser("export-curves", help="Export accumulated curves to share (data commons)")
    add_common(ec)
    ec.add_argument("--output", default="", help="Write to this file instead of stdout")
    ec.set_defaults(func=cmd_export_curves)

    mc = sub.add_parser("merge-curves", help="Pool exported curve files into one community curve")
    mc.add_argument("paths", nargs="+", help="Curve files to merge (from export-curves)")
    mc.add_argument("--output", default="", help="Write to this file instead of stdout")
    mc.add_argument(
        "--cap-n",
        type=int,
        default=0,
        help="Cap each file's per-hour sample weight (0 = no cap) to limit skew",
    )
    mc.set_defaults(func=cmd_merge_curves)

    vc = sub.add_parser("validate-curves", help="Validate contributed curve files for the pool")
    vc.add_argument("paths", nargs="+", help="Curve files to validate (from export-curves)")
    vc.add_argument(
        "--max-intensity",
        type=float,
        default=ledger.MAX_PLAUSIBLE_INTENSITY,
        help="Reject per-hour means above this (gCO2/kWh)",
    )
    vc.add_argument(
        "--min-hours",
        type=int,
        default=6,
        help="Require a zone with at least this many sampled hours (0 to disable)",
    )
    vc.set_defaults(func=cmd_validate_curves)

    wi = sub.add_parser("worth-it", help="Is carbon-aware scheduling worth it for this zone?")
    add_common(wi)
    wi.add_argument(
        "--min-spread",
        type=float,
        default=15.0,
        help="Min hour-of-day spread %% to call shifting worthwhile. Default: 15",
    )
    wi.add_argument("--energy-kwh", type=float, help="Run energy (kWh) for the savings estimate")
    wi.set_defaults(func=cmd_worth_it)

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
