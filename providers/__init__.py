"""Provider registry for carbon intensity data sources."""

PROVIDER_UK = "uk_carbon_intensity"
PROVIDER_EIA = "eia"
PROVIDER_ELECTRICITY_MAPS = "electricity_maps"
PROVIDER_AEMO = "aemo"
PROVIDER_ENTSOE = "entsoe"
PROVIDER_OPEN_METEO = "open_meteo"
PROVIDER_GRID_INDIA = "grid_india"
PROVIDER_ONS_BRAZIL = "ons_brazil"
PROVIDER_ESKOM = "eskom"

# Australian NEM region codes (free, no API key)
AEMO_ZONE_IDS = {"AU-NSW", "AU-QLD", "AU-VIC", "AU-SA", "AU-TAS"}

# Indian grid region codes (free, no API key)
GRID_INDIA_ZONE_IDS = {"IN-NO", "IN-SO", "IN-EA", "IN-WE", "IN-NE"}

# Brazilian grid region codes (free, no API key)
ONS_BRAZIL_ZONE_IDS = {"BR-S", "BR-SE", "BR-CS", "BR-NE", "BR-N"}

# South Africa (free, no API key)
ESKOM_ZONE_IDS = {"ZA"}

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


# auto:green — Curated green-energy zones that work WITHOUT any API keys.
# All zones here use free providers (EIA, UK, AEMO, Grid India, ONS Brazil).
# Spans multiple time zones so at least one is likely green at any given time.
AUTO_GREEN_ZONES = [
    # US (EIA — no key needed)
    #                                                     utc_offset  energy_type
    {"zone": "CISO", "runner_label": "us-west",           "utc_offset": -8, "type": "solar"},
    {"zone": "BPAT", "runner_label": "us-northwest",      "utc_offset": -8, "type": "hydro"},
    {"zone": "SCL", "runner_label": "us-seattle",         "utc_offset": -8, "type": "hydro"},
    # UK (Carbon Intensity API — no key needed)
    {"zone": "GB-16", "runner_label": "uk-scotland",      "utc_offset": 0,  "type": "wind"},
    {"zone": "GB", "runner_label": "uk-national",         "utc_offset": 0,  "type": "wind"},
    # Australia (AEMO — no key needed)
    {"zone": "AU-TAS", "runner_label": "oc-tasmania",     "utc_offset": 10, "type": "hydro"},
    {"zone": "AU-SA", "runner_label": "au-south",         "utc_offset": 9.5, "type": "wind"},
    # India (Grid India — no key needed)
    {"zone": "IN-SO", "runner_label": "in-south",         "utc_offset": 5.5, "type": "solar"},
    # Brazil (ONS — no key needed)
    {"zone": "BR-S", "runner_label": "br-south",          "utc_offset": -3, "type": "hydro"},
    {"zone": "BR-NE", "runner_label": "br-northeast",     "utc_offset": -3, "type": "wind"},
]

# auto:green:full — Extended green-energy zones INCLUDING token-requiring zones.
# Requires electricity_maps_token or entsoe_token for non-free zones.
# Use this when you have API tokens configured for maximum global coverage.
AUTO_GREEN_ZONES_FULL = [
    # All free zones from auto:green
    {"zone": "CISO", "runner_label": "us-west",           "utc_offset": -8, "type": "solar"},
    {"zone": "BPAT", "runner_label": "us-northwest",      "utc_offset": -8, "type": "hydro"},
    {"zone": "SCL", "runner_label": "us-seattle",         "utc_offset": -8, "type": "hydro"},
    {"zone": "GB-16", "runner_label": "uk-scotland",      "utc_offset": 0,  "type": "wind"},
    {"zone": "GB", "runner_label": "uk-national",         "utc_offset": 0,  "type": "wind"},
    {"zone": "AU-TAS", "runner_label": "oc-tasmania",     "utc_offset": 10, "type": "hydro"},
    {"zone": "AU-SA", "runner_label": "au-south",         "utc_offset": 9.5, "type": "wind"},
    {"zone": "IN-SO", "runner_label": "in-south",         "utc_offset": 5.5, "type": "solar"},
    {"zone": "BR-S", "runner_label": "br-south",          "utc_offset": -3, "type": "hydro"},
    {"zone": "BR-NE", "runner_label": "br-northeast",     "utc_offset": -3, "type": "wind"},
    # Europe (ENTSO-E or Electricity Maps — token needed)
    {"zone": "NO-NO1", "runner_label": "eu-norway",       "utc_offset": 1,  "type": "hydro"},
    {"zone": "SE-SE2", "runner_label": "eu-sweden",       "utc_offset": 1,  "type": "hydro"},
    {"zone": "FR", "runner_label": "eu-france",           "utc_offset": 1,  "type": "nuclear"},
    {"zone": "IS", "runner_label": "eu-iceland",          "utc_offset": 0,  "type": "hydro"},
    # Americas (Electricity Maps — token needed)
    {"zone": "CA-QC", "runner_label": "ca-quebec",        "utc_offset": -5, "type": "hydro"},
    {"zone": "CA-BC", "runner_label": "ca-bc",            "utc_offset": -8, "type": "hydro"},
    {"zone": "UY", "runner_label": "sa-uruguay",          "utc_offset": -3, "type": "wind"},
    {"zone": "PY", "runner_label": "sa-paraguay",         "utc_offset": -4, "type": "hydro"},
    {"zone": "CR", "runner_label": "ca-costarica",        "utc_offset": -6, "type": "hydro"},
    # Oceania (Electricity Maps — token needed)
    {"zone": "NZ-NZN", "runner_label": "oc-newzealand",   "utc_offset": 12, "type": "hydro"},
]

# auto:cleanest — ALL free-provider zones for zero-config global routing.
# Checks a smart subset of zones from every free provider (no API keys needed).
# Picks the zone with the lowest carbon intensity worldwide.
AUTO_CLEANEST_ZONES = [
    # US (EIA — no key needed) — most likely clean regions
    {"zone": "CISO", "runner_label": None, "utc_offset": -8, "type": "solar"},
    {"zone": "BPAT", "runner_label": None, "utc_offset": -8, "type": "hydro"},
    {"zone": "SCL", "runner_label": None, "utc_offset": -8, "type": "hydro"},
    {"zone": "NYIS", "runner_label": None, "utc_offset": -5, "type": "hydro"},
    {"zone": "ISNE", "runner_label": None, "utc_offset": -5, "type": "nuclear"},
    # UK (Carbon Intensity API — no key needed)
    {"zone": "GB-16", "runner_label": None, "utc_offset": 0, "type": "wind"},
    {"zone": "GB", "runner_label": None, "utc_offset": 0, "type": "wind"},
    # Australia (AEMO — no key needed)
    {"zone": "AU-TAS", "runner_label": None, "utc_offset": 10, "type": "hydro"},
    {"zone": "AU-SA", "runner_label": None, "utc_offset": 9.5, "type": "wind"},
    {"zone": "AU-VIC", "runner_label": None, "utc_offset": 10, "type": "wind"},
    # India (Grid India — no key needed)
    {"zone": "IN-SO", "runner_label": None, "utc_offset": 5.5, "type": "solar"},
    {"zone": "IN-WE", "runner_label": None, "utc_offset": 5.5, "type": "solar"},
    # Brazil (ONS — no key needed)
    {"zone": "BR-S", "runner_label": None, "utc_offset": -3, "type": "hydro"},
    {"zone": "BR-NE", "runner_label": None, "utc_offset": -3, "type": "wind"},
    # Note: ZA (South Africa) intentionally excluded — ~85% coal, ~750 gCO2eq/kWh.
    # Use auto:escape-coal:ZA to route away from SA's dirty grid.
    # Open-Meteo estimates (no key needed) — clean regions globally
    {"zone": "IS", "runner_label": None, "utc_offset": 0, "type": "hydro"},
    {"zone": "KE", "runner_label": None, "utc_offset": 3, "type": "hydro"},
]

# auto:escape-coal — Dirty-grid presets.
# Maps dirty-grid countries to their closest clean alternatives.
# Users in coal-heavy countries can use "auto:escape-coal" to route
# their CI jobs to the nearest clean energy region.
ESCAPE_COAL_MAPPINGS = {
    # India → clean alternatives (closest first)
    "IN": ["IS", "NO-NO1", "FR", "SE-SE2", "CISO", "BPAT"],
    "IN-NO": ["IS", "NO-NO1", "FR", "SE-SE2"],
    "IN-SO": ["IS", "NO-NO1", "FR", "AU-TAS"],
    "IN-EA": ["IS", "NO-NO1", "FR", "SE-SE2"],
    "IN-WE": ["IS", "NO-NO1", "FR", "SE-SE2"],
    "IN-NE": ["IS", "NO-NO1", "SE-SE2", "AU-TAS"],
    # China → Pacific clean regions
    "CN": ["NZ-NZN", "AU-TAS", "BPAT", "CISO", "CA-BC"],
    "CN-BJ": ["NZ-NZN", "AU-TAS", "BPAT", "CISO"],
    "CN-SH": ["NZ-NZN", "AU-TAS", "BPAT", "CISO"],
    "CN-GD": ["NZ-NZN", "AU-TAS", "BPAT"],
    # Poland → Nordic clean
    "PL": ["NO-NO1", "SE-SE2", "FR", "GB-16", "IS"],
    # Germany → Nordic clean
    "DE": ["NO-NO1", "SE-SE2", "FR", "AT", "GB-16"],
    # South Africa → Europe/Iceland clean
    "ZA": ["IS", "NO-NO1", "FR", "SE-SE2", "GB-16"],
    # Australia (coal states) → Tasmania/NZ
    "AU-NSW": ["AU-TAS", "NZ-NZN", "BPAT", "CISO"],
    "AU-QLD": ["AU-TAS", "NZ-NZN", "BPAT", "CISO"],
    "AU-VIC": ["AU-TAS", "NZ-NZN", "BPAT"],
    # US coal regions → US clean regions
    "PJM": ["BPAT", "CISO", "NYIS", "GB-16"],
    "MISO": ["BPAT", "CISO", "NYIS", "GB-16"],
    # Japan → Pacific clean
    "JP-TK": ["NZ-NZN", "AU-TAS", "BPAT", "CISO"],
    # South Korea → Pacific clean
    "KR": ["NZ-NZN", "AU-TAS", "BPAT", "CISO"],
    # Indonesia → Pacific clean
    "ID": ["NZ-NZN", "AU-TAS", "BPAT"],
}

# Default escape-coal zones when no specific dirty zone is given.
# A curated set of the world's cleanest grids.
AUTO_ESCAPE_COAL_ZONES = [
    {"zone": "IS", "runner_label": None, "utc_offset": 0, "type": "hydro"},
    {"zone": "NO-NO1", "runner_label": None, "utc_offset": 1, "type": "hydro"},
    {"zone": "SE-SE2", "runner_label": None, "utc_offset": 1, "type": "hydro"},
    {"zone": "FR", "runner_label": None, "utc_offset": 1, "type": "nuclear"},
    {"zone": "CISO", "runner_label": None, "utc_offset": -8, "type": "solar"},
    {"zone": "BPAT", "runner_label": None, "utc_offset": -8, "type": "hydro"},
    {"zone": "CA-QC", "runner_label": None, "utc_offset": -5, "type": "hydro"},
    {"zone": "AU-TAS", "runner_label": None, "utc_offset": 10, "type": "hydro"},
    {"zone": "NZ-NZN", "runner_label": None, "utc_offset": 12, "type": "hydro"},
    {"zone": "GB-16", "runner_label": None, "utc_offset": 0, "type": "wind"},
    {"zone": "BR-S", "runner_label": None, "utc_offset": -3, "type": "hydro"},
    {"zone": "UY", "runner_label": None, "utc_offset": -3, "type": "wind"},
    {"zone": "CR", "runner_label": None, "utc_offset": -6, "type": "hydro"},
    {"zone": "PY", "runner_label": None, "utc_offset": -4, "type": "hydro"},
]


# auto:nearest — Map UTC offsets to the closest green zones.
# Used when auto:nearest detects TZ env var or system timezone.
NEAREST_ZONES_BY_OFFSET = {
    # UTC-10 to -9: Hawaii/Alaska → US West
    -10: ["CISO", "BPAT", "SCL"],
    -9: ["CISO", "BPAT", "SCL"],
    # UTC-8 to -7: US Pacific / Mountain → US West
    -8: ["CISO", "BPAT", "SCL"],
    -7: ["CISO", "BPAT", "SCL"],
    # UTC-6: US Central / Mexico → US West + South
    -6: ["CISO", "BPAT", "ISNE", "NYIS"],
    # UTC-5: US Eastern / Colombia / Peru → US East + Quebec
    -5: ["NYIS", "ISNE", "BPAT", "BR-S"],
    # UTC-4: Canada Atlantic / Venezuela / Bolivia
    -4: ["NYIS", "ISNE", "BR-S", "BR-NE"],
    # UTC-3: Brazil / Argentina / Uruguay
    -3: ["BR-S", "BR-NE", "NYIS", "ISNE"],
    # UTC-2 to -1: Mid-Atlantic
    -2: ["BR-S", "BR-NE", "IS", "GB-16"],
    -1: ["IS", "GB-16", "GB", "BR-NE"],
    # UTC+0: UK / Iceland / West Africa
    0: ["GB-16", "GB", "IS", "CISO"],
    # UTC+1: Western/Central Europe
    1: ["GB-16", "GB", "IS", "CISO", "IN-SO"],
    # UTC+2: Eastern Europe / South Africa
    2: ["GB-16", "GB", "IS", "IN-SO"],
    # UTC+3: East Africa / Middle East / Russia West
    3: ["IN-SO", "GB-16", "GB", "IS"],
    # UTC+4: Gulf / Russia
    4: ["IN-SO", "IN-WE", "GB-16", "AU-TAS"],
    # UTC+5 to 5.5: Pakistan / India
    5: ["IN-SO", "IN-WE", "AU-TAS", "AU-SA"],
    5.5: ["IN-SO", "IN-WE", "AU-TAS", "AU-SA"],
    # UTC+6 to 7: Bangladesh / SE Asia
    6: ["IN-SO", "AU-TAS", "AU-SA", "AU-VIC"],
    7: ["AU-TAS", "AU-SA", "AU-VIC", "IN-SO"],
    # UTC+8: China / Singapore / Australia West
    8: ["AU-TAS", "AU-SA", "AU-VIC", "IN-SO"],
    # UTC+9 to 9.5: Japan / Korea / Australia Central
    9: ["AU-TAS", "AU-SA", "AU-VIC", "AU-NSW"],
    9.5: ["AU-SA", "AU-TAS", "AU-VIC"],
    # UTC+10 to 11: Australia East / Pacific
    10: ["AU-TAS", "AU-VIC", "AU-SA", "AU-NSW"],
    11: ["AU-TAS", "AU-VIC", "AU-NSW"],
    # UTC+12 to 13: NZ / Pacific Islands
    12: ["AU-TAS", "AU-VIC", "AU-SA"],
    13: ["AU-TAS", "AU-VIC", "AU-SA"],
}


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
    """Auto-detect the best provider for a zone.

    Priority chain (free providers first, then token-based, then fallback):
    1. UK Carbon Intensity API (no key)
    2. EIA (no key needed, DEMO_KEY built-in)
    3. AEMO Australia (no key)
    4. Grid India (no key)
    5. ONS Brazil (no key)
    6. Eskom South Africa (no key)
    7. ENTSO-E (free token required)
    8. Open-Meteo (free, no key — if zone has known coordinates)
    9. Electricity Maps (free token required — catch-all)
    """
    from providers.entsoe import ENTSOE_AREA_CODES
    from providers.open_meteo import ZONE_COORDINATES

    if zone in UK_REGION_IDS:
        return PROVIDER_UK
    if zone in EIA_BALANCING_AUTHORITIES:
        return PROVIDER_EIA
    if zone in AEMO_ZONE_IDS:
        return PROVIDER_AEMO
    if zone in GRID_INDIA_ZONE_IDS:
        return PROVIDER_GRID_INDIA
    if zone in ONS_BRAZIL_ZONE_IDS:
        return PROVIDER_ONS_BRAZIL
    if zone in ESKOM_ZONE_IDS:
        return PROVIDER_ESKOM
    if entsoe_token and zone in ENTSOE_AREA_CODES:
        return PROVIDER_ENTSOE
    # Open-Meteo for zones with known coordinates (free, no key)
    if zone in ZONE_COORDINATES:
        return PROVIDER_OPEN_METEO
    # Electricity Maps as final catch-all (requires token)
    return PROVIDER_ELECTRICITY_MAPS
