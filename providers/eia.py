"""US EIA (Energy Information Administration) provider — no auth required."""

from collections import OrderedDict

from providers.base import EIA_EMISSION_FACTORS, api_request, compute_trend

EIA_API_BASE = "https://api.eia.gov/v2"


def _fuel_mix_to_intensity(fuel_data):
    """Calculate carbon intensity from EIA fuel mix data.

    fuel_data: list of dicts with 'fueltype' and 'value' keys.
    Returns carbon intensity in gCO2eq/kWh, or None on error.
    """
    total_generation = 0
    total_co2 = 0

    for row in fuel_data:
        fuel_type = row.get("fueltype", "")
        value = row.get("value")
        if value is None:
            continue
        mwh = float(value)
        if mwh <= 0:
            continue
        total_generation += mwh
        ef = EIA_EMISSION_FACTORS.get(fuel_type, 200)
        total_co2 += mwh * ef

    if total_generation == 0:
        return None

    return round(total_co2 / total_generation)


def check_carbon_intensity(zone, max_carbon, eia_api_key=""):
    """Check carbon intensity using the EIA API (hourly fuel mix).

    Returns (is_green, intensity) or (None, None) on error.
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

    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


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
