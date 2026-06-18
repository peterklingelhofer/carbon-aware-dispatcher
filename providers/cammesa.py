"""Argentina grid carbon intensity via CAMMESA's public generation feed.

Free, no API key. CAMMESA publishes 5-minute generation by category (hydro,
thermal, nuclear, renewable, imports) for the Argentine interconnected system
(SADI). We weight each category by a representative IPCC AR5 lifecycle factor to
estimate gCO2eq/kWh.

Caveats: "termico" lumps gas/oil/coal; Argentina's thermal
fleet is gas-dominated, so we use the gas factor as a proxy. "renovable" is
wind/solar-dominated. Imports are excluded from the calculation since their
generation source is unknown.

Zone: AR (national).
"""

from providers.base import FUEL_FACTORS, compute_trend, request

API = "https://api.cammesa.com/demanda-svc/generacion/ObtieneGeneracioEnergiaPorRegion"
CAMMESA_ZONES = {"AR"}
_REGION = {"AR": 1002}
_HEADERS = {"Accept": "application/json", "Referer": "https://cammesaweb.cammesa.com/"}

# CAMMESA category -> representative gCO2eq/kWh (IPCC AR5 lifecycle).
_FACTORS = {
    "hidraulico": FUEL_FACTORS.get("hydro", 24),
    "termico": FUEL_FACTORS.get("gas", 490),  # gas-dominated thermal fleet
    "nuclear": FUEL_FACTORS.get("nuclear", 12),
    "renovable": 25,  # wind/solar-dominated mix
}


def _intensity(record):
    """Weight the category mix into a gCO2eq/kWh estimate, or None."""
    numerator = denominator = 0.0
    for category, factor in _FACTORS.items():
        megawatts = record.get(category)
        if megawatts is None:
            continue
        megawatts = max(0.0, float(megawatts))
        numerator += megawatts * factor
        denominator += megawatts
    if denominator <= 0:
        return None
    return round(numerator / denominator)


def _latest(records):
    """Most recent record with positive total generation, or None."""
    for record in reversed(records or []):
        if record.get("sumTotal"):
            return record
    return None


def _fetch(zone):
    region = _REGION.get(zone, 1002)
    data = request(f"{API}?id_region={region}", headers=_HEADERS, parse="json")
    return data if isinstance(data, list) else []


def check_carbon_intensity(zone, max_carbon):
    """Check carbon intensity using CAMMESA. Returns (is_green, intensity)."""
    print(f"Checking carbon intensity for zone: {zone} (CAMMESA Argentina)...")
    record = _latest(_fetch(zone))
    intensity = _intensity(record) if record else None
    if intensity is None:
        print(f"::warning::No CAMMESA generation data for zone {zone}")
        return None, None
    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


def get_forecast(zone, max_carbon):
    """CAMMESA publishes actual generation; it has no CO2 forecast. Returns None."""
    return None, None


def get_history_trend(zone):
    """Compute a recent trend from CAMMESA history, or None."""
    records = _fetch(zone)
    points = [i for i in (_intensity(r) for r in records[-24:]) if i is not None]
    return compute_trend(points)
