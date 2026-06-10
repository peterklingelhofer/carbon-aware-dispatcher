"""Canadian grid carbon data provider, free, no API key.

Three provinces, three different public feeds:
  CA-ON  IESO  generation-by-fuel hourly XML (reports-public.ieso.ca)
  CA-AB  AESO  Current Supply Demand HTML report (ets.aeso.ca)
  CA-QC        Hydro-Quebec is ~99% hydro with no real-time fuel feed, so it
               is a fixed low-carbon estimate rather than a measurement.
"""

import re
import xml.etree.ElementTree as ET

from providers.base import DEFAULT_FUEL_FACTOR, request

CANADA_ZONES = {"CA-ON", "CA-AB", "CA-QC"}

IESO_URL = (
    "https://reports-public.ieso.ca/public/GenOutputbyFuelHourly/PUB_GenOutputbyFuelHourly.xml"
)
AESO_URL = "http://ets.aeso.ca/ets_web/ip/Market/Reports/CSDReportServlet?contentType=html"

# IPCC AR5 (2014) lifecycle median gCO2eq/kWh
CANADA_EMISSION_FACTORS = {
    "coal": 820,
    "natural_gas": 490,
    "oil": 650,
    "hydro": 24,
    "wind": 12,
    "solar": 45,
    "nuclear": 12,
    "biomass": 230,
    "other": 300,
}

# Storage is not primary generation, exclude from the weighted mix
CANADA_STORAGE_FUELS = {"battery"}

_IESO_FUEL_MAP = {
    "NUCLEAR": "nuclear",
    "GAS": "natural_gas",
    "HYDRO": "hydro",
    "WIND": "wind",
    "SOLAR": "solar",
    "BIOFUEL": "biomass",
}

_AESO_FUEL_MAP = {
    "COAL": "coal",
    # Alberta reports gas under several plant-type labels, all gas-fired
    "GAS": "natural_gas",
    "COGENERATION": "natural_gas",
    "COMBINED CYCLE": "natural_gas",
    "SIMPLE CYCLE": "natural_gas",
    "GAS FIRED STEAM": "natural_gas",
    "DUAL FUEL": "natural_gas",
    "HYDRO": "hydro",
    "WIND": "wind",
    "SOLAR": "solar",
    "ENERGY STORAGE": "battery",
    "OTHER": "other",
}

# AESO summary rows: <TR><TD>FUEL</TD><TD>MC</TD><TD>TNG</TD><TD>DCR</TD></TR>
# (MC = max capability, TNG = total net generation = what we want, DCR = reserve)
_AESO_ROW = re.compile(
    r"<TR>\s*<TD>([A-Z][A-Z ]+?)</TD>\s*<TD>(\d+)</TD>\s*<TD>(\d+)</TD>\s*<TD>(\d+)</TD>\s*</TR>"
)


def _localname(tag):
    return tag.rsplit("}", 1)[-1]


def _mix_to_intensity(fuel_mix):
    """Weighted carbon intensity from a {fuel: MW} dict, or None.

    Storage is excluded; unknown fuels warn then fall back to DEFAULT_FUEL_FACTOR.
    """
    total_gen = 0
    weighted = 0
    for fuel, mw in fuel_mix.items():
        if mw is None or mw <= 0:
            continue
        if fuel in CANADA_STORAGE_FUELS:
            continue
        if fuel in CANADA_EMISSION_FACTORS:
            factor = CANADA_EMISSION_FACTORS[fuel]
        else:
            print(
                f"::warning::Unknown fuel type '{fuel}', using fallback "
                f"{DEFAULT_FUEL_FACTOR} gCO2eq/kWh"
            )
            factor = DEFAULT_FUEL_FACTOR
        total_gen += mw
        weighted += mw * factor
    if total_gen <= 0:
        return None
    return round(weighted / total_gen)


def _parse_ieso(xml_text):
    """Parse IESO by-fuel XML into a {fuel: MW} mix for the latest hour."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    dailies = [e for e in root.iter() if _localname(e.tag) == "DailyData"]
    if not dailies:
        return None

    # Latest day, latest hour that actually has generation
    hours = [e for e in dailies[-1].iter() if _localname(e.tag) == "HourlyData"]
    for hourly in reversed(hours):
        fuel_mix = {}
        for ft in (e for e in hourly.iter() if _localname(e.tag) == "FuelTotal"):
            fuel_el = next((c for c in ft.iter() if _localname(c.tag) == "Fuel"), None)
            out_el = next((c for c in ft.iter() if _localname(c.tag) == "Output"), None)
            if fuel_el is None or out_el is None:
                continue
            norm = _IESO_FUEL_MAP.get((fuel_el.text or "").strip().upper())
            if norm is None:
                continue
            fuel_mix[norm] = fuel_mix.get(norm, 0) + float(out_el.text or 0)
        if sum(fuel_mix.values()) > 0:
            return fuel_mix
    return None


def _parse_aeso(html_text):
    """Parse AESO Current Supply Demand HTML into a {fuel: MW} mix."""
    fuel_mix = {}
    for name, _mc, tng, _dcr in _AESO_ROW.findall(html_text):
        norm = _AESO_FUEL_MAP.get(name.strip())
        if norm is None:
            continue
        fuel_mix[norm] = fuel_mix.get(norm, 0) + float(tng)
    return fuel_mix or None


def check_carbon_intensity(zone, max_carbon):
    """Check carbon intensity for a Canadian province.

    Returns (is_green, intensity) or (None, None) on error.
    """
    if zone == "CA-QC":
        # Hydro-Quebec is ~99% hydro/wind with no real-time fuel feed, so this
        # is an honest fixed estimate rather than a measurement
        intensity = 30
        is_green = intensity <= max_carbon
        status = "GREEN (estimated)" if is_green else "over threshold (estimated)"
        print(f"  Zone {zone}: ~{intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
        print("  (Hydro-Quebec is ~99% hydro, this is a fixed estimate)")
        return is_green, intensity

    if zone == "CA-ON":
        print(f"Checking carbon intensity for zone: {zone} (IESO Ontario)...")
        text = request(IESO_URL, parse="text")
        fuel_mix = _parse_ieso(text) if text else None
    elif zone == "CA-AB":
        print(f"Checking carbon intensity for zone: {zone} (AESO Alberta)...")
        text = request(AESO_URL, parse="text")
        fuel_mix = _parse_aeso(text) if text else None
    else:
        print(f"::warning::Unknown Canada zone: {zone}. Valid: {', '.join(sorted(CANADA_ZONES))}")
        return None, None

    if not fuel_mix:
        print(f"::warning::No generation data for zone {zone}")
        return None, None

    intensity = _mix_to_intensity(fuel_mix)
    if intensity is None:
        return None, None

    is_green = intensity <= max_carbon
    status = "GREEN" if is_green else "over threshold"
    print(f"  Zone {zone}: {intensity} gCO2eq/kWh ({status}, threshold: {max_carbon})")
    return is_green, intensity


def get_forecast(zone, max_carbon):
    """Canada feeds are current-snapshot only, no forecast endpoint.

    Returns (None, None).
    """
    return None, None


def get_history_trend(zone):
    """Canada feeds expose no history endpoint, so no trend is available.

    Returns None.
    """
    return None
