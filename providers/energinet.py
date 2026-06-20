"""Denmark grid carbon intensity via Energinet's open Energi Data Service.

Free, no API key. Energinet publishes 5-minute CO2 emission intensity
(gCO2/kWh) for the two Danish price areas: DK1 (west) and DK2 (east).

Zones: DK-DK1, DK-DK2 (DK1 / DK2 also accepted).
"""

import json
from urllib.parse import quote

from providers.base import compute_trend, green_result, request

API = "https://api.energidataservice.dk/dataset/CO2Emis"
ENERGINET_ZONES = {"DK-DK1", "DK-DK2", "DK1", "DK2"}
_AREA = {"DK-DK1": "DK1", "DK1": "DK1", "DK-DK2": "DK2", "DK2": "DK2"}


def _fetch(zone, limit=1):
    area = _AREA.get(zone, "DK1")
    filt = quote(json.dumps({"PriceArea": [area]}))
    url = f"{API}?limit={limit}&sort=Minutes5UTC%20DESC&filter={filt}"
    data = request(url, parse="json")
    if not data:
        return []
    return data.get("records") or []


def check_carbon_intensity(zone, max_carbon):
    """Check carbon intensity using Energinet. Returns (is_green, intensity)."""
    print(f"Checking carbon intensity for zone: {zone} (Energinet)...")
    records = _fetch(zone)
    value = records[0].get("CO2Emission") if records else None
    if value is None:
        print(f"::warning::No Energinet CO2 intensity for zone {zone}")
        return None, None
    return green_result(zone, round(float(value)), max_carbon)


def get_forecast(zone, max_carbon):
    """Energinet publishes actual CO2 intensity; it has no CO2 forecast. Returns None."""
    return None, None


def get_history_trend(zone):
    """Compute a recent trend from Energinet history, or None."""
    records = _fetch(zone, limit=12)  # newest first
    points = [
        round(float(r["CO2Emission"]))
        for r in reversed(records)
        if r.get("CO2Emission") is not None
    ]
    return compute_trend(points)
