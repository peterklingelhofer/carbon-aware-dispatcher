"""US EIA (Energy Information Administration) provider: no auth required."""

from collections import OrderedDict

from providers.base import (
    DEFAULT_FUEL_FACTOR,
    EIA_EMISSION_FACTORS,
    EIA_STORAGE_FUELS,
    api_request,
    ci_secret_hint,
    compute_trend,
    green_result,
)

EIA_API_BASE = "https://api.eia.gov/v2"


def _fuel_mix_totals(fuel_data):
    """Sum EIA fuel-mix rows into (total_generation, generation_weighted_co2).

    total_generation is MWh; generation_weighted_co2 is Sum(MWh x lifecycle
    factor), so their ratio is the interval's average intensity in gCO2eq/kWh and
    their inter-interval changes feed the marginal regression.
    """
    total_generation = 0.0
    total_co2 = 0.0

    for row in fuel_data:
        fuel_type = row.get("fueltype", "")
        value = row.get("value")
        if value is None:
            continue
        mwh = float(value)
        if mwh <= 0:
            continue
        # Storage is not primary generation and its discharge is not
        # zero-carbon, so exclude it from the mix entirely
        if fuel_type in EIA_STORAGE_FUELS:
            continue
        if fuel_type in EIA_EMISSION_FACTORS:
            ef = EIA_EMISSION_FACTORS[fuel_type]
        else:
            print(
                f"::warning::Unknown fuel type '{fuel_type}', using fallback "
                f"{DEFAULT_FUEL_FACTOR} gCO2eq/kWh"
            )
            ef = DEFAULT_FUEL_FACTOR
        total_generation += mwh
        total_co2 += mwh * ef

    return total_generation, total_co2


def _fuel_mix_to_intensity(fuel_data):
    """Calculate carbon intensity from EIA fuel mix data.

    fuel_data: list of dicts with 'fueltype' and 'value' keys.
    Returns carbon intensity in gCO2eq/kWh, or None on error.
    """
    total_generation, total_co2 = _fuel_mix_totals(fuel_data)
    if total_generation == 0:
        return None
    return round(total_co2 / total_generation)


def _fuel_mix_rows(zone, eia_api_key="", length=100):
    """Fetch recent hourly fuel-mix rows for a zone (newest first), or []."""
    api_key = eia_api_key or "DEMO_KEY"
    url = (
        f"{EIA_API_BASE}/electricity/rto/fuel-type-data/data"
        f"?api_key={api_key}"
        f"&frequency=hourly"
        f"&data[0]=value"
        f"&facets[respondent][]={zone}"
        f"&sort[0][column]=period"
        f"&sort[0][direction]=desc"
        f"&length={length}"
    )
    data = api_request(url)
    if data is None:
        return []
    return data.get("response", {}).get("data", [])


def fuel_mix_series(zone, eia_api_key="", length=100):
    """Per-hour (generation, generation_weighted_co2) series, oldest first.

    Feeds the free marginal-emissions estimator: each interval's totals let it
    regress emission change on generation change across hours. Returns [] when no
    history is available.
    """
    rows = _fuel_mix_rows(zone, eia_api_key, length)
    if not rows:
        return []
    periods = OrderedDict()
    for row in rows:
        periods.setdefault(row.get("period"), []).append(row)
    series = [_fuel_mix_totals(period_rows) for period_rows in periods.values()]
    series.reverse()  # API returns newest first; estimator wants time order
    return [(g, e) for g, e in series if g > 0]


def check_carbon_intensity(zone, max_carbon, eia_api_key=""):
    """Check carbon intensity using the EIA API (hourly fuel mix).

    Returns (is_green, intensity) or (None, None) on error.
    """
    api_key = eia_api_key or "DEMO_KEY"
    if api_key == "DEMO_KEY":
        print(
            "::notice::Using built-in EIA DEMO_KEY (rate limit ~30 req/hr). "
            "For higher limits, register a free key at https://www.eia.gov/opendata/register.php "
            f"and {ci_secret_hint('EIA_API_KEY')}."
        )
    url = (
        f"{EIA_API_BASE}/electricity/rto/fuel-type-data/data"
        f"?api_key={api_key}"
        f"&frequency=hourly"
        f"&data[0]=value"
        f"&facets[respondent][]={zone}"
        f"&sort[0][column]=period"
        f"&sort[0][direction]=desc"
        f"&length=10"
    )

    print(f"Checking carbon intensity for zone: {zone} (EIA API)...")
    data = api_request(url)
    if data is None:
        return None, None

    rows = data.get("response", {}).get("data", [])
    if not rows:
        print(f"::warning::No fuel mix data returned for zone {zone}")
        return None, None

    # Group by the most recent period
    latest_period = rows[0].get("period")
    latest_rows = [r for r in rows if r.get("period") == latest_period]

    intensity = _fuel_mix_to_intensity(latest_rows)
    if intensity is None:
        print(f"::warning::Could not calculate carbon intensity for zone {zone}")
        return None, None

    return green_result(zone, intensity, max_carbon)


def get_history_trend(zone, eia_api_key=""):
    """Fetch recent hourly fuel mix history from EIA and compute trend.

    Returns one of: "decreasing", "increasing", "stable", or None.
    """
    api_key = eia_api_key or "DEMO_KEY"
    url = (
        f"{EIA_API_BASE}/electricity/rto/fuel-type-data/data"
        f"?api_key={api_key}"
        f"&frequency=hourly"
        f"&data[0]=value"
        f"&facets[respondent][]={zone}"
        f"&sort[0][column]=period"
        f"&sort[0][direction]=desc"
        f"&length=100"
    )

    print(f"  Fetching history trend for zone: {zone}...")
    data = api_request(url)
    if data is None:
        return None

    rows = data.get("response", {}).get("data", [])
    if not rows:
        return None

    # Group by period and calculate intensity for each
    periods = OrderedDict()
    for row in rows:
        p = row.get("period")
        if p not in periods:
            periods[p] = []
        periods[p].append(row)

    intensities = []
    for period_rows in periods.values():
        intensity = _fuel_mix_to_intensity(period_rows)
        if intensity is not None:
            intensities.append(intensity)

    # Reverse so oldest is first (API returns newest first)
    intensities.reverse()
    return compute_trend(intensities)


def get_forecast(zone, max_carbon, eia_api_key=""):
    """EIA has no forecast endpoint. Use GridStatus for US zone forecasts.

    Returns (None, None).
    """
    return None, None
