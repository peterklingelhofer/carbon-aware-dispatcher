"""Compute the numbers in docs/VALIDATION.md from the committed snapshots.

Reads data/validation/*.csv, runs the analyses, writes data/validation/results.json,
then substitutes the generated tables into docs/VALIDATION.md between
`<!-- generated:NAME -->` and `<!-- /generated:NAME -->` markers so the prose is
hand-written and the numbers never drift from the data.

The analyses import this repo's own shipped functions (carbon_curve.profile_from_samples,
marginal.estimate_marginal, providers.eia._fuel_mix_totals, providers.base.FUEL_FACTORS)
rather than reimplementing them: the numbers grade the code that ships. A
parallel copy could drift from it.
"""

import argparse
import csv
import json
import os
import re
import statistics
import sys
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import carbon_curve  # noqa: E402
import marginal  # noqa: E402
from providers import base  # noqa: E402
from providers.eia import _fuel_mix_totals  # noqa: E402

DATA_DIR = os.path.join(ROOT, "data", "validation")
RESULTS = os.path.join(DATA_DIR, "results.json")
DOC = os.path.join(ROOT, "docs", "VALIDATION.md")

# The reference CI job the action assumes, read from the shipped constants so this
# never drifts from what the tool actually reports. Every gram figure below is per
# job of that size, so the savings scale linearly with a real workload.
JOB_KWH = base.CI_JOB_POWER_KW * 0.25
# self-track.yml, this repo's own dogfooding workflow, runs at this threshold.
GB_THRESHOLD = 200
# Deferral budgets to report. The action caps max_wait at 360 min; the longer
# horizons stand in for "defer to a later scheduled run" rather than blocking.
WAIT_HOURS = (3, 6, 12, 24)
# Days of GB history reserved for training the diurnal profile. The remainder is
# the held-out evaluation window, so climatology is never scored on its own data.
TRAIN_DAYS = 30
HORIZONS_H = (0.5, 1, 2, 3, 6, 12, 24, 48)
# Trailing windows the marginal regression is run over. The tool passes whatever
# history a provider hands it; both ends of the plausible range are reported
# because the choice changes the answer.
MARGINAL_WINDOWS_H = (6, 24)


def _parse_utc(text, fmt):
    return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)


def load_gb():
    """GB national half-hourly rows as [(dt, forecast, actual)], oldest first."""
    path = os.path.join(DATA_DIR, "gb-national-intensity.csv")
    rows = []
    with open(path) as fh:
        for row in csv.DictReader(fh):
            if not row["actual"] or not row["forecast"]:
                continue
            rows.append(
                (
                    _parse_utc(row["from"], "%Y-%m-%dT%H:%MZ"),
                    float(row["forecast"]),
                    float(row["actual"]),
                )
            )
    rows.sort()
    return rows


def load_eia(zone):
    """EIA hourly rows as [(dt, generation_mwh, weighted_gco2)], oldest first."""
    path = os.path.join(DATA_DIR, f"eia-{zone.lower()}-fuel-mix.csv")
    periods = OrderedDict()
    with open(path) as fh:
        for row in csv.DictReader(fh):
            periods.setdefault(row["period"], []).append(
                {"fueltype": row["fueltype"], "value": row["mwh"]}
            )
    series = []
    for period, fuel_rows in periods.items():
        generation, weighted = _fuel_mix_totals(fuel_rows)
        if generation > 0:
            series.append((_parse_utc(period, "%Y-%m-%dT%H"), generation, weighted))
    series.sort()
    return series


def _error_stats(pairs):
    """MAE / bias / RMSE / p90 absolute error over [(predicted, actual)]."""
    errs = [p - a for p, a in pairs]
    n = len(errs)
    if n == 0:
        return {"n": 0}
    absolute = sorted(abs(e) for e in errs)
    return {
        "n": n,
        "mae": round(sum(absolute) / n, 1),
        "bias": round(sum(errs) / n, 1),
        "rmse": round((sum(e * e for e in errs) / n) ** 0.5, 1),
        "p90_abs": round(absolute[int(0.9 * (n - 1))], 1),
    }


def analysis_nowcast(gb):
    """How wrong is the number the GB badge shows?

    providers/uk.py reads intensity.forecast for the CURRENT half-hour, because
    intensity.actual is still null when the period is live. Once the period
    settles the actual arrives, so the error in the displayed number is
    measurable after the fact.
    """
    stats = _error_stats([(f, a) for _, f, a in gb])
    flips = {}
    for threshold in (100, 150, 200, 250):
        wrong = sum(1 for _, f, a in gb if (f <= threshold) != (a <= threshold))
        false_green = sum(1 for _, f, a in gb if f <= threshold and a > threshold)
        flips[threshold] = {
            "n": len(gb),
            "verdict_flips": wrong,
            "flip_pct": round(100.0 * wrong / len(gb), 2),
            "false_green": false_green,
            "false_green_pct": round(100.0 * false_green / len(gb), 2),
        }
    stats["threshold_flips"] = {str(k): v for k, v in flips.items()}
    stats["window"] = {"from": gb[0][0].isoformat(), "to": gb[-1][0].isoformat()}
    return stats


def analysis_horizon(gb):
    """Forecast skill at horizon h against persistence and climatology.

    Persistence is the honest floor: if a forecast cannot beat "assume it stays
    where it is", it is not adding information. Climatology is the second floor:
    the hour-of-day mean the project already computes in carbon_curve.
    """
    split = gb[0][0] + timedelta(days=TRAIN_DAYS)
    train = [(dt, f, a) for dt, f, a in gb if dt < split]
    evaluate = [(dt, f, a) for dt, f, a in gb if dt >= split]
    # The shipped diurnal-profile builder, fed only training-window actuals
    profile = carbon_curve.profile_from_samples([(dt.hour, a) for dt, _, a in train])
    by_time = {dt: (f, a) for dt, f, a in gb}
    eval_from = evaluate[0][0]

    rows = []
    for hours in HORIZONS_H:
        delta = timedelta(hours=hours)
        persistence, climatology, published = [], [], []
        for dt, _, actual_now in evaluate:
            target = by_time.get(dt + delta)
            if target is None:
                continue
            forecast_target, actual_target = target
            persistence.append((actual_now, actual_target))
            climatology.append((profile[(dt + delta).hour], actual_target))
            published.append((forecast_target, actual_target))
        base = _error_stats(persistence)
        row = {
            "horizon_h": hours,
            "persistence": base,
            "climatology": _error_stats(climatology),
            "published_forecast": _error_stats(published),
        }
        for name in ("climatology", "published_forecast"):
            mae = row[name].get("mae")
            row[name]["skill_vs_persistence"] = (
                round(1 - mae / base["mae"], 3) if mae is not None and base.get("mae") else None
            )
        rows.append(row)
    return {
        "train_days": TRAIN_DAYS,
        "train_from": train[0][0].isoformat(),
        "eval_from": eval_from.isoformat(),
        "eval_to": evaluate[-1][0].isoformat(),
        "profile": profile,
        "rows": rows,
    }


def analysis_deferral(gb):
    """Replay the defer-to-a-green-window policy over real GB half-hours.

    At each decision hour the tool sees forecast(t) as "now" (see analysis_nowcast).
    If that is above the threshold it looks forward for the first period the
    forecast calls green and defers there. The counterfactual is then settled with
    ACTUALS: what the grid really was at the original time versus at the time the
    job really ran. Runs where the shift made things worse are counted in the totals.
    """
    by_time = {dt: (f, a) for dt, f, a in gb}
    split = gb[0][0] + timedelta(days=TRAIN_DAYS)
    decisions = [dt for dt, _, _ in gb if dt >= split and dt.minute == 0]
    by_wait = {}
    for wait_h in WAIT_HOURS:
        deltas, claimed, realized = [], [], []
        ran_immediately = no_window = 0
        for dt in decisions:
            forecast_now, actual_now = by_time[dt]
            if forecast_now <= GB_THRESHOLD:
                ran_immediately += 1
                continue
            target = None
            for step in range(1, wait_h * 2 + 1):
                candidate = dt + timedelta(minutes=30 * step)
                entry = by_time.get(candidate)
                if entry is None:
                    break
                if entry[0] <= GB_THRESHOLD:
                    target = candidate
                    break
            if target is None:
                no_window += 1
                continue
            actual_then = by_time[target][1]
            deltas.append(actual_now - actual_then)
            # What the ledger would bank: a benchmark against the global-average
            # baseline, evaluated at the intensity the provider reported
            claimed.append(max(0.0, base.GLOBAL_AVG_INTENSITY - by_time[target][0]) * JOB_KWH)
            realized.append((actual_now - actual_then) * JOB_KWH)
        n = len(deltas)
        by_wait[str(wait_h)] = {
            "decisions": len(decisions),
            "ran_immediately": ran_immediately,
            "no_green_window": no_window,
            "deferred": n,
            "mean_delta_g_per_kwh": round(statistics.mean(deltas), 1) if n else None,
            "median_delta_g_per_kwh": round(statistics.median(deltas), 1) if n else None,
            "worse_count": sum(1 for d in deltas if d < 0),
            "worse_pct": round(100.0 * sum(1 for d in deltas if d < 0) / n, 1) if n else None,
            "p10_delta": round(sorted(deltas)[int(0.1 * (n - 1))], 1) if n else None,
            "p90_delta": round(sorted(deltas)[int(0.9 * (n - 1))], 1) if n else None,
            "realized_saved_g": round(sum(realized), 1),
            "claimed_benchmark_g": round(sum(claimed), 1),
            "claimed_over_realized": (
                round(sum(claimed) / sum(realized), 1) if sum(realized) > 0 else None
            ),
        }
    return {"threshold": GB_THRESHOLD, "job_kwh": JOB_KWH, "by_wait": by_wait}


def _marginal_track(series, window):
    """Rolling (dt, average, marginal, r2) using the shipped estimator."""
    track = []
    for i in range(window, len(series)):
        chunk = series[i - window : i + 1]
        estimate = marginal.estimate_marginal([(g, e) for _, g, e in chunk])
        dt, generation, weighted = series[i]
        track.append(
            (
                dt,
                weighted / generation,
                estimate["marginal"] if estimate else None,
                estimate["r_squared"] if estimate else None,
            )
        )
    return track


def _resolution(track):
    """Can the estimator tell one hour from the next?

    A shift of a few hours can only be priced by a signal that actually moves over
    a few hours. This compares the hour-to-hour movement of the marginal estimate
    against the same movement in the average intensity it is meant to improve on,
    and counts how often the estimate lands on the MARGINAL_CLAMP floor (a
    negative regression slope, which is physically real, reported as zero).
    """
    usable = [row for row in track if row[2] is not None]
    averages = [row[1] for row in usable]
    marginals = [row[2] for row in usable]
    r2s = [row[3] for row in usable if row[3] is not None]
    step_avg = [abs(averages[i] - averages[i - 1]) for i in range(1, len(averages))]
    step_marg = [abs(marginals[i] - marginals[i - 1]) for i in range(1, len(marginals))]
    at_floor = sum(1 for m in marginals if m == marginal.MARGINAL_CLAMP[0])
    return {
        "n": len(usable),
        "median_hourly_step_average": round(statistics.median(step_avg), 1) if step_avg else None,
        "median_hourly_step_marginal": round(statistics.median(step_marg), 1)
        if step_marg
        else None,
        "median_marginal": round(statistics.median(marginals), 1) if marginals else None,
        "median_average": round(statistics.median(averages), 1) if averages else None,
        "median_r2": round(statistics.median(r2s), 3) if r2s else None,
        "at_clamp_floor": at_floor,
        "at_clamp_floor_pct": round(100.0 * at_floor / len(marginals), 1) if marginals else None,
    }


def _shift_events(track, profile_hours):
    """Replay a climatology-driven shift and price it both ways.

    No US provider publishes a forecast, so the decision rule is the diurnal
    profile the project already builds. Threshold is the zone's own median
    intensity: a fixed GB-style 200 would never trigger in PJM.
    """
    usable = [row for row in track if row[2] is not None]
    if not usable:
        return None
    by_time = {dt: (avg, marg, r2) for dt, avg, marg, r2 in usable}
    threshold = statistics.median(avg for _, avg, _, _ in usable)
    profile = carbon_curve.profile_from_samples(
        [(dt.hour, avg) for dt, avg, _, _ in usable[:profile_hours]]
    )
    events = []
    for dt, average_now, marginal_now, r2_now in usable[profile_hours:]:
        if average_now <= threshold:
            continue
        candidates = [
            (profile[(dt + timedelta(hours=step)).hour], dt + timedelta(hours=step))
            for step in range(1, 7)
            if dt + timedelta(hours=step) in by_time
            and profile.get((dt + timedelta(hours=step)).hour) is not None
        ]
        if not candidates:
            continue
        expected, target = min(candidates)
        if expected >= profile.get(dt.hour, expected):
            continue
        average_then, marginal_then, r2_then = by_time[target]
        confidences = [r for r in (r2_now, r2_then) if r is not None]
        events.append(
            (
                average_now - average_then,
                marginal_now - marginal_then,
                min(confidences) if confidences else 0.0,
            )
        )
    if not events:
        return None
    avg_savings = [e[0] for e in events]
    marg_savings = [e[1] for e in events]
    bands = {}
    for label, low, high in (
        ("r2 < 0.3", 0.0, 0.3),
        ("0.3-0.7", 0.3, 0.7),
        ("r2 >= 0.7", 0.7, 1.01),
    ):
        picked = [e for e in events if low <= e[2] < high]
        bands[label] = (
            {"n": 0}
            if not picked
            else {
                "n": len(picked),
                "mean_avg_saving": round(statistics.mean(e[0] for e in picked), 1),
                "mean_marginal_saving": round(statistics.mean(e[1] for e in picked), 1),
                "marginal_negative_pct": round(
                    100.0 * sum(1 for e in picked if e[1] < 0) / len(picked), 1
                ),
            }
        )
    return {
        "median_intensity": round(threshold, 1),
        "shift_events": len(events),
        "mean_avg_saving": round(statistics.mean(avg_savings), 1),
        "mean_marginal_saving": round(statistics.mean(marg_savings), 1),
        "total_avg_saving_g": round(sum(avg_savings) * JOB_KWH, 2),
        "total_marginal_saving_g": round(sum(marg_savings) * JOB_KWH, 2),
        "marginal_over_average": (
            round(sum(marg_savings) / sum(avg_savings), 2) if sum(avg_savings) else None
        ),
        "avg_negative_pct": round(100.0 * sum(1 for a in avg_savings if a < 0) / len(events), 1),
        "marginal_negative_pct": round(
            100.0 * sum(1 for m in marg_savings if m < 0) / len(events), 1
        ),
        "median_r2": round(statistics.median(e[2] for e in events), 3),
        "r2_bands": bands,
    }


def analysis_avg_vs_marginal(zones):
    """Recompute the savings from shifting under average and marginal accounting.

    Shifting a kWh from t to t' changes emissions by marginal(t) - marginal(t');
    the ledger scores it as average(t) - average(t'). Where the two disagree the
    reported saving is wrong. Run at two trailing-window lengths, because the
    window is not a free parameter: a long one is statistically stable but too
    smooth to price a few-hour shift, a short one moves but is noisy.
    """
    out = {"windows_h": list(MARGINAL_WINDOWS_H), "zones": {}}
    for zone in zones:
        series = load_eia(zone)
        zone_out = {
            "hours": len(series),
            "from": series[0][0].isoformat(),
            "to": series[-1][0].isoformat(),
            "by_window": {},
        }
        for window in MARGINAL_WINDOWS_H:
            track = _marginal_track(series, window=window)
            entry = {"resolution": _resolution(track)}
            events = _shift_events(track, profile_hours=len(track) // 2)
            if events:
                entry.update(events)
            zone_out["by_window"][str(window)] = entry
        out["zones"][zone] = zone_out
    return out


def build(zones):
    gb = load_gb()
    return {
        "generated_from": {
            "gb_rows": len(gb),
            "gb_from": gb[0][0].isoformat(),
            "gb_to": gb[-1][0].isoformat(),
        },
        "nowcast": analysis_nowcast(gb),
        "horizon": analysis_horizon(gb),
        "deferral": analysis_deferral(gb),
        "avg_vs_marginal": analysis_avg_vs_marginal(zones),
    }


def _table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(str(c) for c in row) + " |" for row in rows)
    return "\n".join(lines)


def _fmt(value, suffix=""):
    return "-" if value is None else f"{value}{suffix}"


POWER_FILE = os.path.join(DATA_DIR, "power-assumption.json")


def _power_block():
    """The sourced power range, or an honest placeholder until one exists."""
    if not os.path.exists(POWER_FILE):
        return (
            "> Not yet sourced. `CI_JOB_POWER_KW = 0.05` is currently an estimate "
            "from a stated range with no citation behind it."
        )
    with open(POWER_FILE) as fh:
        power = json.load(fh)
    lines = [
        _table(
            ["Basis", "Watts", "Source"],
            [[row["basis"], row["watts"], row["source"]] for row in power["estimates"]],
        ),
        "",
        power["conclusion"],
    ]
    return "\n".join(lines)


def render(results):
    """Markdown fragments keyed by the marker name they are substituted into."""
    now = results["nowcast"]
    blocks = {}

    source = results["generated_from"]
    marginal_zones = results["avg_vs_marginal"]["zones"]
    blocks["window"] = _table(
        ["Series", "Rows", "From (UTC)", "To (UTC)"],
        [["GB national, 30 min", source["gb_rows"], source["gb_from"], source["gb_to"]]]
        + [
            [f"{zone}, hourly fuel mix", z["hours"], z["from"], z["to"]]
            for zone, z in sorted(marginal_zones.items())
        ],
    )
    blocks["power"] = _power_block()

    blocks["nowcast"] = _table(
        ["Metric", "Value"],
        [
            ["Half-hours compared", now["n"]],
            ["MAE", f"{now['mae']} gCO2eq/kWh"],
            ["Bias (forecast - actual)", f"{now['bias']} gCO2eq/kWh"],
            ["RMSE", f"{now['rmse']} gCO2eq/kWh"],
            ["90th percentile absolute error", f"{now['p90_abs']} gCO2eq/kWh"],
        ],
    )
    blocks["nowcast-flips"] = _table(
        ["Threshold", "Verdict flips", "of which false green"],
        [
            [
                f"{t} gCO2eq/kWh",
                f"{v['verdict_flips']} ({v['flip_pct']}%)",
                f"{v['false_green']} ({v['false_green_pct']}%)",
            ]
            for t, v in sorted(now["threshold_flips"].items(), key=lambda kv: int(kv[0]))
        ],
    )

    horizon = results["horizon"]
    blocks["horizon"] = _table(
        [
            "Horizon",
            "Persistence MAE",
            "Climatology MAE",
            "Climatology skill",
            "Published forecast MAE",
            "Published skill",
        ],
        [
            [
                f"{r['horizon_h']} h",
                _fmt(r["persistence"].get("mae")),
                _fmt(r["climatology"].get("mae")),
                _fmt(r["climatology"].get("skill_vs_persistence")),
                _fmt(r["published_forecast"].get("mae")),
                _fmt(r["published_forecast"].get("skill_vs_persistence")),
            ]
            for r in horizon["rows"]
        ],
    )

    deferral = results["deferral"]["by_wait"]
    blocks["deferral"] = _table(
        [
            "Wait budget",
            "Deferrals",
            "Mean realized delta",
            "Median",
            "Made it worse",
            "p10 / p90 delta",
        ],
        [
            [
                f"{h} h",
                deferral[str(h)]["deferred"],
                _fmt(deferral[str(h)]["mean_delta_g_per_kwh"], " g/kWh"),
                _fmt(deferral[str(h)]["median_delta_g_per_kwh"], " g/kWh"),
                f"{deferral[str(h)]['worse_count']} ({_fmt(deferral[str(h)]['worse_pct'], '%')})",
                f"{_fmt(deferral[str(h)]['p10_delta'])} / {_fmt(deferral[str(h)]['p90_delta'])}",
            ]
            for h in WAIT_HOURS
        ],
    )
    blocks["deferral-claim"] = _table(
        ["Wait budget", "Realized saving", "Ledger would bank", "Overstatement"],
        [
            [
                f"{h} h",
                f"{deferral[str(h)]['realized_saved_g']} g",
                f"{deferral[str(h)]['claimed_benchmark_g']} g",
                _fmt(deferral[str(h)]["claimed_over_realized"], "x"),
            ]
            for h in WAIT_HOURS
        ],
    )

    marginal_block = results["avg_vs_marginal"]
    blocks["marginal"] = _table(
        [
            "Zone",
            "Window",
            "Shift events",
            "Mean saving (average)",
            "Mean saving (marginal)",
            "Marginal / average",
            "Marginal saving negative",
        ],
        [
            [
                zone,
                f"{window} h",
                w["shift_events"],
                f"{w['mean_avg_saving']} g/kWh",
                f"{w['mean_marginal_saving']} g/kWh",
                _fmt(w["marginal_over_average"], "x"),
                f"{w['marginal_negative_pct']}%",
            ]
            for zone, z in sorted(marginal_block["zones"].items())
            for window, w in sorted(z["by_window"].items(), key=lambda kv: int(kv[0]))
            if "shift_events" in w
        ],
    )
    blocks["marginal-resolution"] = _table(
        [
            "Zone",
            "Window",
            "Median average",
            "Median marginal",
            "Hourly step, average",
            "Hourly step, marginal",
            "Median r2",
            "At clamp floor",
        ],
        [
            [
                zone,
                f"{window} h",
                _fmt(w["resolution"]["median_average"]),
                _fmt(w["resolution"]["median_marginal"]),
                _fmt(w["resolution"]["median_hourly_step_average"]),
                _fmt(w["resolution"]["median_hourly_step_marginal"]),
                _fmt(w["resolution"]["median_r2"]),
                f"{w['resolution']['at_clamp_floor']} "
                f"({_fmt(w['resolution']['at_clamp_floor_pct'], '%')})",
            ]
            for zone, z in sorted(marginal_block["zones"].items())
            for window, w in sorted(z["by_window"].items(), key=lambda kv: int(kv[0]))
        ],
    )
    blocks["marginal-bands"] = _table(
        ["Zone", "Window", "r2 band", "n", "Mean saving (average)", "Mean saving (marginal)"],
        [
            [
                zone,
                f"{window} h",
                band,
                stats["n"],
                _fmt(stats.get("mean_avg_saving"), " g/kWh"),
                _fmt(stats.get("mean_marginal_saving"), " g/kWh"),
            ]
            for zone, z in sorted(marginal_block["zones"].items())
            for window, w in sorted(z["by_window"].items(), key=lambda kv: int(kv[0]))
            for band, stats in w.get("r2_bands", {}).items()
        ],
    )
    return blocks


def _filler(body):
    """re.sub replacement that keeps both markers and drops the body between them."""
    return lambda match: f"{match.group(1)}\n{body}\n{match.group(2)}"


def _filler(body):
    """re.sub replacement that keeps both markers and swaps what lies between them."""
    return lambda match: f"{match.group(1)}\n{body}\n{match.group(2)}"


def substitute(blocks):
    """Replace each `<!-- generated:NAME -->...<!-- /generated:NAME -->` block."""
    with open(DOC) as fh:
        text = fh.read()
    missing = []
    for name, body in blocks.items():
        pattern = re.compile(
            rf"(<!-- generated:{re.escape(name)} -->).*?(<!-- /generated:{re.escape(name)} -->)",
            re.DOTALL,
        )
        text, count = pattern.subn(_filler(body), text, count=1)
        if count == 0:
            missing.append(name)
    if missing:
        raise SystemExit(f"docs/VALIDATION.md has no marker for: {', '.join(missing)}")
    with open(DOC, "w") as fh:
        fh.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zones", default="CISO,PJM")
    parser.add_argument("--print", action="store_true", help="dump results.json to stdout")
    args = parser.parse_args(argv)

    results = build([z.strip() for z in args.zones.split(",") if z.strip()])
    with open(RESULTS, "w") as fh:
        json.dump(results, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote {os.path.relpath(RESULTS, ROOT)}")
    if args.print:
        print(json.dumps(results, indent=2, sort_keys=True))
    if os.path.exists(DOC):
        substitute(render(results))
        print(f"updated {os.path.relpath(DOC, ROOT)}")
    else:
        print(f"{os.path.relpath(DOC, ROOT)} does not exist yet; wrote results only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
