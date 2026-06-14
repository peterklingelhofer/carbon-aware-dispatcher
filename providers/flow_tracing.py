"""Consumption-based carbon intensity via electricity flow tracing (EU only).

Production-based intensity counts only what a zone *generates*. But grids import
and export power, so what a zone actually *consumes* can be cleaner or dirtier
than what it produces. Flow tracing (Tranberg et al., 2019) attributes emissions
across the interconnected network: the intensity of everything leaving a zone
equals the intensity of its whole consumed mix, so for every zone i

    c_i * (P_i + imports_i) = P_i * I_i + Sum_j  F_ji * c_j

where P_i is local generation, I_i its production intensity, and F_ji the power
flowing from j into i. That is a linear system A.c = b. The matrix is diagonally
dominant (the diagonal P_i + imports_i is >= the off-diagonal import sum), so
Gauss-Seidel iteration converges with no numpy needed.

Requires the free ENTSO-E token. Opt-in: the dispatcher only uses this when
consumption mode is enabled, so default behavior is unchanged.
"""

from datetime import datetime, timedelta, timezone

from providers.base import request
from providers.entsoe import (
    ENTSOE_API_BASE,
    ENTSOE_AREA_CODES,
    _parse_flow_latest,
    production_for_zone,
)

# A connected slice of the European grid covering the cloud-region zones plus the
# key neighbours they trade with, so imports are attributed to a real source
# rather than ignored. Larger sets mean more ENTSO-E calls; this is a pragmatic
# cut of the well-interconnected continental + GB/IE network.
TRACED_ZONES = [
    "FR",
    "DE",
    "NL",
    "BE",
    "CH",
    "AT",
    "ES",
    "PT",
    "IT-NO",
    "PL",
    "CZ",
    "GB",
    "IE",
    "DK-DK1",
]

# Undirected interconnector borders among TRACED_ZONES.
BORDERS = [
    ("FR", "DE"),
    ("FR", "BE"),
    ("FR", "CH"),
    ("FR", "ES"),
    ("FR", "IT-NO"),
    ("FR", "GB"),
    ("DE", "NL"),
    ("DE", "CH"),
    ("DE", "AT"),
    ("DE", "PL"),
    ("DE", "CZ"),
    ("DE", "DK-DK1"),
    ("NL", "BE"),
    ("NL", "GB"),
    ("CH", "AT"),
    ("CH", "IT-NO"),
    ("AT", "CZ"),
    ("AT", "IT-NO"),
    ("ES", "PT"),
    ("PL", "CZ"),
    ("GB", "IE"),
]


def trace_consumption_intensity(
    production_mw, production_intensity, flows_mw, *, max_iter=200, tol=1e-4
):
    """Solve for consumption-based intensity per zone.

    Args:
        production_mw: zone -> local generation (MW).
        production_intensity: zone -> production carbon intensity (gCO2eq/kWh).
        flows_mw: (from_zone, to_zone) -> power flowing from->to (MW, >= 0).
        max_iter / tol: Gauss-Seidel stopping conditions.

    Returns zone -> consumption-based intensity (gCO2eq/kWh). Only zones present
    in production_mw are returned; flows to/from unknown zones are ignored.
    """
    zones = list(production_mw)
    if not zones:
        return {}

    # imports_into[i] = list of (j, F_ji); inflow[i] = P_i + sum F_ji
    imports_into = {z: [] for z in zones}
    for (src, dst), mw in flows_mw.items():
        if mw <= 0 or src not in production_mw or dst not in production_mw:
            continue
        imports_into[dst].append((src, mw))

    inflow = {z: production_mw[z] + sum(mw for _, mw in imports_into[z]) for z in zones}

    # Initialise consumption intensity at production intensity, then relax
    c = {z: production_intensity.get(z, 0.0) for z in zones}
    for _ in range(max_iter):
        delta = 0.0
        for z in zones:
            denom = inflow[z]
            if denom <= 0:
                continue
            imported = sum(mw * c[src] for src, mw in imports_into[z])
            new_c = (production_mw[z] * production_intensity.get(z, 0.0) + imported) / denom
            delta = max(delta, abs(new_c - c[z]))
            c[z] = new_c
        if delta < tol:
            break

    return {z: round(c[z], 1) for z in zones}


def _flow(in_eic, out_eic, period_start, period_end, entsoe_token):
    """Latest physical flow out_eic -> in_eic (MW); 0 on any failure."""
    url = (
        f"{ENTSOE_API_BASE}?securityToken={entsoe_token}"
        f"&documentType=A11&in_Domain={in_eic}&out_Domain={out_eic}"
        f"&periodStart={period_start}&periodEnd={period_end}"
    )
    response = request(url, parse="response")
    if response is None or response.status_code != 200:
        return 0.0
    return _parse_flow_latest(response.text) or 0.0


def compute_consumption_intensities(entsoe_token):
    """Return zone -> consumption-based intensity (gCO2eq/kWh) for traced EU zones.

    Returns {} if no token, or if production data could not be fetched.
    """
    if not entsoe_token:
        return {}

    production_mw = {}
    production_intensity = {}
    for zone in TRACED_ZONES:
        intensity, total_mw = production_for_zone(zone, entsoe_token)
        if intensity is not None and total_mw:
            production_mw[zone] = float(total_mw)
            production_intensity[zone] = float(intensity)
    if not production_mw:
        return {}

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    period_start = (now - timedelta(hours=2)).strftime("%Y%m%d%H00")
    period_end = now.strftime("%Y%m%d%H00")

    flows = {}
    for a, b in BORDERS:
        if a not in production_mw or b not in production_mw:
            continue
        ea, eb = ENTSOE_AREA_CODES[a], ENTSOE_AREA_CODES[b]
        b_to_a = _flow(ea, eb, period_start, period_end, entsoe_token)  # b -> a
        a_to_b = _flow(eb, ea, period_start, period_end, entsoe_token)  # a -> b
        net = (b_to_a or 0.0) - (a_to_b or 0.0)  # net b -> a
        if net > 0:
            flows[(b, a)] = net
        elif net < 0:
            flows[(a, b)] = -net

    return trace_consumption_intensity(production_mw, production_intensity, flows)
