"""Provider registry for carbon intensity data sources."""

PROVIDER_UK = "uk_carbon_intensity"
PROVIDER_EIA = "eia"
PROVIDER_ELECTRICITY_MAPS = "electricity_maps"

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
    {"zone": "CISO", "runner_label": "us-west"},       # California — solar peak midday
    {"zone": "BPAT", "runner_label": "us-northwest"},   # Pacific NW — hydroelectric
    # UK (Carbon Intensity API — no key needed)
    {"zone": "GB-16", "runner_label": "uk-scotland"},   # Scotland — wind
    # Global (Electricity Maps — free token needed)
    {"zone": "NO-NO1", "runner_label": "eu-norway"},    # Norway — hydroelectric
    {"zone": "SE-SE2", "runner_label": "eu-sweden"},    # Sweden — hydro + nuclear
    {"zone": "FR", "runner_label": "eu-france"},        # France — nuclear
    {"zone": "CA-QC", "runner_label": "ca-quebec"},     # Quebec — hydroelectric
]


def detect_provider(zone):
    """Auto-detect the provider based on zone identifier."""
    if zone in UK_REGION_IDS:
        return PROVIDER_UK
    if zone in EIA_BALANCING_AUTHORITIES:
        return PROVIDER_EIA
    # Everything else goes to Electricity Maps (global)
    return PROVIDER_ELECTRICITY_MAPS
