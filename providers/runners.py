"""Runner provider integrations — maps grid zones to cloud regions and runner labels.

Supports routing CI jobs to the greenest region by outputting provider-specific
runner labels that downstream jobs can use in their `runs-on:` field.

Currently supported providers:
  - runson: RunsOn (AWS) — full per-job region selection via labels
  - custom: User-provided labels via grid_zones input (always works)
"""

# ---------------------------------------------------------------------------
# Zone → cloud region mapping
# ---------------------------------------------------------------------------
# Maps grid zone identifiers to the nearest AWS region.
# Used by RunsOn and as a general-purpose cloud_region output.

ZONE_TO_AWS_REGION = {
    # US — EIA Balancing Authorities
    "CISO": "us-west-1",       # California ISO → N. California
    "BANC": "us-west-1",       # Sacramento
    "LDWP": "us-west-1",       # LA Dept of Water & Power
    "TIDC": "us-west-1",       # Turlock Irrigation District
    "IID": "us-west-1",        # Imperial Irrigation District
    "SRP": "us-west-2",        # Salt River Project (AZ, closer to Oregon)
    "AZPS": "us-west-2",       # Arizona Public Service
    "TEPC": "us-west-2",       # Tucson Electric
    "PNM": "us-west-2",        # New Mexico
    "EPE": "us-west-2",        # El Paso Electric
    "BPAT": "us-west-2",       # Bonneville Power → Oregon
    "SCL": "us-west-2",        # Seattle City Light
    "PSEI": "us-west-2",       # Puget Sound Energy
    "PACW": "us-west-2",       # PacifiCorp West
    "AVA": "us-west-2",        # Avista (WA/ID)
    "CHPD": "us-west-2",       # Chelan County PUD
    "DOPD": "us-west-2",       # Douglas County PUD
    "GCPD": "us-west-2",       # Grant County PUD
    "TPWR": "us-west-2",       # Tacoma Power
    "IPCO": "us-west-2",       # Idaho Power
    "NWMT": "us-west-2",       # NorthWestern Montana
    "PACE": "us-west-2",       # PacifiCorp East (UT/WY)
    "WACM": "us-west-2",       # Western Area (CO/WY)
    "PSCO": "us-west-2",       # Public Service CO
    "WALC": "us-west-2",       # Western Area (AZ/NV)
    "NEVP": "us-west-2",       # Nevada Power
    "WAUW": "us-west-2",       # Western Area Upper Great Plains
    "PJM": "us-east-1",        # PJM (Mid-Atlantic) → N. Virginia
    "NYIS": "us-east-1",       # NYISO → N. Virginia
    "ISNE": "us-east-1",       # ISO New England → N. Virginia
    "ERCO": "us-east-2",       # ERCOT (Texas) → Ohio (closest)
    "MISO": "us-east-2",       # MISO (Midwest) → Ohio
    "SWPP": "us-east-2",       # Southwest Power Pool → Ohio
    "SPA": "us-east-2",        # Southwestern Power Admin
    "AECI": "us-east-2",       # Associated Electric Coop
    "TVA": "us-east-1",        # Tennessee Valley Authority
    "SOCO": "us-east-1",       # Southern Company
    "DUK": "us-east-1",        # Duke Energy Carolinas
    "CPLE": "us-east-1",       # Duke Energy Progress East
    "CPLW": "us-east-1",       # Duke Energy Progress West
    "SCEG": "us-east-1",       # Dominion SC
    "SC": "us-east-1",         # South Carolina
    "FPL": "us-east-1",        # Florida Power & Light
    "FPC": "us-east-1",        # Duke Energy Florida
    "FMPP": "us-east-1",       # Florida Municipal Power Pool
    "GVL": "us-east-1",        # Gainesville Regional
    "HST": "us-east-1",        # Homestead
    "JEA": "us-east-1",        # Jacksonville Electric
    "NSB": "us-east-1",        # New Smyrna Beach
    "SEC": "us-east-1",        # Seminole Electric
    "SEPA": "us-east-1",       # Southeastern Power Admin
    "TAL": "us-east-1",        # Tallahassee
    "TEC": "us-east-1",        # Tampa Electric
    "YAD": "us-east-1",        # Alcoa Power (Yadkin)
    "LGEE": "us-east-2",       # Louisville G&E
    "EEI": "us-east-2",        # Energy East Illinois
    "AEC": "us-east-2",        # Associated Electric Coop
    "PGE": "us-west-1",        # Portland General Electric
    "DEAA": "us-west-2",       # Arlington Valley (AZ)
    # Canadian (in EIA)
    "IESO": "ca-central-1",    # Ontario
    "AESO": "ca-central-1",    # Alberta

    # UK zones
    "GB": "eu-west-2",
    "GB-national": "eu-west-2",
    "GB-1": "eu-west-2", "GB-2": "eu-west-2",
    "GB-3": "eu-west-2", "GB-4": "eu-west-2",
    "GB-5": "eu-west-2", "GB-6": "eu-west-2",
    "GB-7": "eu-west-2", "GB-8": "eu-west-2",
    "GB-9": "eu-west-2", "GB-10": "eu-west-2",
    "GB-11": "eu-west-2", "GB-12": "eu-west-2",
    "GB-13": "eu-west-2", "GB-14": "eu-west-2",
    "GB-15": "eu-west-2", "GB-16": "eu-west-2",
    "GB-17": "eu-west-2",

    # Europe (Electricity Maps zones)
    "NO-NO1": "eu-north-1",    # Oslo → Stockholm
    "NO-NO2": "eu-north-1",
    "NO-NO3": "eu-north-1",
    "NO-NO4": "eu-north-1",
    "NO-NO5": "eu-north-1",
    "SE-SE1": "eu-north-1",    # Sweden → Stockholm
    "SE-SE2": "eu-north-1",
    "SE-SE3": "eu-north-1",
    "SE-SE4": "eu-north-1",
    "FI": "eu-north-1",        # Finland → Stockholm
    "DK-DK1": "eu-north-1",    # Denmark West → Stockholm
    "DK-DK2": "eu-north-1",    # Denmark East → Stockholm
    "FR": "eu-west-3",         # France → Paris
    "DE": "eu-central-1",      # Germany → Frankfurt
    "NL": "eu-central-1",      # Netherlands → Frankfurt
    "BE": "eu-central-1",      # Belgium → Frankfurt
    "AT": "eu-central-1",      # Austria → Frankfurt
    "CH": "eu-central-1",      # Switzerland → Frankfurt
    "PL": "eu-central-1",      # Poland → Frankfurt
    "CZ": "eu-central-1",      # Czech Republic → Frankfurt
    "ES": "eu-south-2",        # Spain → Spain
    "PT": "eu-south-2",        # Portugal → Spain
    "IT-NO": "eu-south-1",     # Italy North → Milan
    "IT-CNO": "eu-south-1",
    "IT-CSO": "eu-south-1",
    "IT-SO": "eu-south-1",
    "IT-SIC": "eu-south-1",
    "IT-SAR": "eu-south-1",
    "IE": "eu-west-1",         # Ireland → Ireland
    "GR": "eu-south-1",        # Greece → Milan
    "IS": "eu-west-1",         # Iceland → Ireland (closest)
    "EE": "eu-north-1",        # Estonia → Stockholm
    "LV": "eu-north-1",        # Latvia → Stockholm
    "LT": "eu-north-1",        # Lithuania → Stockholm
    "RO": "eu-central-1",      # Romania → Frankfurt
    "BG": "eu-south-1",        # Bulgaria → Milan
    "HU": "eu-central-1",      # Hungary → Frankfurt
    "SK": "eu-central-1",      # Slovakia → Frankfurt
    "HR": "eu-south-1",        # Croatia → Milan
    "RS": "eu-south-1",        # Serbia → Milan
    "SI": "eu-south-1",        # Slovenia → Milan
    "BA": "eu-south-1",        # Bosnia → Milan
    "ME": "eu-south-1",        # Montenegro → Milan
    "MK": "eu-south-1",        # North Macedonia → Milan
    "AL": "eu-south-1",        # Albania → Milan

    # Canada (Electricity Maps)
    "CA-QC": "ca-central-1",   # Quebec → Montreal
    "CA-ON": "ca-central-1",   # Ontario → Montreal
    "CA-AB": "ca-west-1",      # Alberta → Calgary
    "CA-BC": "us-west-2",      # British Columbia → Oregon (closest)
    "CA-SK": "ca-central-1",   # Saskatchewan
    "CA-MB": "ca-central-1",   # Manitoba
    "CA-NB": "ca-central-1",   # New Brunswick
    "CA-NS": "ca-central-1",   # Nova Scotia
    "CA-PE": "ca-central-1",   # PEI
    "CA-NL": "ca-central-1",   # Newfoundland

    # Asia-Pacific
    "JP-TK": "ap-northeast-1",  # Tokyo
    "JP-CB": "ap-northeast-1",
    "JP-KN": "ap-northeast-3",  # Kansai → Osaka
    "JP-KY": "ap-northeast-3",
    "JP-HR": "ap-northeast-1",
    "JP-HK": "ap-northeast-1",
    "JP-TH": "ap-northeast-1",
    "JP-SK": "ap-northeast-1",
    "KR": "ap-northeast-2",     # South Korea → Seoul
    "IN-NO": "ap-south-1",      # India North → Mumbai
    "IN-SO": "ap-south-1",
    "IN-EA": "ap-south-1",
    "IN-WE": "ap-south-1",
    "IN-NE": "ap-south-1",
    "AU-NSW": "ap-southeast-2", # Australia → Sydney
    "AU-VIC": "ap-southeast-2",
    "AU-QLD": "ap-southeast-2",
    "AU-SA": "ap-southeast-2",
    "AU-TAS": "ap-southeast-2",
    "AU-WA": "ap-southeast-2",
    "NZ-NZN": "ap-southeast-2", # New Zealand → Sydney (closest)
    "NZ-NZS": "ap-southeast-2",
    "SG": "ap-southeast-1",     # Singapore
    "TW": "ap-northeast-1",     # Taiwan → Tokyo (closest)
    "HK": "ap-east-1",          # Hong Kong

    # Latin America
    "BR-CS": "sa-east-1",       # Brazil South → São Paulo
    "BR-S": "sa-east-1",
    "BR-NE": "sa-east-1",
    "BR-N": "sa-east-1",
    "CL-SEN": "sa-east-1",      # Chile → São Paulo (closest)
    "AR": "sa-east-1",          # Argentina → São Paulo
    "UY": "sa-east-1",          # Uruguay → São Paulo
    "CO": "sa-east-1",          # Colombia → São Paulo
    "CR": "us-east-1",          # Costa Rica → N. Virginia (closest)
    "PA": "us-east-1",          # Panama → N. Virginia
    "PY": "sa-east-1",          # Paraguay → São Paulo
    "PE": "sa-east-1",          # Peru → São Paulo
    "EC": "sa-east-1",          # Ecuador → São Paulo
    "MX": "us-east-2",          # Mexico → Ohio

    # Middle East & Africa
    "IL": "me-south-1",         # Israel → Bahrain
    "AE": "me-south-1",         # UAE → Bahrain
    "ZA": "af-south-1",         # South Africa → Cape Town

    # EIA region rollups (used in some queries)
    "CAL": "us-west-1",
    "NW": "us-west-2",
    "SW": "us-west-2",
    "NE": "us-east-1",
    "NY": "us-east-1",
    "MIDA": "us-east-1",
    "SE": "us-east-1",
    "FLA": "us-east-1",
    "MIDW": "us-east-2",
    "CENT": "us-east-2",
    "TEX": "us-east-2",
    "CAR": "us-east-1",
}

# Default AWS region when zone isn't in the mapping
DEFAULT_AWS_REGION = "us-east-1"

# Default RunsOn runner spec
DEFAULT_RUNSON_SPEC = "2cpu-linux-x64"


def get_cloud_region(zone):
    """Get the nearest AWS region for a grid zone.

    Returns an AWS region string (e.g., 'us-west-1') or the default.
    """
    return ZONE_TO_AWS_REGION.get(zone, DEFAULT_AWS_REGION)


def format_runson_label(zone, run_id, runner_spec=None):
    """Format a RunsOn-compatible runner label with region.

    RunsOn label syntax: runs-on=$RUN_ID/runner=$SPEC/region=$REGION
    See https://runs-on.com/configuration/job-labels/
    """
    spec = runner_spec or DEFAULT_RUNSON_SPEC
    region = get_cloud_region(zone)
    return f"runs-on={run_id}/runner={spec}/region={region}"


def format_runner_label(zone, provider, run_id="", runner_spec=""):
    """Format a runner label for the given provider.

    Args:
        zone: Grid zone identifier (e.g., 'CISO', 'GB', 'DE')
        provider: Runner provider name ('runson' or empty for passthrough)
        run_id: GitHub run ID (required for RunsOn)
        runner_spec: Runner spec override (e.g., '4cpu-linux-arm64')

    Returns:
        Formatted runner label string, or None if no formatting needed.
    """
    provider = (provider or "").strip().lower()

    if provider == "runson":
        if not run_id:
            print("::warning::runner_provider=runson but GITHUB_RUN_ID not available. "
                  "Falling back to cloud_region output only.")
            return None
        return format_runson_label(zone, run_id, runner_spec or None)

    # No known provider — return None (caller uses user-provided label or zone)
    return None
