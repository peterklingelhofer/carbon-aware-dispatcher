# Carbon-Aware Dispatcher

A GitHub Action that delays compute-heavy CI/CD workflows until the energy grid is powered by clean, renewable energy. Runs on a cron schedule as a gatekeeper — checks live carbon intensity data, and dispatches your heavy workflow only when the grid is green.

**Zero config for US, UK & Australia — no API keys required.** Uses the [EIA API](https://www.eia.gov/opendata/) for US zones, the [UK Carbon Intensity API](https://carbonintensity.org.uk/) for UK zones, and [AEMO](https://aemo.com.au/) for Australian NEM zones. All free, open, and need no authentication.

**EU coverage with [ENTSO-E](https://transparency.entsoe.eu/)** — 36 European countries with actual generation data. Requires a free security token.

**Global coverage with [Electricity Maps](https://www.electricitymaps.com/)** — 200+ zones worldwide including Europe, Canada, India, Japan, Latin America, and more. Requires a free API token (50 requests/hour).

**Universal fallback with [Open-Meteo](https://open-meteo.com/)** — estimates carbon intensity from real-time solar irradiance and wind speed for any location worldwide. Free, no API key. Used automatically when no other provider covers a zone.

**Multi-zone support** — provide multiple grid zones (optionally mapped to self-hosted runner labels) and the action picks the zone with the lowest carbon intensity, routing your workload to the greenest available region.

**Carbon savings badge** — each run estimates the CO2 saved by running on clean energy and outputs a Shields.io badge URL for your README.

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

## Inline Mode (Single-File Workflow)

Don't want two separate workflow files? Omit `workflow_id` to use inline mode — the action just checks the grid and sets outputs, and you use conditional steps in the same workflow:

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
| `grid_zone` | No* | — | Single zone, or `auto:green` for curated green zones. US: EIA BA code, UK: `GB`/`GB-1`..`GB-17`, Global: [Electricity Maps zone](https://app.electricitymaps.com/map). |
| `grid_zones` | No* | — | Comma-separated zones, optionally with runner labels. Or `auto:green`. |
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

\* One of `grid_zone` or `grid_zones` is required.

## Outputs

| Output | Description |
|--------|-------------|
| `grid_clean` | `true` if a zone was clean enough, `false` otherwise |
| `carbon_intensity` | Carbon intensity in gCO2eq/kWh, or `unknown` on error |
| `grid_zone` | The zone that was selected |
| `runner_label` | Runner label for the selected zone. Auto-formatted when `runner_provider` is set. |
| `cloud_region` | Nearest AWS region for the selected zone (e.g., `us-west-1`, `eu-west-2`). Always set. |
| `intensity_trend` | Recent trend: `decreasing`, `increasing`, or `stable` |
| `forecast_green_at` | ISO 8601 timestamp of next predicted green window (UK: free, US: GridStatus key) |
| `forecast_intensity` | Predicted intensity at the next green window |
| `co2_saved_grams` | Estimated grams of CO2 saved by running on clean energy vs. global average (450 gCO2eq/kWh) |
| `carbon_badge_url` | Shields.io badge URL showing estimated CO2 saved. Embed in README: `![carbon](url)` |

## Forecast & Trend

When the grid is over threshold, the action provides extra context:

**Trend (US + UK):** Reports whether intensity is `decreasing`, `increasing`, or `stable` based on recent hourly history. Always available, no extra keys needed.

**Forecast (UK):** Predicts when the grid will next be below your threshold, up to 48h ahead. Automatic and free — no API key needed.

**Forecast (US):** Predicts when the grid will be green using solar, wind, and load forecasts from [GridStatus.io](https://www.gridstatus.io). Requires a free GridStatus API key. Supported ISOs: CISO (California), ERCO (Texas), ISNE (New England), MISO, NYIS (New York), PJM, SWPP (SPP).

**Forecast (EU):** ENTSO-E day-ahead generation forecasts estimate when the grid will be green within 24h. Requires `entsoe_token`.

**Forecast (Open-Meteo):** 48h solar irradiance and wind speed forecast for any location with known coordinates. Free, automatic.

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

For zones not covered by any other provider, the action uses [Open-Meteo](https://open-meteo.com/) to estimate carbon intensity from real-time solar irradiance and wind speed data. This is a rough estimate — it doesn't know the actual grid mix, but indicates renewable potential. Covers 30+ zones across Africa, Middle East, Southeast Asia, South Asia, China, and Eastern Europe.

This provider activates automatically as a fallback. No configuration needed.

**Provider auto-detection:** The action automatically selects the right provider based on the zone identifier (in priority order):
1. `GB`, `GB-1`..`GB-17` → UK Carbon Intensity API (no key needed)
2. Known US balancing authorities (`CISO`, `ERCO`, `PJM`, etc.) → EIA API (no key needed)
3. `AU-NSW`, `AU-QLD`, etc. → AEMO NEM API (no key needed)
4. EU zones when `entsoe_token` is set → ENTSO-E Transparency Platform
5. Any zone with `electricity_maps_token` → Electricity Maps
6. Zones with known coordinates → Open-Meteo weather estimate (automatic fallback)

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
| Norway, Quebec, Iceland | 10–30 | `50` |
| France, Sweden, Ontario | 30–80 | `100` |
| California (midday solar) | 0–150 | `150`–`200` |
| UK | 100–300 | `200` |
| Germany, US average | 200–500 | `300` |
| Poland, India, Australia | 400–800 | `500` (or use multi-zone) |

If your region never goes below your threshold, use **multi-zone mode** or **`auto:green`** to route work to a cleaner region.

## Troubleshooting

**`forecast_green_at = none_in_forecast`** — The grid isn't expected to go below your threshold within the forecast horizon (48h for UK, varies for others). Consider raising your threshold or using multi-zone mode.

**`carbon_intensity = unknown`** — The API couldn't be reached. Check your API key and network. Set `fail_on_api_error: 'true'` to make these failures visible instead of silently skipping.

**EIA rate limiting** — If you see `429` errors, you're hitting the DEMO_KEY limit (~30 req/hr). [Register a free EIA API key](https://www.eia.gov/opendata/register.php) for 1,000 req/hr.

**Electricity Maps zone not found** — Zone codes are case-sensitive and use the format shown on [app.electricitymaps.com/map](https://app.electricitymaps.com/map) (e.g., `DE`, `FR`, `AU-NSW`, `NO-NO1`).

**Multi-zone zones silently skipped** — If a zone requires an API key that isn't set (e.g., `DE` without `electricity_maps_token`), it's skipped with a warning. Check the action logs for "Skipping zone" messages.

**All timestamps are UTC** — The `forecast_green_at` output is in ISO 8601 UTC format. Convert to your local timezone as needed.

## License

[MIT](LICENSE)
