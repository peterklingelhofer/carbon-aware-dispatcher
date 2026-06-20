"""Cumulative carbon-savings ledger.

Each run records the CO2 it saved to a persistent store, so the impact compounds
into a lifetime total you can watch grow instead of a number that vanishes after
one build. Two backends, selected by the `ledger` input:

  - ``gist:<id>``   persist to a GitHub Gist (needs a gist-scoped token via the
                    ``gist_token`` input). The gist also publishes a shields.io
                    endpoint badge and feeds the Pages dashboard.
  - ``file:<path>`` persist to a local JSON file (handy for self-hosted runners,
                    local testing, or committing the file yourself).

The ledger is intentionally tiny and self-healing: a missing or corrupt store
starts fresh rather than failing the build. Tracking savings must never break
someone's CI, so every IO path degrades to a warning and a skip.
"""

import json
from datetime import datetime

from providers import base

LEDGER_FILENAME = "carbon-ledger.json"
BADGE_FILENAME = "carbon-badge.json"
STATUS_BADGE_FILENAME = "carbon-now.json"
GIST_API = "https://api.github.com/gists"

# Keep at most a year of daily history so the gist/dashboard stays small.
HISTORY_CAP = 365


def empty_ledger():
    """Return a fresh, empty ledger structure."""
    return {"schemaVersion": 1, "totals": {}, "history": []}


def _weekday_of(date_str):
    """Weekday (Mon=0..Sun=6) for a YYYY-MM-DD date_str, or None if unparseable."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").weekday()
    except (TypeError, ValueError):
        return None


def merge_entry(data, saved_grams, date_str, emitted_grams=0, is_green=None):
    """Fold one run's savings and emissions into the ledger, aggregating by day.

    Pure: returns a new dict, never mutates the input. Negative values are
    clamped to zero so a dirty-grid run still counts as a build without
    subtracting from the running totals. emitted_grams is the actual CO2 the run
    produced on the grid, used for carbon budgeting. is_green (when provided)
    tallies green runs for Green SLA compliance.
    """
    saved = max(0.0, float(saved_grams or 0))
    emitted = max(0.0, float(emitted_grams or 0))
    green_inc = 1 if is_green else 0

    totals = dict(data.get("totals") or {})
    totals["co2_saved_grams"] = round(float(totals.get("co2_saved_grams", 0)) + saved, 1)
    totals["co2_emitted_grams"] = round(float(totals.get("co2_emitted_grams", 0)) + emitted, 1)
    totals["runs"] = int(totals.get("runs", 0)) + 1
    if is_green is not None:
        totals["green_runs"] = int(totals.get("green_runs", 0)) + green_inc
    totals.setdefault("first_run", date_str)
    totals["last_run"] = date_str

    history = [dict(h) for h in (data.get("history") or [])]
    if history and history[-1].get("date") == date_str:
        last = history[-1]
        last["saved_g"] = round(float(last.get("saved_g", 0)) + saved, 1)
        last["emitted_g"] = round(float(last.get("emitted_g", 0)) + emitted, 1)
        last["runs"] = int(last.get("runs", 0)) + 1
        if is_green is not None:
            last["green"] = int(last.get("green", 0)) + green_inc
    else:
        entry = {
            "date": date_str,
            "saved_g": round(saved, 1),
            "emitted_g": round(emitted, 1),
            "runs": 1,
        }
        if is_green is not None:
            entry["green"] = green_inc
        history.append(entry)

    result = {
        "schemaVersion": 1,
        "totals": totals,
        "history": history[-HISTORY_CAP:],
    }
    if data.get("curve"):  # preserve the accumulated hour-of-day curve
        result["curve"] = data["curve"]
    if data.get("weekday_curve"):  # and the accumulated day-of-week curve
        result["weekday_curve"] = data["weekday_curve"]
    return result


def _merge_sample(data, field, zone, key, intensity):
    """Fold one (key, intensity) reading into a per-zone sum/count curve (pure).

    field is "curve" (hour of day) or "weekday_curve" (Mon=0..Sun=6). Storing a
    running sum/count keeps the store tiny however many runs accumulate. No-op
    when zone/key/intensity are missing
    """
    if zone is None or key is None or intensity is None:
        return data
    out = dict(data)
    curve = {z: dict(cells) for z, cells in (data.get(field) or {}).items()}
    zone_curve = dict(curve.get(zone, {}))
    cell = dict(zone_curve.get(str(key), {"sum": 0.0, "n": 0}))
    cell["sum"] = round(float(cell.get("sum", 0)) + float(intensity), 1)
    cell["n"] = int(cell.get("n", 0)) + 1
    zone_curve[str(key)] = cell
    curve[zone] = zone_curve
    out[field] = curve
    return out


def _cell_profile(data, field, zone, min_cells):
    """Mean intensity per cell from a sum/count curve, or {} below min_cells.

    field is "curve" or "weekday_curve". Below min_cells distinct samples the
    profile is withheld so a repo that only runs at one time gets no misleading curve
    """
    cells = (data.get(field) or {}).get(zone) or {}
    profile = {}
    for key, cell in cells.items():
        n = int(cell.get("n", 0))
        if n > 0:
            profile[int(key)] = round(float(cell.get("sum", 0)) / n, 1)
    return profile if len(profile) >= min_cells else {}


def merge_curve_sample(data, zone, hour, intensity):
    """Fold one (hour, intensity) reading into the per-zone hour-of-day curve.

    Builds an hour-of-day profile for any zone over time (24 cells per zone), not
    just those with free historical APIs.
    """
    return _merge_sample(data, "curve", zone, hour, intensity)


def merge_weekday_sample(data, zone, weekday, intensity):
    """Fold one (weekday, intensity) reading into the per-zone day-of-week curve.

    The day-of-week axis (Mon=0..Sun=6, 7 cells per zone) captures
    weekend-vs-weekday shifts as a second scheduling axis.
    """
    return _merge_sample(data, "weekday_curve", zone, weekday, intensity)


def weekday_profile(data, zone, min_days=3):
    """Derive a {weekday: mean_intensity} profile from the day-of-week curve.

    Returns {} until at least min_days distinct weekdays have been sampled, so a
    repo that only ever runs on one day doesn't get a misleading curve.
    """
    return _cell_profile(data, "weekday_curve", zone, min_days)


def _fold_cells(into, cells, cap_n):
    """Add one zone's sum/n cells into an accumulator, honoring the weight cap."""
    for key, cell in cells.items():
        n = int(cell.get("n", 0))
        if n <= 0:
            continue
        s = float(cell.get("sum", 0))
        if cap_n and n > cap_n:
            s = s * cap_n / n  # keep the mean, cap the weight
            n = cap_n
        acc = into.setdefault(str(key), {"sum": 0.0, "n": 0})
        acc["sum"] = round(acc["sum"] + s, 1)
        acc["n"] += n


def merge_curves(docs, cap_n=None):
    """Pool several curve documents into one by summing each cell's sum/n.

    Each doc is shaped like a ledger ({"curve": {zone: {hour: {sum, n}}}} plus an
    optional "weekday_curve"); the running sum/count form means pooling across
    contributors is just addition, so the merged mean is the true volume-weighted
    average. Powers the community data commons: many users' exported curves merge
    into one shared hour-of-day and day-of-week profile.

    cap_n limits how much any single document can weigh on a cell: one with more
    than cap_n samples is scaled down to cap_n samples while preserving its mean,
    so the pool reflects breadth across contributors rather than one high-volume
    repo dominating a zone.
    """
    pooled = {"curve": {}, "weekday_curve": {}}
    for doc in docs:
        for field in ("curve", "weekday_curve"):
            for zone, cells in (doc.get(field) or {}).items():
                _fold_cells(pooled[field].setdefault(zone, {}), cells, cap_n)
    result = {"curve": pooled["curve"]}
    if pooled["weekday_curve"]:
        result["weekday_curve"] = pooled["weekday_curve"]
    return result


MAX_PLAUSIBLE_INTENSITY = 2000.0


def _validate_cells(zone, unit, cells, lo, hi, max_intensity, problems):
    """Validate one zone's sum/n cells (keys in [lo, hi]), appending problems."""
    if not isinstance(cells, dict) or not cells:
        problems.append(f"{zone}: no {unit} cells")
        return
    for key, cell in cells.items():
        try:
            k = int(key)
        except (TypeError, ValueError):
            problems.append(f"{zone}: non-integer {unit} {key!r}")
            continue
        if not lo <= k <= hi:
            problems.append(f"{zone}: {unit} {k} out of range")
        if not isinstance(cell, dict):
            problems.append(f"{zone} {unit} {k}: cell is not an object")
            continue
        n = cell.get("n")
        s = cell.get("sum")
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            problems.append(f"{zone} {unit} {k}: bad count {n!r}")
            continue
        if isinstance(s, bool) or not isinstance(s, (int, float)):
            problems.append(f"{zone} {unit} {k}: bad sum {s!r}")
            continue
        mean = s / n
        if mean < 0 or mean > max_intensity:
            problems.append(f"{zone} {unit} {k}: implausible mean {mean:.0f} gCO2/kWh")


def validate_curve_doc(doc, max_intensity=MAX_PLAUSIBLE_INTENSITY, min_hours=6):
    """Check a contributed curve doc, returning a list of problems ([] = valid).

    Guards the community pool against malformed or skewing input before it is
    merged: structural shape, hour keys in 0-23 (and weekday keys in 0-6 when a
    weekday_curve is present), positive integer counts, numeric sums, per-cell
    means within a plausible grid range, and at least one zone with enough
    distinct hours to be a real contribution rather than a sparse dump.
    """
    if not isinstance(doc, dict):
        return ["not a JSON object"]
    curve = doc.get("curve")
    if not isinstance(curve, dict) or not curve:
        return ["missing or empty 'curve'"]

    problems = []
    for zone, cells in curve.items():
        _validate_cells(zone, "hour", cells, 0, 23, max_intensity, problems)

    weekday_curve = doc.get("weekday_curve")
    if weekday_curve is not None:
        if not isinstance(weekday_curve, dict):
            problems.append("weekday_curve: not an object")
        else:
            for zone, cells in weekday_curve.items():
                _validate_cells(zone, "weekday", cells, 0, 6, max_intensity, problems)

    if min_hours and not problems:
        usable = any(len(curve_profile(doc, z, min_hours)) for z in curve)
        if not usable:
            problems.append(f"no zone has >= {min_hours} sampled hours (too sparse to contribute)")
    return problems


def curve_mean(data, zone, min_hours=6):
    """Mean intensity across the zone's accumulated curve, or 0 if too sparse."""
    profile = curve_profile(data, zone, min_hours)
    return round(sum(profile.values()) / len(profile), 1) if profile else 0.0


def curve_profile(data, zone, min_hours=6):
    """Derive an hour-of-day {hour: mean_intensity} profile from the ledger curve.

    Returns {} until at least min_hours distinct hours have been sampled, so a
    repo that only ever runs at one time of day doesn't get a misleading curve.
    """
    return _cell_profile(data, "curve", zone, min_hours)


def month_to_date_emitted(data, month_prefix):
    """Sum emitted gCO2 for history days within the given YYYY-MM prefix."""
    total = 0.0
    for h in data.get("history") or []:
        if str(h.get("date", "")).startswith(month_prefix):
            total += float(h.get("emitted_g", 0))
    return round(total, 1)


def sla_window(data, prefix=""):
    """Sum (green_runs, total_runs) over history days matching the date prefix.

    An empty prefix covers all history (lifetime). Used for Green SLA compliance:
    the share of runs that ran on a clean grid.
    """
    green = total = 0
    for h in data.get("history") or []:
        if str(h.get("date", "")).startswith(prefix):
            green += int(h.get("green", 0))
            total += int(h.get("runs", 0))
    return green, total


def format_total(grams):
    """Format a gram total as a compact human string ("4.2 kg" / "850 g")."""
    if grams >= 1000:
        return f"{grams / 1000:.1f} kg"
    return f"{grams:.0f} g"


def badge_payload(data):
    """Build a shields.io endpoint-badge payload from the ledger totals."""
    totals = data.get("totals") or {}
    grams = float(totals.get("co2_saved_grams", 0))
    runs = int(totals.get("runs", 0))
    return {
        "schemaVersion": 1,
        "label": "CO2 saved",
        "message": f"{format_total(grams)} over {runs} builds",
        "color": "brightgreen" if grams > 0 else "lightgrey",
    }


def status_badge_payload(zone, intensity, tier):
    """Build a shields.io payload for the live current-grid status badge."""
    color = {"green": "brightgreen", "amber": "yellow", "red": "red"}.get(tier, "blue")
    return {
        "schemaVersion": 1,
        "label": "grid now",
        "message": f"{zone} {intensity} gCO2eq/kWh",
        "color": color,
    }


def write_status_badge(gist_id, token, zone, intensity, tier):
    """Write the live status badge to the gist; return its shields URL or None."""
    if not token or not gist_id:
        return None
    body = {
        "files": {
            STATUS_BADGE_FILENAME: {
                "content": json.dumps(status_badge_payload(zone, intensity, tier), indent=2)
            }
        }
    }
    resp = base.request(
        f"{GIST_API}/{gist_id}",
        method="PATCH",
        headers=base.github_headers(token),
        json_body=body,
        parse="json",
    )
    if resp is None:
        return None
    owner = (resp.get("owner") or {}).get("login")
    if not owner:
        return None
    return _shields_endpoint(owner, gist_id, STATUS_BADGE_FILENAME)


def _shields_endpoint(owner, gist_id, filename):
    """Build the shields.io endpoint-badge URL for a raw gist file."""
    raw = f"https://gist.githubusercontent.com/{owner}/{gist_id}/raw/{filename}"
    return f"https://img.shields.io/endpoint?url={raw}"


def parse_config(config):
    """Split a ``ledger`` config string into (backend, location).

    Returns (None, None) when the ledger is disabled or the prefix is unknown.
    """
    if not config:
        return None, None
    config = config.strip()
    for prefix in ("gist:", "file:"):
        if config.startswith(prefix):
            return prefix[:-1], config[len(prefix) :].strip()
    return None, None


def _load_file(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return empty_ledger()


def _save_file(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _gist_read(gist_id, token):
    """Return (ledger_data, owner_login). Falls back to an empty ledger."""
    resp = base.request(f"{GIST_API}/{gist_id}", headers=base.github_headers(token), parse="json")
    if not resp:
        return empty_ledger(), None
    owner = (resp.get("owner") or {}).get("login")
    entry = (resp.get("files") or {}).get(LEDGER_FILENAME)
    if not entry or not entry.get("content"):
        return empty_ledger(), owner
    try:
        return json.loads(entry["content"]), owner
    except ValueError:
        return empty_ledger(), owner


def _gist_write(gist_id, token, data):
    body = {
        "files": {
            LEDGER_FILENAME: {"content": json.dumps(data, indent=2)},
            BADGE_FILENAME: {"content": json.dumps(badge_payload(data), indent=2)},
        }
    }
    return base.request(
        f"{GIST_API}/{gist_id}",
        method="PATCH",
        headers=base.github_headers(token),
        json_body=body,
        parse="json",
    )


def _summary(data, badge_url, month=None):
    totals = data.get("totals") or {}
    grams = float(totals.get("co2_saved_grams", 0))
    runs = int(totals.get("runs", 0))
    green_mtd, runs_mtd = sla_window(data, month) if month else (0, 0)
    return {
        "total_grams": grams,
        "total_runs": runs,
        "message": f"{format_total(grams)} over {runs} builds",
        "badge_url": badge_url,
        "emitted_total": float(totals.get("co2_emitted_grams", 0)),
        "emitted_mtd": month_to_date_emitted(data, month) if month else 0,
        "avoided_total": float(totals.get("co2_avoided_grams", 0)),
        "green_mtd": green_mtd,
        "runs_mtd": runs_mtd,
    }


def _assemble(
    current, saved_grams, date_str, emitted_grams, zone, intensity, hour, energy_kwh, is_green=None
):
    """Build the next ledger state: fold the run, the curve sample, and the
    counterfactual avoided emissions (vs the zone's own typical hour so far)."""
    avoided = 0.0
    if zone and intensity is not None and energy_kwh:
        mean = curve_mean(current, zone)  # the zone's typical hour, from history so far
        if mean:
            avoided = round(max(0.0, (mean - float(intensity)) * energy_kwh), 1)
    data = merge_entry(current, saved_grams, date_str, emitted_grams, is_green=is_green)
    data = merge_curve_sample(data, zone, hour, intensity)
    data = merge_weekday_sample(data, zone, _weekday_of(date_str), intensity)
    if avoided:
        totals = dict(data.get("totals") or {})
        totals["co2_avoided_grams"] = round(float(totals.get("co2_avoided_grams", 0)) + avoided, 1)
        data["totals"] = totals
    return data


def record_savings(
    config,
    token,
    saved_grams,
    date_str,
    emitted_grams=0,
    zone=None,
    intensity=None,
    hour=None,
    energy_kwh=0,
    is_green=None,
):
    """Append this run to the configured ledger and return a summary.

    Also folds (hour, intensity) into the per-zone hour-of-day curve, tracks
    counterfactual avoided emissions (cleaner than the zone's typical hour), and
    tallies green runs for Green SLA compliance. Returns a dict with total_grams,
    total_runs, message, badge_url (gist only), emitted_total, emitted_mtd,
    avoided_total, green_mtd, and runs_mtd, or None when the ledger is disabled or
    an IO/auth step fails. Never raises: failures degrade to a warning so CI is
    never broken by bookkeeping.
    """
    backend, location = parse_config(config)
    if not backend or not location:
        return None
    month = date_str[:7]

    if backend == "file":
        data = _assemble(
            _load_file(location),
            saved_grams,
            date_str,
            emitted_grams,
            zone,
            intensity,
            hour,
            energy_kwh,
            is_green,
        )
        try:
            _save_file(location, data)
        except OSError as exc:
            print(f"::warning::Could not write ledger file {location}: {exc}")
            return None
        return _summary(data, None, month)

    # gist backend
    if not token:
        print(
            "::warning::ledger gist backend needs a gist-scoped token "
            "(gist_token input); skipping ledger update"
        )
        return None
    current, owner = _gist_read(location, token)
    data = _assemble(
        current, saved_grams, date_str, emitted_grams, zone, intensity, hour, energy_kwh, is_green
    )
    if _gist_write(location, token, data) is None:
        print("::warning::Could not update ledger gist; skipping ledger update")
        return None
    badge_url = _shields_endpoint(owner, location, BADGE_FILENAME) if owner else None
    return _summary(data, badge_url, month)
