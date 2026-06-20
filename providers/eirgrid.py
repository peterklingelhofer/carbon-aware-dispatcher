"""Ireland grid carbon intensity via EirGrid's Smart Grid Dashboard.

Free, no API key. EirGrid publishes live CO2 intensity (gCO2/kWh) for the
Republic of Ireland, Northern Ireland, and the all-island system, updated about
every 15 minutes.

Zones:
  IE / IE-ROI  Republic of Ireland
  IE-NI        Northern Ireland
  IE-ALL       all-island system

The dashboard endpoint returns {"Rows": [{"EffectiveTime", "FieldName",
"Region", "Value"}, ...]} in chronological order; we take the latest non-null
Value.
"""

from datetime import datetime, timedelta, timezone

from providers.base import compute_trend, green_result, request

API_BASE = "https://www.smartgriddashboard.com/DashboardService.svc/data"
EIRGRID_ZONES = {"IE", "IE-ROI", "IE-NI", "IE-ALL"}
_REGION = {"IE": "ROI", "IE-ROI": "ROI", "IE-NI": "NI", "IE-ALL": "ALL"}
# The dashboard service replies only to browser-like requests.
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _fetch_rows(zone, hours=6):
    region = _REGION.get(zone, "ROI")
    now = datetime.now(timezone.utc)
    frm = (now - timedelta(hours=hours)).strftime("%d-%b-%Y")
    to = now.strftime("%d-%b-%Y")
    url = f"{API_BASE}?area=co2intensity&region={region}&datefrom={frm}+00%3A00&dateto={to}+23%3A59"
    data = request(url, headers=_HEADERS, parse="json")
    if not data:
        return []
    return data.get("Rows") or []


def _latest_value(rows):
    """Return the most recent non-null CO2 intensity from chronological rows."""
    value = None
    for row in rows:
        if row.get("Value") is not None:
            value = row["Value"]
    return value


def check_carbon_intensity(zone, max_carbon):
    """Check carbon intensity using EirGrid. Returns (is_green, intensity)."""
    region = _REGION.get(zone, "ROI")
    print(f"Checking carbon intensity for zone: {zone} (EirGrid {region})...")
    value = _latest_value(_fetch_rows(zone))
    if value is None:
        print(f"::warning::No EirGrid CO2 intensity for zone {zone}")
        return None, None
    return green_result(zone, round(float(value)), max_carbon)


def get_forecast(zone, max_carbon):
    """EirGrid publishes actual CO2 intensity; it has no CO2 forecast. Returns None."""
    return None, None


def get_history_trend(zone):
    """Compute a recent trend from EirGrid history, or None."""
    rows = _fetch_rows(zone, hours=12)
    points = [round(float(r["Value"])) for r in rows if r.get("Value") is not None]
    return compute_trend(points)
