"""European grid carbon intensity via Fraunhofer ISE's Energy-Charts API.

Free, no API key. Energy-Charts publishes near-real-time CO2-equivalent
intensity (gCO2eq/kWh) and a forecast for many European countries, which makes
a large swathe of the EU carbon-aware with zero setup (no ENTSO-E token needed).

Zones are ISO country codes mapped to the API's lowercase country parameter.
ENTSO-E (with a token) still takes priority for these zones; this is the keyless
path when no token is configured.
"""

from datetime import datetime, timezone

from providers.base import compute_trend, request

API = "https://api.energy-charts.info/co2eq"

# Countries Energy-Charts covers, excluding those with a dedicated provider here
# (FR -> RTE, DK -> Energinet, GB -> UK, IE -> EirGrid).
ENERGY_CHARTS_ZONES = {
    "DE",
    "ES",
    "IT",
    "NL",
    "BE",
    "AT",
    "CH",
    "PL",
    "PT",
    "CZ",
    "FI",
    "GR",
    "HU",
    "RO",
    "SK",
    "SI",
    "BG",
    "HR",
    "EE",
    "LV",
    "LT",
    "LU",
}


def _fetch(zone):
    return request(f"{API}?country={zone.lower()}", parse="json") or {}


def _latest(values):
    for value in reversed(values or []):
        if value is not None:
            return value
    return None


def check_carbon_intensity(zone, max_carbon):
    """Check carbon intensity using Energy-Charts. Returns (is_green, intensity)."""
    print(f"Checking carbon intensity for zone: {zone} (Energy-Charts)...")
    value = _latest(_fetch(zone).get("co2eq"))
    if value is None:
        print(f"::warning::No Energy-Charts CO2 intensity for zone {zone}")
        return None, None
    intensity = round(float(value))
    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


def get_forecast(zone, max_carbon):
    """Find the next forecast slot at or below max_carbon. Returns (iso_time, value)."""
    data = _fetch(zone)
    times = data.get("unix_seconds") or []
    forecast = data.get("co2eq_forecast") or []
    now = datetime.now(timezone.utc).timestamp()
    for ts, value in zip(times, forecast):
        if value is None or ts < now:
            continue
        if round(float(value)) <= max_carbon:
            iso = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
            return iso, round(float(value))
    return "none_in_forecast", None


def get_history_trend(zone):
    """Compute a recent trend from the Energy-Charts series, or None."""
    series = _fetch(zone).get("co2eq") or []
    points = [round(float(v)) for v in series[-12:] if v is not None]
    return compute_trend(points)
