"""France grid carbon intensity via RTE eco2mix (ODRE open data, keyless).

Free, no API key. RTE publishes near-real-time national CO2 intensity
(taux_co2, gCO2/kWh) for mainland France through the ODRE open data platform.

Zone: FR.
"""

from providers.base import compute_trend, request

API = "https://odre.opendatasoft.com/api/records/1.0/search/"
RTE_ZONES = {"FR"}


def _fetch(rows=8):
    url = f"{API}?dataset=eco2mix-national-tr&rows={rows}&sort=-date_heure"
    data = request(url, parse="json")
    if not data:
        return []
    return data.get("records") or []


def _latest_taux(records):
    """Latest non-null taux_co2 from newest-first records, or None."""
    for rec in records:
        value = (rec.get("fields") or {}).get("taux_co2")
        if value is not None:
            return value
    return None


def check_carbon_intensity(zone, max_carbon):
    """Check carbon intensity using RTE eco2mix. Returns (is_green, intensity)."""
    print(f"Checking carbon intensity for zone: {zone} (RTE eco2mix)...")
    value = _latest_taux(_fetch())
    if value is None:
        print(f"::warning::No RTE CO2 intensity for zone {zone}")
        return None, None
    intensity = round(float(value))
    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


def get_forecast(zone, max_carbon):
    """RTE eco2mix publishes actual CO2 intensity; it has no CO2 forecast. Returns None."""
    return None, None


def get_history_trend(zone):
    """Compute a recent trend from RTE history, or None."""
    records = _fetch(rows=24)  # newest first
    points = [
        round(float((r.get("fields") or {}).get("taux_co2")))
        for r in reversed(records)
        if (r.get("fields") or {}).get("taux_co2") is not None
    ]
    return compute_trend(points)
