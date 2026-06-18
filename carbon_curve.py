"""Hour-of-day carbon intensity profile.

Builds a diurnal curve (average gCO2eq/kWh by hour of day) from historical
samples, so scheduling decisions rest on a stable, repeating pattern rather than
a single forecast day. Genuine free hourly history exists for GB via the UK
Carbon Intensity API; where a provider has no free history, callers fall back to
the live forecast. The analysis functions are pure and provider-agnostic, so any
sample source (a paid provider, a local log) can feed them.
"""

from datetime import datetime, timedelta, timezone

from providers import base

UK_API_BASE = "https://api.carbonintensity.org.uk"
# UK history endpoint accepts at most a 14-day span per request.
MAX_HISTORY_DAYS = 14


def profile_from_samples(samples):
    """Average intensity by hour of day (pure).

    samples: iterable of (hour:int, intensity). Returns {hour: mean_intensity}
    for the hours present, ignoring None intensities.
    """
    buckets = {}
    for hour, intensity in samples:
        if intensity is None:
            continue
        buckets.setdefault(hour, []).append(float(intensity))
    return {h: round(sum(v) / len(v), 1) for h, v in buckets.items()}


def cleanest_hour(profile):
    """Return (hour, intensity) of the lowest-intensity hour, or (None, None)."""
    if not profile:
        return None, None
    hour = min(profile, key=lambda h: profile[h])
    return hour, profile[hour]


def spread_pct(profile):
    """Relative spread (max-min)/mean as a percent: how much shifting can help.

    A high spread means the cleanest hour is much cleaner than the dirtiest, so
    scheduling pays off; a low spread means a flat grid where it barely matters.
    """
    if not profile:
        return 0.0
    values = list(profile.values())
    mean = sum(values) / len(values)
    if mean <= 0:
        return 0.0
    return round((max(values) - min(values)) / mean * 100, 1)


# Below this relative spread, the grid is flat enough that shifting the schedule
# saves little; say so rather than add complexity for ~nothing.
DEFAULT_MIN_SPREAD_PCT = 15.0


def is_worth_shifting(profile, min_spread_pct=DEFAULT_MIN_SPREAD_PCT):
    """Whether time-shifting meaningfully helps, given the curve's spread."""
    return spread_pct(profile) >= min_spread_pct


def mean_intensity(profile):
    """Mean intensity across the profiled hours, or 0 for an empty profile."""
    return round(sum(profile.values()) / len(profile), 1) if profile else 0.0


def shift_savings_grams(profile, from_hour, to_hour, energy_kwh):
    """gCO2 saved per run by moving a job from from_hour to to_hour (>= 0).

    Honest, curve-based: the actual intensity delta between the two hours times
    the run's energy. No hypothetical average grid enters the calculation.
    """
    if not profile or from_hour not in profile or to_hour not in profile:
        return 0.0
    return round(max(0.0, (profile[from_hour] - profile[to_hour]) * energy_kwh), 1)


def cleanest_window(profile, hours):
    """Start hour of the cleanest contiguous `hours`-long window (wraps midnight).

    For batch jobs that run for several hours, the right target is the cleanest
    block spanning them. Returns (start_hour, avg_intensity) over windows
    where every hour is present in the profile, or (None, None) if none qualify.
    """
    hours = int(hours)
    if not profile or hours < 1 or hours > 24:
        return None, None
    best_start, best_avg = None, None
    for start in range(24):
        window = [(start + i) % 24 for i in range(hours)]
        if not all(h in profile for h in window):
            continue
        avg = sum(profile[h] for h in window) / hours
        if best_avg is None or avg < best_avg:
            best_avg, best_start = avg, start
    if best_start is None:
        return None, None
    return best_start, round(best_avg, 1)


def best_case_savings_grams(profile, energy_kwh):
    """Max gCO2 saved per run: dirtiest hour minus cleanest, times energy."""
    if not profile:
        return 0.0
    return round((max(profile.values()) - min(profile.values())) * energy_kwh, 1)


def _uk_history_periods(days=7):
    """Fetch GB national half-hourly history; return [(datetime, intensity)] in UTC."""
    days = max(1, min(days, MAX_HISTORY_DAYS))
    to = datetime.now(timezone.utc)
    frm = to - timedelta(days=days)
    fmt = "%Y-%m-%dT%H:%MZ"
    url = f"{UK_API_BASE}/intensity/{frm.strftime(fmt)}/{to.strftime(fmt)}"
    data = base.request(url, parse="json")
    if not data:
        return []
    periods = []
    for period in data.get("data", []):
        actual = (period.get("intensity") or {}).get("actual")
        if actual is None:
            continue
        try:
            dt = datetime.fromisoformat(str(period.get("from", "")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        periods.append((dt, actual))
    return periods


def uk_history_samples(days=7):
    """GB half-hourly history as [(hour, intensity)] in UTC."""
    return [(dt.hour, val) for dt, val in _uk_history_periods(days)]


def uk_weekday_samples(days=14):
    """GB half-hourly history as [(weekday, intensity)] (Mon=0..Sun=6, UTC)."""
    return [(dt.weekday(), val) for dt, val in _uk_history_periods(days)]


def weekday_profile_from_samples(samples, min_days=3):
    """Average intensity by weekday (Mon=0..Sun=6). {} until min_days seen."""
    buckets = {}
    for weekday, intensity in samples:
        if intensity is None:
            continue
        buckets.setdefault(weekday, []).append(float(intensity))
    profile = {d: round(sum(v) / len(v), 1) for d, v in buckets.items()}
    return profile if len(profile) >= min_days else {}


def cleanest_weekday(profile):
    """Return (weekday, intensity) of the lowest-intensity day, or (None, None)."""
    if not profile:
        return None, None
    day = min(profile, key=lambda d: profile[d])
    return day, profile[day]


def build_weekday_profile(zone):
    """Day-of-week profile for a zone, or {} when no free history exists.

    GB has a free historical feed; other zones return {} for now (the hour-of-day
    curve already accumulates per zone; weekday accumulation can follow).
    """
    if zone in ("GB", "GB-national"):
        return weekday_profile_from_samples(uk_weekday_samples())
    return {}


def ledger_profile(zone):
    """Build a profile from the curve accumulated in the configured ledger.

    Works for ANY zone once enough hours have been sampled across runs: the way
    coverage extends beyond GB without paid history. Returns {} when no ledger is
    configured or too few hours are recorded yet.
    """
    import os

    import ledger

    backend, location = ledger.parse_config(os.environ.get("LEDGER", ""))
    if not backend or not location:
        return {}
    if backend == "file":
        data = ledger._load_file(location)
    else:
        data, _ = ledger._gist_read(location, os.environ.get("GIST_TOKEN", ""))
    return ledger.curve_profile(data, zone)


def community_profile(zone):
    """Build a profile from a shared community curve file (the data commons).

    Set COMMUNITY_CURVE to a pooled curve file (produced by `carbon-aware
    export-curves` and merged across users) to get an hour-of-day profile for any
    zone others have sampled, even with no local history. Returns {} when unset,
    missing, or too sparse for the zone.
    """
    import json
    import os

    path = os.environ.get("COMMUNITY_CURVE", "")
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    import ledger

    return ledger.curve_profile(data, zone)


def build_profile(zone, days=7):
    """Build an hour-of-day profile for a zone, or None when none is available.

    Prefers a free historical API (GB today), then the curve accumulated in the
    local ledger over past runs (any zone), then a shared community curve file.
    Returns None so callers fall back to the forecast rather than invent a curve.
    """
    if zone in ("GB", "GB-national"):
        profile = profile_from_samples(uk_history_samples(days))
        if profile:
            return profile
    return ledger_profile(zone) or community_profile(zone) or None
