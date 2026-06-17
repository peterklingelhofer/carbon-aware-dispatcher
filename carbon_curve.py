"""Hour-of-day carbon intensity profile.

Builds a diurnal curve — average gCO2eq/kWh by hour of day — from historical
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
    """Relative spread (max-min)/mean as a percent — how much shifting can help.

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
# saves little — be honest and say so rather than add complexity for ~nothing.
DEFAULT_MIN_SPREAD_PCT = 15.0


def is_worth_shifting(profile, min_spread_pct=DEFAULT_MIN_SPREAD_PCT):
    """Whether time-shifting meaningfully helps, given the curve's spread."""
    return spread_pct(profile) >= min_spread_pct


def uk_history_samples(days=7):
    """Fetch GB national half-hourly history; return [(hour, intensity)] in UTC."""
    days = max(1, min(days, MAX_HISTORY_DAYS))
    to = datetime.now(timezone.utc)
    frm = to - timedelta(days=days)
    fmt = "%Y-%m-%dT%H:%MZ"
    url = f"{UK_API_BASE}/intensity/{frm.strftime(fmt)}/{to.strftime(fmt)}"
    data = base.request(url, parse="json")
    if not data:
        return []
    samples = []
    for period in data.get("data", []):
        actual = (period.get("intensity") or {}).get("actual")
        if actual is None:
            continue
        try:
            dt = datetime.fromisoformat(str(period.get("from", "")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        samples.append((dt.hour, actual))
    return samples


def build_profile(zone, days=7):
    """Build an hour-of-day profile for a zone, or None when no free history.

    Only GB has a free historical feed today; other zones return None so callers
    fall back to the forecast rather than pretend to have a curve.
    """
    if zone in ("GB", "GB-national"):
        return profile_from_samples(uk_history_samples(days)) or None
    return None
