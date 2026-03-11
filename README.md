# Carbon-Aware Dispatcher

![CO2 Saved](https://img.shields.io/badge/CO2_saved-green_CI-brightgreen?style=flat&logo=leaf&logoColor=white) ![Providers](https://img.shields.io/badge/providers-10-blue) ![Zones](https://img.shields.io/badge/zones-200%2B-blue) ![CI Platforms](https://img.shields.io/badge/CI-GitHub%20%7C%20GitLab%20%7C%20Bitbucket%20%7C%20CircleCI-orange)

Sustainable CI/CD for any platform. Delays compute-heavy workflows until the energy grid is powered by clean, renewable energy. Works with GitHub Actions, GitLab CI, Bitbucket Pipelines, and CircleCI.

**Truly zero config** — just add the action with no inputs. Auto-detects your cloud region (AWS, GCP, Azure) and checks the grid. No API keys, no zone codes to look up, no configuration needed.

### Provider Capabilities

| Provider | Coverage | API Key | Real-time | Forecast | Zones |
|----------|----------|---------|-----------|----------|-------|
| [EIA](https://www.eia.gov/opendata/) | US (60+ regions) | Free built-in | Fuel mix | Via GridStatus | `CISO`, `ERCO`, `PJM`, `BPAT`... |
| [UK Carbon Intensity](https://carbonintensity.org.uk/) | UK (18 regions) | None | Direct | 48h free | `GB`, `GB-1`..`GB-17` |
| [AEMO](https://aemo.com.au/) | Australia (5 states) | None | Fuel mix | - | `AU-NSW`, `AU-SA`, `AU-TAS`... |
| [Grid India](https://report.grid-india.in/) | India (5 regions) | None | Generation | Solar heuristic | `IN-NO`, `IN-SO`, `IN-WE`... |
| [ONS Brazil](https://integra.ons.org.br/) | Brazil (5 regions) | None | Energy balance | Hydro heuristic | `BR-S`, `BR-SE`, `BR-NE`... |
| [Eskom](https://www.eskom.co.za/) | South Africa | None | Estimation | Heuristic | `ZA` |
| [ENTSO-E](https://transparency.entsoe.eu/) | EU (36 countries) | Free token | Generation mix | Day-ahead | `DE`, `FR`, `NO-NO1`... |
| [Electricity Maps](https://www.electricitymaps.com/) | Global (200+ zones) | Free token | Direct | 24h | Any zone on their map |
| [Open-Meteo](https://open-meteo.com/) | Worldwide (90+ zones) | None | Weather-based | 48h weather | Auto-fallback for any zone |
| [GridStatus](https://www.gridstatus.io) | US (7 ISOs) | Free token | - | Solar/wind/load | `CISO`, `ERCO`, `PJM`... |

### Key Features

- **Cloud auto-detection** — detects AWS/GCP/Azure region from environment variables, maps to the nearest grid zone automatically
- **Multi-zone routing** — checks multiple zones, picks the greenest one, outputs runner labels for all three major clouds
- **Fallback chains** — if a provider fails, automatically falls back to Open-Meteo weather-based estimation
- **Smart presets** — `auto:detect`, `auto:nearest`, `auto:green`, `auto:cleanest`, `auto:escape-coal` for zero-config use
- **Forecast for all providers** — time-of-day heuristics for India (solar peak), Brazil (hydro/thermal), and South Africa even without API forecasts
- **Queue strategy** — find the optimal green window within your deadline across all zones
- **Multi-platform** — templates for GitHub Actions, GitLab CI, Bitbucket Pipelines, CircleCI
- **Carbon savings badge** — Shields.io badge URL showing estimated CO2 saved per run
- **Cron schedule optimizer** — when the grid is dirty, suggests the optimal cron schedule based on zone energy type
- **Reusable workflow** — other repos can call the carbon check with zero setup via `workflow_call`
- **One-liner setup** — `curl | bash` script to add carbon-aware CI to any repo in seconds
- **Org-wide config** — `.github/carbon-policy.yml` sets defaults for all workflows
- **Fast installs via uv** — uses [uv](https://github.com/astral-sh/uv) for near-instant dependency installation in CI

## How It Works

1. Action runs on a cron schedule (e.g., hourly)
2. Fetches real-time fuel mix data and calculates carbon intensity
3. If intensity is below your threshold, dispatches your heavy workflow
4. If the grid is dirty, optionally waits for a green window (`max_wait`), reports the trend and forecast, then exits cleanly

## Use Cases

- **Media Processing:** Batch audio/video conversion, rendering
- **Machine Learning:** Model training or fine-tuning
- **Data Operations:** Database backups, indexing, large migrations
- **Any non-urgent batch job** that can wait for clean energy

## Easiest Start — Truly Zero Config (No Inputs Needed)

Just add the action. No zone, no API key, no configuration:

```yaml
name: Carbon-Aware Build
on:
  schedule:
    - cron: '0 * * * *'
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: peterklingelhofer/carbon-aware-dispatcher@v1
        id: carbon

      - if: steps.carbon.outputs.grid_clean == 'true'
        uses: actions/checkout@v4

      - if: steps.carbon.outputs.grid_clean == 'true'
        run: echo "Running on clean energy in ${{ steps.carbon.outputs.grid_zone }}!"
```

That's it — **no inputs, no API keys, no secrets, one file.** The action auto-detects your cloud region or checks 17+ zones across 8 free providers worldwide.

You can also be explicit with presets:
- `auto:detect` — detects your cloud region from env vars (AWS/GCP/Azure)
- `auto:nearest` — detects your timezone and picks the geographically closest green zones
- `auto:cleanest` — checks all free-provider zones, picks the cleanest
- `auto:green` — 10 curated zones that are frequently powered by clean energy (free providers only)
- `auto:green:full` — 21 zones including token-requiring EU/Canada/NZ zones

### Escape from Dirty Grids

In India, China, Poland, or South Africa? Use `auto:escape-coal` to route your jobs to the nearest clean energy region:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zones: 'auto:escape-coal'       # Routes to cleanest global zones
    # Or target escapes from a specific zone:
    # grid_zones: 'auto:escape-coal:IN'  # India → Iceland, Norway, France
    # grid_zones: 'auto:escape-coal:CN'  # China → NZ, Tasmania, Pacific NW
    # grid_zones: 'auto:escape-coal:PL'  # Poland → Norway, Sweden, France
    # grid_zones: 'auto:escape-coal:ZA'  # South Africa → Iceland, Norway
```

### One-Liner Setup

Add carbon-aware CI to any repo with one command:

```bash
curl -fsSL https://raw.githubusercontent.com/peterklingelhofer/carbon-aware-dispatcher/main/setup.sh | bash
```

Options: `--threshold 200`, `--zones "auto:green"`, `--strategy queue`, `--cron "0 6 * * *"`. Run `setup.sh --help` for details.

### Reusable Workflow (Easiest Cross-Repo Adoption)

Other repos can call the carbon check without copying any files:

```yaml
jobs:
  green-check:
    uses: peterklingelhofer/carbon-aware-dispatcher/.github/workflows/carbon-check.yml@v1
    with:
      max_carbon_intensity: '200'
    secrets:
      electricity_maps_token: ${{ secrets.ELECTRICITY_MAPS_TOKEN }}

  build:
    needs: green-check
    if: needs.green-check.outputs.grid_clean == 'true'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "Building on clean energy in ${{ needs.green-check.outputs.grid_zone }}!"
```

## Quick Start (US)

### 1. Create the Gatekeeper Workflow

Create `.github/workflows/carbon-gatekeeper.yml`:

```yaml
name: Carbon-Aware Gatekeeper
on:
  schedule:
    - cron: '0 * * * *'  # Hourly
  workflow_dispatch:     # Allow manual triggers for testing

jobs:
  check-grid:
    runs-on: ubuntu-latest
    steps:
      - uses: peterklingelhofer/carbon-aware-dispatcher@v1
        id: carbon-check
        with:
          grid_zone: 'CISO'            # California ISO
          max_carbon_intensity: '200'
          workflow_id: 'heavy-batch.yml'
          github_token: ${{ secrets.GITHUB_TOKEN }}
```

### 2. Create Your Heavy Workflow

Create `.github/workflows/heavy-batch.yml` with a `workflow_dispatch` trigger:

```yaml
name: Heavy Batch Job
on:
  workflow_dispatch:

jobs:
  process:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "Running heavy workload on clean energy!"
```

That's it — no API keys, no secrets to configure.

## Quick Start (Global — Electricity Maps)

For zones outside US and UK (Europe, Asia, Australia, etc.), use [Electricity Maps](https://www.electricitymaps.com/):

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zone: 'DE'                    # Germany (any Electricity Maps zone code)
    max_carbon_intensity: '200'
    workflow_id: 'heavy-batch.yml'
    github_token: ${{ secrets.GITHUB_TOKEN }}
    electricity_maps_token: ${{ secrets.ELECTRICITY_MAPS_TOKEN }}
```

Register free at [portal.electricitymaps.com](https://portal.electricitymaps.com/) — 50 requests/hour on the free tier.

## Quick Start (UK)

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zone: 'GB'                  # or GB-13 for London, GB-16 for Scotland
    max_carbon_intensity: '150'
    workflow_id: 'heavy-batch.yml'
    github_token: ${{ secrets.GITHUB_TOKEN }}
```

UK zones also get free 48h forecasts — the action will tell you when the grid is expected to be green.

## Multi-Zone Mode (Route to the Greenest Region)

Check multiple zones and pick the cleanest one. You can mix US and UK zones. Pair with self-hosted runner labels to physically route work to the greenest region:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  id: carbon-check
  with:
    grid_zones: 'CISO:runner-cal, NYIS:runner-ny, GB:runner-uk'
    max_carbon_intensity: '200'
    workflow_id: 'heavy-batch.yml'
    github_token: ${{ secrets.GITHUB_TOKEN }}
```

The selected runner label is available via `${{ steps.carbon-check.outputs.runner_label }}`.

## Carbon-Aware Routing (Route Jobs to Green Regions)

The action can shift from gatekeeper (block if dirty) to **router** (send jobs to the greenest region). This works today with any provider that supports region selection.

### Phase 1: Label-Based Routing (Any Runner Provider)

Use multi-zone mode with `runner_label` to route a downstream job to whichever region is greenest:

```yaml
jobs:
  pick-region:
    runs-on: ubuntu-latest
    outputs:
      runner: ${{ steps.carbon.outputs.runner_label }}
      region: ${{ steps.carbon.outputs.cloud_region }}
      clean: ${{ steps.carbon.outputs.grid_clean }}
    steps:
      - uses: peterklingelhofer/carbon-aware-dispatcher@v1
        id: carbon
        with:
          grid_zones: 'CISO:us-west-runner,PJM:us-east-runner,GB:uk-runner'
          max_carbon_intensity: '200'

  build:
    needs: pick-region
    if: needs.pick-region.outputs.clean == 'true'
    runs-on: ${{ needs.pick-region.outputs.runner }}
    steps:
      - uses: actions/checkout@v4
      - run: echo "Building in greenest region (${{ needs.pick-region.outputs.region }})"
```

This works with self-hosted runners, GitHub Larger Runners, or any third-party runner provider. You provide the label mapping; the action picks the greenest zone.

The `cloud_region` output always contains the nearest AWS region name (e.g., `us-west-1`, `eu-west-2`) — useful for configuring deployments, caches, or artifact stores to match where the job ran.

### Phase 2: RunsOn Integration (Automatic Region Routing)

[RunsOn](https://runs-on.com) is an AWS-based runner provider that supports per-job region selection via labels. Set `runner_provider: 'runson'` and the action automatically outputs a RunsOn-compatible label with the greenest AWS region — no manual label mapping needed:

```yaml
jobs:
  pick-region:
    runs-on: ubuntu-latest
    outputs:
      runner: ${{ steps.carbon.outputs.runner_label }}
    steps:
      - uses: peterklingelhofer/carbon-aware-dispatcher@v1
        id: carbon
        with:
          grid_zones: 'CISO,BPAT,PJM,GB'
          max_carbon_intensity: '200'
          runner_provider: 'runson'
          runner_spec: '2cpu-linux-x64'   # Optional, this is the default

  build:
    needs: pick-region
    if: needs.pick-region.outputs.clean == 'true'
    runs-on: ${{ needs.pick-region.outputs.runner }}
    steps:
      - uses: actions/checkout@v4
      - run: echo "Running on clean energy!"
```

The action maps grid zones to AWS regions automatically:

| Grid Zone | AWS Region | Location |
|-----------|-----------|----------|
| `CISO` | `us-west-1` | N. California |
| `BPAT` | `us-west-2` | Oregon |
| `PJM` | `us-east-1` | N. Virginia |
| `ERCO` | `us-east-2` | Ohio |
| `GB` | `eu-west-2` | London |
| `NO-NO1` | `eu-north-1` | Stockholm |
| `FR` | `eu-west-3` | Paris |
| `DE` | `eu-central-1` | Frankfurt |
| `CA-QC` | `ca-central-1` | Montreal |
| `JP-TK` | `ap-northeast-1` | Tokyo |
| `AU-NSW` | `ap-southeast-2` | Sydney |

200+ zones are mapped. The full mapping covers all EIA balancing authorities, UK regions, and major Electricity Maps zones worldwide.

### Using cloud_region with Other Providers

Even without `runner_provider`, the `cloud_region` output lets you build your own routing logic for any provider:

```yaml
# Use cloud_region to construct your own runner labels
- run: echo "Greenest AWS region: ${{ steps.carbon.outputs.cloud_region }}"

# Example: use with WarpBuild, Namespace, or custom labels
- run: |
    echo "Deploy to ${{ steps.carbon.outputs.cloud_region }}"
    # Or use in Terraform/Pulumi to provision in the green region
```

## Auto-Green Mode (Zero-Config Global Routing)

Don't know which zones are green? Use `auto:green` — a curated list of zones that are frequently powered by clean energy, spanning multiple time zones so at least one is likely green at any given time:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  id: carbon-check
  with:
    grid_zones: 'auto:green'
    max_carbon_intensity: '200'
    workflow_id: 'heavy-batch.yml'
    github_token: ${{ secrets.GITHUB_TOKEN }}
    electricity_maps_token: ${{ secrets.ELECTRICITY_MAPS_TOKEN }}  # Optional: enables global zones
```

The preset expands to 15 curated zones across 5 continents, sorted by time-of-day priority (solar zones rank higher during their local daytime):

- **Americas:** `CISO` (California solar), `BPAT` (Pacific NW hydro), `CA-QC` (Quebec hydro), `CA-BC` (BC hydro), `BR-S` (Brazil South hydro), `UY` (Uruguay wind), `PY` (Paraguay hydro), `CR` (Costa Rica hydro)
- **Europe:** `GB-16` (Scotland wind), `NO-NO1` (Norway hydro), `SE-SE2` (Sweden hydro), `FR` (France nuclear), `IS` (Iceland hydro)
- **Oceania:** `NZ-NZN` (New Zealand hydro), `AU-TAS` (Tasmania hydro)

US, UK, and Australian zones always work without extra API keys; EU zones work with an ENTSO-E token or Electricity Maps token (missing zones are silently skipped).

## Smart Wait (Wait for Green Energy)

Instead of just checking once and exiting, the action can wait for a green window within a time limit. It uses forecast data to sleep efficiently:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zone: 'CISO'
    max_carbon_intensity: '200'
    workflow_id: 'heavy-batch.yml'
    github_token: ${{ secrets.GITHUB_TOKEN }}
    max_wait: '120'   # Wait up to 2 hours for green energy
    enable_forecast: 'true'
    gridstatus_api_key: ${{ secrets.GRIDSTATUS_API_KEY }}
```

If the grid is dirty at 8am but the forecast shows California going green at 10am (solar ramp-up), the action will sleep until then and dispatch automatically — no need for repeated cron runs.

**Important:** GitHub Actions bills for wait time. Each minute of waiting counts as a billable minute. Max: 360 minutes (6 hours).

## Inline Mode (Recommended — Single-File Workflow)

**The simplest setup.** Omit `workflow_id` to use inline mode — the action just checks the grid and sets outputs, and you use conditional steps in the same workflow. No second file, no `github_token`, no `workflow_dispatch` trigger needed:

```yaml
name: Carbon-Aware Build
on:
  schedule:
    - cron: '0 * * * *'
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: peterklingelhofer/carbon-aware-dispatcher@v1
        id: carbon
        with:
          grid_zone: 'CISO'
          max_carbon_intensity: '200'

      - if: steps.carbon.outputs.grid_clean == 'true'
        uses: actions/checkout@v4

      - if: steps.carbon.outputs.grid_clean == 'true'
        run: echo "Running on clean energy! (${{ steps.carbon.outputs.carbon_intensity }} gCO2eq/kWh)"
```

No `workflow_id`, `github_token`, or second workflow file needed. The action sets `grid_clean`, `carbon_intensity`, and other outputs for your steps to use.

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `grid_zone` | No | `auto:detect` | Single zone, or a preset (`auto:detect`, `auto:green`, `auto:cleanest`). US: EIA BA code, UK: `GB`/`GB-1`..`GB-17`, Global: [Electricity Maps zone](https://app.electricitymaps.com/map). If omitted, auto-detects cloud region. |
| `grid_zones` | No | `auto:detect` | Comma-separated zones, optionally with runner labels. Or a preset. If omitted, auto-detects cloud region. |
| `eia_api_key` | No | — | Optional EIA API key for higher rate limits. [Register free](https://www.eia.gov/opendata/register.php). Built-in `DEMO_KEY` works for basic use. |
| `gridstatus_api_key` | No | — | Optional [GridStatus.io](https://www.gridstatus.io) API key for US zone forecasts. [Register free](https://www.gridstatus.io) (1M rows/month). |
| `max_carbon_intensity` | No | `250` | Maximum gCO2eq/kWh to allow dispatch |
| `workflow_id` | No | — | Filename of the workflow to dispatch. Omit for inline mode (just set outputs). |
| `github_token` | No | — | GitHub token with Actions write permission. Required when `workflow_id` is set. |
| `target_ref` | No | `main` | Git ref to dispatch the workflow on |
| `fail_on_api_error` | No | `false` | Fail the action on API errors instead of skipping |
| `electricity_maps_token` | No | — | [Electricity Maps](https://portal.electricitymaps.com/) API token for global zones (200+ zones, 50 req/hr free). |
| `max_wait` | No | `0` | Minutes to wait for green energy (0 = check once). Uses forecasts to sleep efficiently. Max 360 (6h). Billable time. |
| `enable_forecast` | No | `false` | Fetch forecast when grid is dirty. UK: free 48h. US: requires `gridstatus_api_key`. Global: requires `electricity_maps_token`. |
| `runner_provider` | No | — | Runner provider for automatic region routing. Set to `runson` for [RunsOn](https://runs-on.com) AWS-based labels. |
| `runner_spec` | No | `2cpu-linux-x64` | Runner machine spec for RunsOn (e.g., `4cpu-linux-arm64`). Only used when `runner_provider` is set. |
| `entsoe_token` | No | — | [ENTSO-E](https://transparency.entsoe.eu/) security token for EU coverage (36 countries). Free registration. Preferred over Electricity Maps for EU zones when set. |
| `strategy` | No | `check` | Scheduling strategy. `check`: check now, dispatch if green. `queue`: find optimal green window within `deadline_hours`. |
| `deadline_hours` | No | `24` | Hours to look ahead for green windows when `strategy=queue`. |
| `carbon_policy_path` | No | `.github/carbon-policy.yml` | Path to org-wide carbon policy file. Policy values are defaults; action inputs override. |

If no zone inputs are provided, the action uses `auto:detect` — it detects your cloud region from environment variables and checks the nearest grid zone, or falls back to `auto:cleanest` if no cloud region is detected.

## Outputs

| Output | Description |
|--------|-------------|
| `grid_clean` | `true` if a zone was clean enough, `false` otherwise |
| `carbon_intensity` | Carbon intensity in gCO2eq/kWh, or `unknown` on error |
| `grid_zone` | The zone that was selected |
| `runner_label` | Runner label for the selected zone. Auto-formatted when `runner_provider` is set. |
| `cloud_region` | Nearest AWS region for the selected zone (e.g., `us-west-1`, `eu-west-2`). Always set. |
| `gcp_region` | Nearest GCP region for the selected zone (e.g., `us-west1`, `europe-west3`). Always set. |
| `azure_region` | Nearest Azure region for the selected zone (e.g., `westus2`, `germanywestcentral`). Always set. |
| `intensity_trend` | Recent trend: `decreasing`, `increasing`, or `stable` |
| `forecast_green_at` | ISO 8601 timestamp of next predicted green window (UK: free, US: GridStatus key) |
| `forecast_intensity` | Predicted intensity at the next green window |
| `co2_saved_grams` | Estimated grams of CO2 saved by running on clean energy vs. global average (450 gCO2eq/kWh) |
| `carbon_badge_url` | Shields.io badge URL showing estimated CO2 saved. Embed in README: `![carbon](url)` |
| `optimal_dispatch_at` | ISO 8601 timestamp of the optimal green window (`strategy=queue`). `now` if already green, `none_in_deadline` if no window. |
| `optimal_zone` | Zone recommended for the optimal dispatch window (`strategy=queue`). |

## Forecast & Trend

When the grid is over threshold, the action provides extra context:

**Trend (US + UK):** Reports whether intensity is `decreasing`, `increasing`, or `stable` based on recent hourly history. Always available, no extra keys needed.

**Forecast (UK):** Predicts when the grid will next be below your threshold, up to 48h ahead. Automatic and free — no API key needed.

**Forecast (US):** Predicts when the grid will be green using solar, wind, and load forecasts from [GridStatus.io](https://www.gridstatus.io). Requires a free GridStatus API key. Supported ISOs: CISO (California), ERCO (Texas), ISNE (New England), MISO, NYIS (New York), PJM, SWPP (SPP).

**Forecast (EU):** ENTSO-E day-ahead generation forecasts estimate when the grid will be green within 24h. Requires `entsoe_token`.

**Forecast (India):** Time-of-day heuristic based on solar generation patterns. India's grid gets significantly cleaner during solar peak (10am-4pm IST). Southern grid (IN-SO) is cleanest due to higher renewable penetration. Automatic, no key needed.

**Forecast (Brazil):** Hydro/thermal dispatch heuristic. Brazil's grid is cleanest off-peak (22:00-16:00 BRT) when hydro dominates. Evening peak (17:00-21:00) uses more thermal. Automatic, no key needed.

**Forecast (South Africa):** Heuristic based on SA's coal-dominant grid. Midday (10am-4pm SAST) is slightly cleaner due to solar, but rarely below 650 gCO2eq/kWh. Recommends `auto:escape-coal:ZA` for truly green scheduling.

**Forecast (Open-Meteo):** 48h solar irradiance and wind speed forecast for any location with known coordinates (90+ zones). Free, automatic.

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zone: 'CISO'
    max_carbon_intensity: '200'
    workflow_id: 'heavy-batch.yml'
    github_token: ${{ secrets.GITHUB_TOKEN }}
    gridstatus_api_key: ${{ secrets.GRIDSTATUS_API_KEY }}  # Enables US forecasts
    enable_forecast: 'true'
```

```yaml
- if: steps.carbon-check.outputs.grid_clean == 'false'
  run: |
    echo "Grid dirty. Trend: ${{ steps.carbon-check.outputs.intensity_trend }}"
    echo "Next green window: ${{ steps.carbon-check.outputs.forecast_green_at }}"
```

## Supported Zones

### US Zones (EIA API — No Key Required)

Uses hourly fuel mix data from the [U.S. Energy Information Administration](https://www.eia.gov/electricity/gridmonitor/) to calculate real-time carbon intensity using standard lifecycle emission factors.

| Zone | Region | Zone | Region |
|------|--------|------|--------|
| `CISO` | California ISO | `MISO` | Midcontinent |
| `ERCO` | Texas (ERCOT) | `ISNE` | New England |
| `PJM` | Mid-Atlantic/Midwest | `SWPP` | Southwest Power Pool |
| `NYIS` | New York ISO | `BPAT` | Bonneville Power |

60+ balancing authorities supported. Full list at [EIA Grid Monitor](https://www.eia.gov/electricity/gridmonitor/).

### UK Zones (Carbon Intensity API — No Key Required)

Uses the [UK Carbon Intensity API](https://carbonintensity.org.uk/) by the National Energy System Operator.

| Zone | Region | Zone | Region |
|------|--------|------|--------|
| `GB` | National | `GB-8` | West Midlands |
| `GB-1` | North Scotland | `GB-9` | East Midlands |
| `GB-2` | South Scotland | `GB-10` | East England |
| `GB-3` | North West England | `GB-11` | South West England |
| `GB-4` | North East England | `GB-12` | South England |
| `GB-5` | Yorkshire | `GB-13` | London |
| `GB-6` | North Wales | `GB-14` | South East England |
| `GB-7` | South Wales | `GB-15`/`GB-16`/`GB-17` | England/Scotland/Wales |

### Australian Zones (AEMO NEM — No Key Required)

Uses the [AEMO](https://aemo.com.au/) National Electricity Market API for real-time fuel mix data. Free, no registration.

| Zone | Region |
|------|--------|
| `AU-NSW` | New South Wales |
| `AU-QLD` | Queensland |
| `AU-VIC` | Victoria |
| `AU-SA` | South Australia |
| `AU-TAS` | Tasmania |

### Indian Zones (Grid India — No Key Required)

Uses [Grid India](https://report.grid-india.in/) (formerly POSOCO) for real-time generation data across India's five regional grids. Free, no registration.

| Zone | Region |
|------|--------|
| `IN-NO` | Northern |
| `IN-SO` | Southern |
| `IN-EA` | Eastern |
| `IN-WE` | Western |
| `IN-NE` | North-Eastern |

### Brazilian Zones (ONS — No Key Required)

Uses [ONS](https://integra.ons.org.br/) (Operador Nacional do Sistema Elétrico) for real-time energy balance data. Brazil's grid is ~70% hydro, making it one of the cleanest large grids globally. Free, no registration.

| Zone | Region |
|------|--------|
| `BR-S` | South (Sul) |
| `BR-SE` | Southeast (Sudeste) |
| `BR-CS` | Centro-Southeast |
| `BR-NE` | Northeast (Nordeste) |
| `BR-N` | North (Norte) |

### South Africa (Eskom — No Key Required)

Uses [Eskom](https://www.eskom.co.za/) data for South Africa's national grid. SA's grid is ~85% coal — one of the dirtiest globally (typically 700-900 gCO2eq/kWh). Use `auto:escape-coal:ZA` to route jobs to clean alternatives. Falls back to estimation from known grid characteristics if the API is unavailable.

| Zone | Region |
|------|--------|
| `ZA` | National (South Africa) |

### EU Zones (ENTSO-E — Free Token Required)

Uses the [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/) for actual generation data across 36 European countries. Calculates carbon intensity from the generation mix using standard emission factors. Requires a free security token (register at ENTSO-E, go to Account Settings → Web API Security Token).

| Zone | Country | Zone | Country |
|------|---------|------|---------|
| `DE` | Germany | `FR` | France |
| `ES` | Spain | `PT` | Portugal |
| `NL` | Netherlands | `BE` | Belgium |
| `AT` | Austria | `CH` | Switzerland |
| `PL` | Poland | `CZ` | Czech Republic |
| `DK-DK1`/`DK-DK2` | Denmark | `FI` | Finland |
| `SE-SE1`..`SE-SE4` | Sweden | `NO-NO1`..`NO-NO5` | Norway |
| `IT-NO`..`IT-SAR` | Italy (6 zones) | `GR` | Greece |
| `RO` | Romania | `BG` | Bulgaria |
| `HU` | Hungary | `IE` | Ireland |

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zone: 'DE'
    max_carbon_intensity: '200'
    entsoe_token: ${{ secrets.ENTSOE_TOKEN }}
```

### Global Zones (Electricity Maps — Free API Token Required)

Uses [Electricity Maps](https://www.electricitymaps.com/) for 200+ zones worldwide. Direct carbon intensity values in gCO2eq/kWh. Includes forecast and history trend data.

| Zone | Region | Zone | Region |
|------|--------|------|--------|
| `JP-TK` | Tokyo | `IN-NO` | Northern India |
| `CA-ON` | Ontario | `CA-QC` | Quebec |
| `BR-S` | Southern Brazil | `NZ-NZN` | New Zealand North |
| `SG` | Singapore | `KR` | South Korea |

Full zone list at [app.electricitymaps.com/map](https://app.electricitymaps.com/map). Any zone shown on the map can be used.

### Universal Fallback (Open-Meteo — No Key Required)

For zones not covered by any other provider, the action uses [Open-Meteo](https://open-meteo.com/) to estimate carbon intensity from real-time solar irradiance and wind speed data. This is a rough estimate — it doesn't know the actual grid mix, but indicates renewable potential. Covers 90+ zones across Europe, Americas, Asia-Pacific, Africa, and Middle East.

This provider activates automatically as a fallback. No configuration needed.

**Provider auto-detection:** The action automatically selects the best provider for each zone (in priority order):
1. `GB`, `GB-1`..`GB-17` → UK Carbon Intensity API (no key needed)
2. Known US balancing authorities (`CISO`, `ERCO`, `PJM`, etc.) → EIA API (no key needed)
3. `AU-NSW`, `AU-QLD`, etc. → AEMO NEM API (no key needed)
4. `IN-NO`, `IN-SO`, etc. → Grid India API (no key needed)
5. `BR-S`, `BR-NE`, etc. → ONS Brazil API (no key needed)
6. `ZA` → Eskom South Africa (no key needed)
7. EU zones when `entsoe_token` is set → ENTSO-E Transparency Platform
8. Zones with known coordinates → Open-Meteo weather estimate (automatic fallback)
9. Any zone with `electricity_maps_token` → Electricity Maps (catch-all)

If the primary provider fails, the action automatically falls back to Open-Meteo weather-based estimation for any zone with known coordinates.

## How Carbon Intensity Is Calculated

For US zones, the action fetches the real-time hourly fuel mix from the EIA API and calculates carbon intensity using standard lifecycle emission factors:

| Fuel | gCO2eq/kWh | Fuel | gCO2eq/kWh |
|------|-----------|------|-----------|
| Coal | 820 | Nuclear | 0 |
| Natural Gas | 490 | Solar | 0 |
| Oil/Petroleum | 650 | Wind | 0 |
| Other | 200 | Hydro/Geo | 0 |

For UK zones, the Carbon Intensity API provides pre-calculated intensity values directly.

For global zones, Electricity Maps provides pre-calculated lifecycle carbon intensity values directly in gCO2eq/kWh.

## Carbon Savings Badge

Each run estimates the CO2 saved by running on a clean grid vs. the global average (450 gCO2eq/kWh). Use the `carbon_badge_url` output to add a badge to your README:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  id: carbon
  with:
    grid_zone: 'CISO'
    max_carbon_intensity: '200'

- if: steps.carbon.outputs.grid_clean == 'true'
  run: |
    echo "CO2 saved: ${{ steps.carbon.outputs.co2_saved_grams }}g"
    echo "Badge: ${{ steps.carbon.outputs.carbon_badge_url }}"
```

Embed the badge in your README (the URL updates each run):
```markdown
![CO2 Saved](https://img.shields.io/badge/CO2_saved-5g_CO2-brightgreen?style=flat&logo=leaf&logoColor=white)
```

## Smart Queue Mode

Instead of just checking once, `strategy: queue` searches forecasts across all zones to find the optimal green window within your deadline:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  id: carbon
  with:
    grid_zones: 'auto:cleanest'
    strategy: 'queue'
    deadline_hours: '24'         # Find best window in next 24h
    max_wait: '120'              # Actually wait up to 2h if window is soon
```

The action outputs:
- `optimal_dispatch_at` — ISO 8601 timestamp of the best green window (`now` if already green)
- `optimal_zone` — which zone to use at that time
- If `max_wait` is set and the window is within range, it waits and dispatches automatically

Use this for jobs that aren't urgent but need to run within a deadline (e.g., nightly ML training, weekly reports).

## Multi-Cloud Region Recommender

The action outputs the nearest region for all three major cloud providers:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  id: carbon
  with:
    grid_zones: 'auto:cleanest'

# Use with any cloud provider:
- run: |
    echo "AWS:   ${{ steps.carbon.outputs.cloud_region }}"   # e.g., us-west-1
    echo "GCP:   ${{ steps.carbon.outputs.gcp_region }}"     # e.g., us-west1
    echo "Azure: ${{ steps.carbon.outputs.azure_region }}"   # e.g., westus2
```

60+ zones are mapped across all three clouds. Use the region output to route deployments, provision infrastructure, or configure runner providers on any cloud.

## Organization-Wide Carbon Policy

Drop a `.github/carbon-policy.yml` file in your repo to set defaults for all workflows:

```yaml
# .github/carbon-policy.yml
max_carbon_intensity: 150
grid_zones: 'auto:cleanest'
enable_forecast: true
strategy: queue
deadline_hours: 24
runner_provider: runson
```

Action inputs always override policy values. This lets platform teams enforce green CI defaults without every developer configuring the action manually.

## Setup Wizard

Validate your configuration before using the action. The setup wizard tests API connectivity and zone codes:

```bash
# Test common zones (no args)
python setup_wizard.py

# Test specific zones
python setup_wizard.py --zone CISO
python setup_wizard.py --zones "CISO,GB,DE,AU-NSW"

# Test auto:green preset
python setup_wizard.py --auto-green

# With API keys
python setup_wizard.py --zones "DE,FR" --electricity-maps-token YOUR_TOKEN
```

The wizard checks each zone's provider connectivity and prints a summary with recommendations.

## Rate Limits & API Keys

### US Zones (EIA API)

The action works **without any API key** using the EIA's built-in `DEMO_KEY`, but this key has aggressive rate limits (~30 requests/hour) and will quickly throttle if you check multiple zones or run frequent schedules.

**Recommended:** [Register for a free EIA API key](https://www.eia.gov/opendata/register.php) (takes 30 seconds, no approval needed) and pass it as `eia_api_key`. Free registered keys allow up to 1,000 requests/hour.

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zone: 'CISO'
    max_carbon_intensity: '200'
    workflow_id: 'heavy-batch.yml'
    github_token: ${{ secrets.GITHUB_TOKEN }}
    eia_api_key: ${{ secrets.EIA_API_KEY }}  # Optional but recommended
```

### UK Zones (Carbon Intensity API)

No API key needed. No documented rate limits. Works out of the box.

### Global Zones (Electricity Maps)

Requires a free API token. [Register at portal.electricitymaps.com](https://portal.electricitymaps.com/) — takes 30 seconds. Free tier allows **50 requests/hour**, which covers hourly checks of a single zone with room to spare. Includes carbon intensity, forecast, and history endpoints.

### Australian Zones (AEMO NEM)

No API key needed. No documented rate limits. Works out of the box.

### EU Zones (ENTSO-E)

Requires a free security token. [Register at transparency.entsoe.eu](https://transparency.entsoe.eu/) → Login → Account Settings → Web API Security Token. Rate limit: 400 requests/minute — more than sufficient.

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zone: 'DE'
    max_carbon_intensity: '200'
    workflow_id: 'heavy-batch.yml'
    github_token: ${{ secrets.GITHUB_TOKEN }}
    entsoe_token: ${{ secrets.ENTSOE_TOKEN }}
```

### Indian Zones (Grid India)

No API key needed. Uses Grid India's public generation data API. Works out of the box.

### Brazilian Zones (ONS)

No API key needed. Uses ONS Integra API for real-time energy balance. Works out of the box. Brazil's hydro-dominant grid is typically very clean.

### South Africa (Eskom)

No API key needed. Uses Eskom's public data with estimation fallback. SA's grid is ~85% coal — consider using `auto:escape-coal:ZA` to route to clean alternatives.

### Open-Meteo (Universal Fallback)

No API key needed. Rate limit: ~10,000 requests/day. Used automatically as a fallback for zones not covered by other providers. Provides rough estimates based on weather data, not actual grid mix.

### US Forecasts (GridStatus.io — Optional)

US zone forecasts require a free [GridStatus.io](https://www.gridstatus.io) API key. Sign up, then find your key under Settings. The free tier allows **1 million rows/month**, which is more than enough for hourly forecast checks.

GridStatus forecasts estimate carbon intensity by combining solar/wind generation forecasts with load forecasts. The action calculates what percentage of demand will be met by renewables and estimates the carbon intensity of the remaining fossil portion.

Supported ISOs for forecasts: `CISO`, `ERCO`, `ISNE`, `MISO`, `NYIS`, `PJM`, `SWPP`.

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zone: 'CISO'
    max_carbon_intensity: '200'
    workflow_id: 'heavy-batch.yml'
    github_token: ${{ secrets.GITHUB_TOKEN }}
    gridstatus_api_key: ${{ secrets.GRIDSTATUS_API_KEY }}
    enable_forecast: 'true'
```

## Why Carbon-Aware CI/CD?

Data centers consume **2.7% of Europe's energy** and the carbon footprint of CI/CD is substantial — a 2025 study estimates GitHub Actions alone produced **~457 metric tons of CO2e in 2024**, equivalent to the carbon captured by 7,615 urban trees per year ([Saavedra et al., 2025](https://arxiv.org/abs/2510.26413)). Carbon intensity varies dramatically by time of day and region: California's grid can swing from 400+ gCO2eq/kWh (evening, gas peakers) to near-zero (midday solar). Simply shifting *when* and *where* batch jobs run can yield significant reductions with zero changes to your code.

### Key Research

| Paper | Finding |
|-------|---------|
| [Carbon-Awareness in CI/CD](https://arxiv.org/abs/2310.18718) (Cla&szlig;en et al., 2023) | Analyzed 7,392 GitHub Actions workflows with real carbon intensity data. Demonstrated that scheduling CI/CD jobs based on grid carbon intensity and user-provided deadlines effectively reduces emissions. |
| [Environmental Impact of CI/CD Pipelines](https://arxiv.org/abs/2510.26413) (Saavedra et al., 2025) | Large-scale study of 2.2M GitHub Actions runs across 18,000+ repos. Recommends deploying runners in regions with cleaner energy grids (e.g., France, UK) as a key mitigation strategy. |
| [CarbonScaler](https://arxiv.org/abs/2302.08681) (Hanafy et al., 2023) | Achieves up to **51% carbon savings** by dynamically adjusting compute based on real-time grid carbon intensity — the same principle this action applies. |
| [On the Limitations of Carbon-Aware Workload Shifting](https://arxiv.org/abs/2306.06502) (Sukprasert et al., 2023) | Finds that even simple scheduling policies capture most achievable carbon reductions — you don't need complex optimization to make a difference. |
| [Survey on Carbon-Aware Container Orchestration](https://arxiv.org/abs/2508.05949) (Yang et al., 2025) | Comprehensive survey of 50+ works. Reports 10–51% emission reductions across various carbon-aware scheduling approaches. |

### The Bottom Line

Carbon-aware scheduling is not theoretical — it delivers **20–50% carbon reductions** with minimal complexity. This action implements the simplest effective approach: check the grid, dispatch if clean, wait if not. No infrastructure changes required.

## Choosing a Threshold

The default `max_carbon_intensity` is `250` gCO2eq/kWh. Here's what typical values look like by region to help you choose:

| Region | Typical Range | Suggested Threshold |
|--------|--------------|-------------------|
| Norway, Quebec, Iceland, Paraguay | 10–30 | `50` |
| France, Sweden, Ontario, Brazil (hydro) | 30–80 | `100` |
| California (midday solar), Costa Rica | 0–150 | `150`–`200` |
| UK, New Zealand | 100–300 | `200` |
| Germany, US average | 200–500 | `300` |
| Poland, Australia (coal states) | 400–800 | `500` (or use `auto:escape-coal`) |
| India, South Africa | 600–900 | Use `auto:escape-coal:IN` or `auto:escape-coal:ZA` |

If your region never goes below your threshold, use **multi-zone mode** or **`auto:green`** to route work to a cleaner region.

## Other CI Platforms (GitLab, Bitbucket, CircleCI)

The core Python script works on any CI platform. Ready-to-use templates are in the [`ci-templates/`](ci-templates/) directory:

### GitLab CI

```yaml
# .gitlab-ci.yml
include:
  - local: 'ci-templates/gitlab-ci.yml'

my-heavy-job:
  extends: .carbon-aware-job
  script:
    - echo "Running on clean energy!"
```

Set `GRID_ZONE` and `MAX_CARBON` as CI/CD variables. See [`ci-templates/gitlab-ci.yml`](ci-templates/gitlab-ci.yml) for the full template.

### Bitbucket Pipelines

See [`ci-templates/bitbucket-pipelines.yml`](ci-templates/bitbucket-pipelines.yml) — uses artifacts to pass the carbon check result between steps.

### CircleCI

See [`ci-templates/circleci-config.yml`](ci-templates/circleci-config.yml) — uses workspaces to share the carbon check result between jobs.

All templates support the same environment variables: `GRID_ZONE`, `MAX_CARBON`, `ELECTRICITY_MAPS_TOKEN`, `ENTSOE_TOKEN`, `EIA_API_KEY`.

## Example Workflows

Ready-to-copy workflow files in the [`examples/`](examples/) directory:

| Example | Description |
|---------|-------------|
| [`zero-config.yml`](examples/zero-config.yml) | Simplest setup — no inputs needed |
| [`multi-cloud-routing.yml`](examples/multi-cloud-routing.yml) | Route jobs to greenest AWS/GCP/Azure region |
| [`queue-strategy.yml`](examples/queue-strategy.yml) | Find optimal green window within a deadline |
| [`escape-coal.yml`](examples/escape-coal.yml) | Escape dirty grids (India, China, Poland, SA) |

## Troubleshooting

**`forecast_green_at = none_in_forecast`** — The grid isn't expected to go below your threshold within the forecast horizon (48h for UK, varies for others). Consider raising your threshold or using multi-zone mode.

**`carbon_intensity = unknown`** — The API couldn't be reached. Check your API key and network. Set `fail_on_api_error: 'true'` to make these failures visible instead of silently skipping.

**EIA rate limiting** — If you see `429` errors, you're hitting the DEMO_KEY limit (~30 req/hr). [Register a free EIA API key](https://www.eia.gov/opendata/register.php) for 1,000 req/hr.

**Electricity Maps zone not found** — Zone codes are case-sensitive and use the format shown on [app.electricitymaps.com/map](https://app.electricitymaps.com/map) (e.g., `DE`, `FR`, `AU-NSW`, `NO-NO1`).

**Multi-zone zones silently skipped** — If a zone requires an API key that isn't set (e.g., `DE` without `electricity_maps_token`), it's skipped with a warning. Check the action logs for "Skipping zone" messages.

**All timestamps are UTC** — The `forecast_green_at` output is in ISO 8601 UTC format. Convert to your local timezone as needed.

## License

[MIT](LICENSE)
