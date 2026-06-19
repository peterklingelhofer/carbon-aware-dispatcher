"""Free marginal-emissions estimator.

Average grid intensity says how clean the grid is overall; *marginal* intensity
is the emissions of the generator that responds to YOUR added load, which is the
signal that actually reflects the carbon you avoid by shifting flexible compute.
True marginal data is free only for CAISO_NORTH (WattTime). This module estimates
it for any grid that exposes a fuel-mix time series, by regressing the change in
generation-weighted emissions on the change in total generation between
consecutive intervals: the slope through the origin is the marginal rate. The
r_squared says how much of the variation the load change explains, i.e. how much
to trust the number. No extra dependencies, no key.
"""

import math

# A reported marginal outside this band (gCO2eq/kWh) is unphysical, so clamp to the
# range spanned by the dirtiest (lignite ~1050) and cleanest real fuels, and let
# r_squared carry the confidence.
MARGINAL_CLAMP = (0, 1100)

# Minimum number of informative (load actually changed) interval pairs before we
# are willing to report an estimate at all.
MIN_PAIRS = 2


def _slope_through_origin(xs, ys):
    """Least-squares slope of y = m*x (no intercept): m = Sum(xy)/Sum(x^2)."""
    sxx = sum(x * x for x in xs)
    if sxx <= 0:
        return None
    return sum(x * y for x, y in zip(xs, ys)) / sxx


def _pearson_r2(xs, ys):
    """Square of the Pearson correlation between xs and ys, or None if undefined."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    r = sxy / math.sqrt(sxx * syy)
    return r * r


def estimate_marginal(series, min_load_change=1.0):
    """Estimate the marginal emissions rate (gCO2eq/kWh) from a fuel-mix series.

    series: list of (generation, weighted_emissions) per interval, in time order,
    where weighted_emissions is Sum(MWh_fuel x lifecycle_factor) so that
    weighted_emissions / generation is the interval's average intensity. The
    marginal generator is the one that moves to meet a load change, so we regress
    the change in weighted_emissions on the change in generation across
    consecutive intervals; the origin slope is the marginal rate (already in
    gCO2eq/kWh, since the per-kWh factors cancel the MWh). Intervals whose load
    barely moves carry no marginal signal and are dropped.

    Returns {"marginal", "average", "r_squared", "n"} or None when there is too
    little signal (fewer than MIN_PAIRS informative intervals, or no load change).
    """
    rows = [(float(g), float(e)) for g, e in series if g is not None and e is not None]
    if len(rows) < MIN_PAIRS + 1:
        return None

    dgs, des = [], []
    for (g0, e0), (g1, e1) in zip(rows, rows[1:]):
        dg = g1 - g0
        if abs(dg) < min_load_change:
            continue
        dgs.append(dg)
        des.append(e1 - e0)
    if len(dgs) < MIN_PAIRS:
        return None

    slope = _slope_through_origin(dgs, des)
    if slope is None:
        return None

    total_g = sum(g for g, _ in rows)
    total_e = sum(e for _, e in rows)
    average = round(total_e / total_g) if total_g > 0 else None
    r2 = _pearson_r2(dgs, des)
    marginal = max(MARGINAL_CLAMP[0], min(MARGINAL_CLAMP[1], round(slope)))
    return {
        "marginal": marginal,
        "average": average,
        "r_squared": round(r2, 3) if r2 is not None else None,
        "n": len(dgs),
    }
