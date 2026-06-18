"""Carbon-aware inference routing.

AI inference is a fast-growing, increasingly latency-tolerant load (batch and
async requests especially). Routing each request to the cleanest available
endpoint, in real time, cuts emissions on a load class that is scaling far faster
than CI.

Give it your candidate endpoints (each tagged with the grid zone its datacenter
sits in); it returns them ranked by live carbon intensity, cleanest first.

    from integrations.inference_router import cleanest_endpoint
    endpoints = [
        {"name": "us-west", "zone": "CISO", "url": "https://us-west..."},
        {"name": "france",  "zone": "FR",   "url": "https://eu..."},
        {"name": "norway",  "zone": "NO-NO1", "url": "https://no..."},
    ]
    target = cleanest_endpoint(endpoints)   # route this request there
"""

import check_grid

# A high threshold so every reachable zone is measured, then ranked by intensity.
_MEASURE_CEILING = 100_000


def _zone_intensities(zones, tokens):
    """Measure each unique zone once; return {zone: intensity}."""
    unique = sorted({z for z in zones})
    measured = []
    check_grid.check_multiple_zones(
        [{"zone": z} for z in unique],
        _MEASURE_CEILING,
        tokens.get("eia", ""),
        tokens.get("emaps", ""),
        tokens.get("entsoe", ""),
        collect=measured,
    )
    by_zone = {}
    for zone, intensity in measured:
        # keep the cleanest reading if a zone somehow reports more than once
        if zone not in by_zone or intensity < by_zone[zone]:
            by_zone[zone] = intensity
    return by_zone


def rank_endpoints(candidates, tokens=None):
    """Rank endpoints by their zone's live carbon intensity, cleanest first.

    candidates: list of dicts each with at least a "zone" key. Returns copies with
    an "intensity" field added (None when unreadable); unreadable endpoints sort
    last so a usable endpoint is always preferred.
    """
    tokens = tokens or {}
    by_zone = _zone_intensities([c["zone"] for c in candidates], tokens)
    ranked = [dict(c, intensity=by_zone.get(c["zone"])) for c in candidates]
    ranked.sort(key=lambda c: (c["intensity"] is None, c["intensity"] or 0))
    return ranked


def cleanest_endpoint(candidates, tokens=None):
    """Return the single cleanest endpoint with a live reading, or None."""
    for endpoint in rank_endpoints(candidates, tokens):
        if endpoint.get("intensity") is not None:
            return endpoint
    return None
