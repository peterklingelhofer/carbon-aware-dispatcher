"""Hour-of-day carbon intensity profile.

Builds a diurnal curve (average gCO2eq/kWh by hour of day) from historical
samples, so scheduling decisions rest on a stable, repeating pattern rather than
a single forecast day. Genuine free hourly history exists for GB via the UK
Carbon Intensity API; where a provider has no free history, callers fall back to
the live forecast. The analysis functions are pure and provider-agnostic, so any
sample source (a paid provider, a local log) can feed them.
"""

import math
from datetime import datetime, timedelta, timezone

from providers import base

UK_API_BASE = "https://api.carbonintensity.org.uk"
# UK history endpoint accepts at most a 14-day span per request.
MAX_HISTORY_DAYS = 14


def _bucket_samples(samples):
    """Group (key, intensity) pairs into {key: [intensities]}, dropping None.

    key is whatever the caller bins on (hour of day or weekday)
    """
    buckets = {}
    for key, intensity in samples:
        if intensity is None:
            continue
        buckets.setdefault(key, []).append(float(intensity))
    return buckets


def profile_from_samples(samples):
    """Average intensity by hour of day (pure).

    samples: iterable of (hour:int, intensity). Returns {hour: mean_intensity}
    for the hours present, ignoring None intensities.
    """
    return {h: round(sum(v) / len(v), 1) for h, v in _bucket_samples(samples).items()}


def profile_stats_from_samples(samples):
    """Per-hour statistics from raw samples (pure).

    Returns {hour: {"mean", "count", "var"}} using the sample variance (n-1);
    an hour with a single sample gets variance 0.0. Feeds the ANOVA significance
    test and the confidence band, which a bare {hour: mean} profile can't support.
    """
    stats = {}
    for hour, vals in _bucket_samples(samples).items():
        n = len(vals)
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / (n - 1) if n > 1 else 0.0
        stats[hour] = {"mean": round(mean, 1), "count": n, "var": round(var, 2)}
    return stats


def median_profile_from_samples(samples):
    """Per-hour MEDIAN intensity (pure): resists spikes, unlike the mean.

    A single bad reading (a grid data glitch) can drag an hour's mean around;
    the median ignores it, so the cleanest-hour pick is more trustworthy.
    """
    out = {}
    for hour, vals in _bucket_samples(samples).items():
        vals.sort()
        n = len(vals)
        mid = n // 2
        out[hour] = round(vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2, 1)
    return out


def anova_f(stats):
    """One-way ANOVA F statistic: does hour-of-day explain intensity variance?

    F = between-hour mean square / within-hour mean square. A large F means the
    diurnal pattern is real signal beyond the noise of a few samples. Returns
    (F, df_between, df_within), or None when there are too few groups/samples.
    """
    groups = [s for s in stats.values() if s["count"] > 0]
    k = len(groups)
    total_n = sum(s["count"] for s in groups)
    if k < 2 or total_n - k < 1:
        return None
    grand = sum(s["mean"] * s["count"] for s in groups) / total_n
    ss_between = sum(s["count"] * (s["mean"] - grand) ** 2 for s in groups)
    ss_within = sum((s["count"] - 1) * s["var"] for s in groups)
    df_between = k - 1
    df_within = total_n - k
    # Floor the within-group mean square so a perfectly clean pattern (zero noise)
    # yields a huge finite F rather than dividing by zero.
    ms_within = max(ss_within / df_within, 1e-9)
    ms_between = ss_between / df_between
    return ms_between / ms_within, df_between, df_within


# A deliberately conservative F bar. The exact 0.05 critical value for the large
# degrees of freedom we get from many hourly samples is ~1.5-2.0; 4.0 means we
# only call a diurnal pattern "real" when it clearly beats sampling noise, so we
# never tell a user to add scheduling complexity for a curve that is mostly noise.
DEFAULT_F_CRIT = 4.0


def is_significant(stats, f_crit=DEFAULT_F_CRIT):
    """True when hour-of-day variation is statistically real (ANOVA F >= f_crit)."""
    result = anova_f(stats)
    return bool(result and result[0] >= f_crit)


def confidence_band(stats, z=1.96):
    """95% confidence band on each hour's mean: mean +/- z x standard error.

    Returns {hour: {"mean","lo","hi","count"}}. A wide band flags an
    under-sampled hour whose number should be trusted less.
    """
    band = {}
    for hour, s in stats.items():
        n = s["count"]
        margin = z * math.sqrt(s["var"] / n) if n > 0 else 0.0
        band[hour] = {
            "mean": s["mean"],
            "lo": round(s["mean"] - margin, 1),
            "hi": round(s["mean"] + margin, 1),
            "count": n,
        }
    return band


def build_profile_samples(zone, days=7):
    """Raw [(hour, intensity)] for a zone with a free historical feed, else None.

    Only GB exposes raw half-hourly history for free today; the accumulated
    ledger/community curves store aggregates without the per-sample detail the
    significance test and confidence band need, so this returns None for them and
    callers fall back to the spread heuristic.
    """
    if zone in ("GB", "GB-national"):
        return uk_history_samples(days) or None
    return None


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
    profile = {d: round(sum(v) / len(v), 1) for d, v in _bucket_samples(samples).items()}
    return profile if len(profile) >= min_days else {}


def cleanest_weekday(profile):
    """Return (weekday, intensity) of the lowest-intensity day, or (None, None)."""
    if not profile:
        return None, None
    day = min(profile, key=lambda d: profile[d])
    return day, profile[day]


def build_weekday_profile(zone):
    """Day-of-week profile for a zone, or {} when none is available.

    Prefers GB's free historical feed, then the weekday curve accumulated in the
    local ledger over past runs (any zone), then a shared community curve. Lets
    schedulers capture weekend-vs-weekday shifts beyond GB as coverage grows.
    """
    if zone in ("GB", "GB-national"):
        profile = weekday_profile_from_samples(uk_weekday_samples())
        if profile:
            return profile
    return ledger_weekday_profile(zone) or community_weekday_profile(zone) or {}


def _load_ledger_doc():
    """Load the configured ledger document, or None when none is configured."""
    import os

    import ledger

    backend, location = ledger.parse_config(os.environ.get("LEDGER", ""))
    if not backend or not location:
        return None
    if backend == "file":
        return ledger._load_file(location)
    data, _ = ledger._gist_read(location, os.environ.get("GIST_TOKEN", ""))
    return data


def ledger_profile(zone):
    """Build an hour-of-day profile from the curve accumulated in the ledger.

    Works for ANY zone once enough hours have been sampled across runs: the way
    coverage extends beyond GB without paid history. Returns {} when no ledger is
    configured or too few hours are recorded yet.
    """
    import ledger

    data = _load_ledger_doc()
    return ledger.curve_profile(data, zone) if data is not None else {}


def ledger_weekday_profile(zone):
    """Build a day-of-week profile from the weekday curve accumulated in the ledger."""
    import ledger

    data = _load_ledger_doc()
    return ledger.weekday_profile(data, zone) if data is not None else {}


def _load_community_curve(src):
    """Load a pooled curve doc from a local path or an http(s) URL, or {}."""
    if src.startswith(("http://", "https://")):
        return base.request(src) or {}
    import json
    import os

    if not os.path.exists(src):
        return {}
    try:
        with open(src) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def community_profile(zone):
    """Build a profile from a shared community curve (the data commons).

    Set COMMUNITY_CURVE to a pooled curve produced by `carbon-aware export-curves`
    and merged across users (`carbon-aware merge-curves`), either a local file or
    a published http(s) URL, to get an hour-of-day profile for any zone others
    have sampled, even with no local history. Returns {} when unset, unreachable,
    or too sparse for the zone.
    """
    import os

    src = os.environ.get("COMMUNITY_CURVE", "")
    if not src:
        return {}
    data = _load_community_curve(src)
    if not data:
        return {}
    import ledger

    return ledger.curve_profile(data, zone)


def community_weekday_profile(zone):
    """Day-of-week profile from the shared community curve, or {} when unavailable.

    Same COMMUNITY_CURVE source as community_profile; reads the pool's
    weekday_curve so zones with no free weekday history still gain a
    weekend-vs-weekday profile as the commons grows.
    """
    import os

    src = os.environ.get("COMMUNITY_CURVE", "")
    if not src:
        return {}
    data = _load_community_curve(src)
    if not data:
        return {}
    import ledger

    return ledger.weekday_profile(data, zone)


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
