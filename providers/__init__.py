"""Provider registry for carbon intensity data sources."""

PROVIDER_UK = "uk_carbon_intensity"
PROVIDER_EIA = "eia"
PROVIDER_ELECTRICITY_MAPS = "electricity_maps"
PROVIDER_AEMO = "aemo"
PROVIDER_ENTSOE = "entsoe"
PROVIDER_OPEN_METEO = "open_meteo"

# Australian NEM region codes (free, no API key)
AEMO_ZONE_IDS = {"AU-NSW", "AU-QLD", "AU-VIC", "AU-SA", "AU-TAS"}

# UK Carbon Intensity API region IDs
UK_REGION_IDS = {
    "GB": None,  # National
    "GB-national": None,
    "GB-1": 1, "North Scotland": 1,
    "GB-2": 2, "South Scotland": 2,
    "GB-3": 3, "North West England": 3,
    "GB-4": 4, "North East England": 4,
    "GB-5": 5, "Yorkshire": 5,
    "GB-6": 6, "North Wales": 6,
    "GB-7": 7, "South Wales": 7,
    "GB-8": 8, "West Midlands": 8,
    "GB-9": 9, "East Midlands": 9,
    "GB-10": 10, "East England": 10,
    "GB-11": 11, "South West England": 11,
    "GB-12": 12, "South England": 12,
    "GB-13": 13, "London": 13,
    "GB-14": 14, "South East England": 14,
    "GB-15": 15, "England": 15,
    "GB-16": 16, "Scotland": 16,
    "GB-17": 17, "Wales": 17,
}

# EIA Balancing Authority codes (US grid regions)
EIA_BALANCING_AUTHORITIES = {
    # Major ISOs
    "CISO", "ERCO", "PJM", "NYIS", "MISO", "ISNE", "SWPP", "SPA",
    # Regions
    "CAL", "CAR", "CENT", "FLA", "MIDA", "MIDW", "NE", "NY", "NW",
    "SE", "SW", "TEX",
    # Other BAs
    "AEC", "AECI", "AVA", "AZPS", "BANC", "BPAT", "CHPD", "CPLE",
    "CPLW", "DEAA", "DOPD", "DUK", "EEI", "EPE", "FMPP",
    "FPC", "FPL", "GCPD", "GVL", "HST", "IID", "IPCO", "JEA",
    "LDWP", "LGEE", "NEVP", "NSB", "NWMT", "PACE",
    "PACW", "PGE", "PNM", "PSCO", "PSEI", "SC", "SCEG", "SCL",
    "SEC", "SEPA", "SOCO", "SRP", "TAL", "TEC",
    "TEPC", "TIDC", "TPWR", "TVA", "WACM", "WALC", "WAUW", "YAD",
    # Canadian (also in EIA)
    "IESO", "AESO",
}


# Curated list of zones that are frequently powered by clean energy.
# Spans multiple time zones so at least one is likely green at any given time.
# US/UK zones work without API keys; global zones need an Electricity Maps token.
AUTO_GREEN_ZONES = [
    # US (EIA — no key needed)
    #                                                     utc_offset  energy_type
    {"zone": "CISO", "runner_label": "us-west",           "utc_offset": -8, "type": "solar"},
    {"zone": "BPAT", "runner_label": "us-northwest",      "utc_offset": -8, "type": "hydro"},
    # UK (Carbon Intensity API — no key needed)
    {"zone": "GB-16", "runner_label": "uk-scotland",      "utc_offset": 0,  "type": "wind"},
    # Europe (Electricity Maps — free token needed)
    {"zone": "NO-NO1", "runner_label": "eu-norway",       "utc_offset": 1,  "type": "hydro"},
    {"zone": "SE-SE2", "runner_label": "eu-sweden",       "utc_offset": 1,  "type": "hydro"},
    {"zone": "FR", "runner_label": "eu-france",           "utc_offset": 1,  "type": "nuclear"},
    {"zone": "IS", "runner_label": "eu-iceland",          "utc_offset": 0,  "type": "hydro"},
    # Americas (Electricity Maps — free token needed)
    {"zone": "CA-QC", "runner_label": "ca-quebec",        "utc_offset": -5, "type": "hydro"},
    {"zone": "CA-BC", "runner_label": "ca-bc",            "utc_offset": -8, "type": "hydro"},
    {"zone": "BR-S", "runner_label": "br-south",          "utc_offset": -3, "type": "hydro"},
    {"zone": "UY", "runner_label": "sa-uruguay",          "utc_offset": -3, "type": "wind"},
    {"zone": "PY", "runner_label": "sa-paraguay",         "utc_offset": -4, "type": "hydro"},
    {"zone": "CR", "runner_label": "ca-costarica",        "utc_offset": -6, "type": "hydro"},
    # Oceania (Electricity Maps — free token needed)
    {"zone": "NZ-NZN", "runner_label": "oc-newzealand",   "utc_offset": 12, "type": "hydro"},
    {"zone": "AU-TAS", "runner_label": "oc-tasmania",     "utc_offset": 10, "type": "hydro"},
]


def _time_priority_score(zone_entry, utc_hour):
    """Score a zone's likelihood of being green at the given UTC hour.

    Higher score = more likely to be green right now.
    Solar zones score high during their local daytime (10am-4pm).
    Hydro/nuclear zones score consistently high (always-on).
    Wind zones score slightly higher at night (statistically windier).
    """
    energy_type = zone_entry.get("type", "hydro")
    offset = zone_entry.get("utc_offset", 0)
    local_hour = (utc_hour + offset) % 24

    if energy_type == "solar":
        # Peak: 10am-4pm local. Ramp: 8-10am and 4-6pm. Low: night.
        if 10 <= local_hour <= 16:
            return 100  # Peak solar
        if 8 <= local_hour <= 18:
            return 60   # Shoulder hours
        return 10       # Night — solar is offline

    if energy_type == "hydro" or energy_type == "nuclear":
        # Always-on, slight preference for off-peak (lower demand = higher % renewable)
        if 22 <= local_hour or local_hour <= 6:
            return 85   # Off-peak
        return 80       # On-peak (still very green)

    if energy_type == "wind":
        # Wind is statistically stronger at night in many regions
        if 20 <= local_hour or local_hour <= 8:
            return 75   # Night wind
        return 55       # Day (less reliable)

    return 50  # Unknown type


def sort_auto_green_by_time(zones, utc_hour):
    """Sort auto:green zones by time-of-day priority.

    Returns a new list sorted by descending priority score.
    """
    scored = [(z, _time_priority_score(z, utc_hour)) for z in zones]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [z for z, _ in scored]


def detect_provider(zone, entsoe_token=""):
    """Auto-detect the provider based on zone identifier.

    If entsoe_token is set and the zone is in ENTSO-E's coverage,
    ENTSO-E is preferred over Electricity Maps for EU zones.
    """
    from providers.entsoe import ENTSOE_AREA_CODES

    if zone in UK_REGION_IDS:
        return PROVIDER_UK
    if zone in EIA_BALANCING_AUTHORITIES:
        return PROVIDER_EIA
    if zone in AEMO_ZONE_IDS:
        return PROVIDER_AEMO
    if entsoe_token and zone in ENTSOE_AREA_CODES:
        return PROVIDER_ENTSOE
    # Electricity Maps if token available, otherwise Open-Meteo fallback
    return PROVIDER_ELECTRICITY_MAPS
