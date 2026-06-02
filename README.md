# Carbon-Aware Dispatcher

[![tests](https://github.com/peterklingelhofer/carbon-aware-dispatcher/actions/workflows/test.yml/badge.svg)](https://github.com/peterklingelhofer/carbon-aware-dispatcher/actions/workflows/test.yml) ![CO2 Saved](https://img.shields.io/badge/CO2_saved-green_CI-brightgreen?style=flat&logo=leaf&logoColor=white) ![Providers](https://img.shields.io/badge/providers-10-blue) ![Zones](https://img.shields.io/badge/zones-200%2B-blue) ![CI Platforms](https://img.shields.io/badge/CI-GitHub%20%7C%20GitLab%20%7C%20Bitbucket%20%7C%20CircleCI-orange)

Run your CI/CD only when the energy grid is clean. One file, no API keys, no configuration.

```yaml
# .github/workflows/carbon-aware-build.yml
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
        uses: actions/checkout@v5

      - if: steps.carbon.outputs.grid_clean == 'true'
        run: |
          echo "Running on clean energy in ${{ steps.carbon.outputs.grid_zone }}!"
          # your build/test/deploy commands here
```

That's it. The action auto-detects your cloud region (AWS, GCP, Azure) or checks 17+ zones across 8 free providers worldwide. Replace the `echo` with your actual build commands and you're done.

## How It Works

1. Runs on a schedule (e.g., hourly)
2. Fetches real-time fuel mix data and calculates carbon intensity (gCO2eq/kWh)
3. If below your threshold: sets `grid_clean=true`, your build runs on clean energy
4. If above: sets `grid_clean=false`, skips the build, reports forecast for next green window

**Use cases:** ML training, batch processing, media rendering, database migrations: any non-urgent job that can wait for clean energy.

## Presets

Instead of looking up zone codes, use a preset:

| Preset | What It Does |
|--------|-------------|
| *(no input)* | Auto-detects cloud region, falls back to checking all free zones worldwide |
| `auto:detect` | Detects AWS/GCP/Azure region from environment variables |
| `auto:nearest` | Picks zones closest to your timezone |
| `auto:green` | 10 curated green zones across 5 continents (free providers only) |
| `auto:cleanest` | Checks all free-provider zones, picks the single cleanest |
| `auto:green:full` | 21 zones including EU/Canada/NZ (requires API tokens) |
| `auto:escape-coal` | Routes jobs away from coal-heavy grids to clean alternatives |
| `auto:escape-coal:IN` | Escape from a specific dirty zone (IN, CN, PL, ZA, DE...) |

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zones: 'auto:green'           # or any preset above
    max_carbon_intensity: '200'        # gCO2eq/kWh threshold (default: 250)
```

## Quick Setup Options

### One-liner (generates workflow file for you)

```bash
curl -fsSL https://raw.githubusercontent.com/peterklingelhofer/carbon-aware-dispatcher/main/setup.sh | bash
```

Options: `--threshold 200`, `--zones "auto:green"`, `--strategy queue`, `--cron "0 6 * * *"`. Run with `--help` for details.

### Reusable workflow (no files to copy)

Other repos can call the carbon check directly:

```yaml
jobs:
  green-check:
    uses: peterklingelhofer/carbon-aware-dispatcher/.github/workflows/carbon-check.yml@v1
    with:
      max_carbon_intensity: '200'

  build:
    needs: green-check
    if: needs.green-check.outputs.grid_clean == 'true'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - run: echo "Building on clean energy!"
```

### Specific zone (e.g., US, UK, EU)

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  id: carbon
  with:
    grid_zone: 'CISO'                 # California ISO (see Supported Zones below)
    max_carbon_intensity: '200'
```

For EU zones, add a free ENTSO-E token (`entsoe_token`). For other global zones, add a free Electricity Maps token (`electricity_maps_token`). US, UK, Australia, India, Brazil, and South Africa zones need no keys.

### Dispatch mode (trigger a separate workflow)

If you prefer a gatekeeper pattern with a separate heavy workflow:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zone: 'CISO'
    max_carbon_intensity: '200'
    workflow_id: 'heavy-batch.yml'        # triggers this workflow when green
    github_token: ${{ secrets.GITHUB_TOKEN }}
```

The target workflow needs a `workflow_dispatch` trigger. For most users, inline mode (the default, shown at the top) is simpler.

## Multi-Zone Routing

Check multiple zones and run in the greenest one:

```yaml
jobs:
  pick-region:
    runs-on: ubuntu-latest
    outputs:
      runner: ${{ steps.carbon.outputs.runner_label }}
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
      - uses: actions/checkout@v5
      - run: echo "Building in ${{ needs.pick-region.outputs.runner }}"
```

> **Prerequisite for region routing:** `runs-on: ${{ needs.pick-region.outputs.runner }}`
> only works if the `runner_label` resolves to a runner that actually exists. The
> `grid_zones` `zone:label` syntax (e.g. `CISO:us-west-runner`) maps each zone to a
> runner label, but GitHub-hosted runners (`ubuntu-latest` etc.) have no concept of
> geographic region, so the labels must match **self-hosted runners** you have
> registered with those labels, or a provider like [RunsOn](#runson-integration)
> that interprets region labels. If you only have GitHub-hosted runners, use the
> time-shift pattern above (gate steps on `grid_clean`) rather than region routing,
> or consume the `cloud_region` / `gcp_region` / `azure_region` outputs to target
> regions in your own deploy steps.

The action always outputs cloud regions for all three major providers:

| Output | Example |
|--------|---------|
| `cloud_region` | `us-west-1` (AWS) |
| `gcp_region` | `us-west1` (GCP) |
| `azure_region` | `westus2` (Azure) |

These are set regardless of which provider you use, useful for routing deployments, caches, or infrastructure.

### RunsOn integration

[RunsOn](https://runs-on.com) supports per-job AWS region selection. Set `runner_provider: 'runson'` for automatic region-aware labels:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  id: carbon
  with:
    grid_zones: 'CISO,BPAT,PJM,GB'
    runner_provider: 'runson'
    runner_spec: '2cpu-linux-x64'       # optional, this is the default
```

The `runner_label` output will be a RunsOn-compatible label like `runs-on=12345/runner=2cpu-linux-x64/region=us-west-1`.

## Escape from Coal-Heavy Grids

In India, China, Poland, South Africa, or other coal-dependent regions? Route your jobs to the nearest clean alternative:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zones: 'auto:escape-coal'       # global clean routing
    # Or escape from a specific zone:
    # grid_zones: 'auto:escape-coal:IN'  # India → Iceland, Norway, France
    # grid_zones: 'auto:escape-coal:CN'  # China → NZ, Tasmania, Pacific NW
    # grid_zones: 'auto:escape-coal:PL'  # Poland → Nordic clean
    # grid_zones: 'auto:escape-coal:ZA'  # South Africa → Iceland, Norway
    max_carbon_intensity: '150'
```

## Smart Wait & Queue Strategy

### Wait for a green window

Instead of just checking once, wait up to N minutes for the grid to become clean:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zone: 'CISO'
    max_wait: '120'                      # wait up to 2 hours
    enable_forecast: 'true'
```

The action uses forecast data to sleep efficiently. **Note:** GitHub Actions bills for wait time.

### Find the optimal green window

`strategy: queue` searches forecasts across all zones to find the best time within your deadline:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  id: carbon
  with:
    grid_zones: 'auto:cleanest'
    strategy: 'queue'
    deadline_hours: '24'                 # find best window in next 24h
    max_wait: '120'                      # actually wait if window is within 2h
```

Outputs `optimal_dispatch_at` (ISO 8601 timestamp) and `optimal_zone`. Great for nightly ML training or weekly reports.

## Organization-Wide Defaults

Drop a `.github/carbon-policy.yml` in your repo:

```yaml
# .github/carbon-policy.yml
max_carbon_intensity: 150
grid_zones: auto:cleanest
enable_forecast: true
strategy: queue
deadline_hours: 24
```

Action inputs always override policy values. This lets platform teams enforce green CI defaults across all workflows.

## Other CI Platforms

The core Python script works on any CI platform. Ready-to-use templates in [`ci-templates/`](ci-templates/):

| Platform | Template | How It Works |
|----------|----------|-------------|
| GitLab CI | [`gitlab-ci.yml`](ci-templates/gitlab-ci.yml) | Extend `.carbon-aware-job` in your jobs |
| Bitbucket | [`bitbucket-pipelines.yml`](ci-templates/bitbucket-pipelines.yml) | Artifact-based result passing |
| CircleCI | [`circleci-config.yml`](ci-templates/circleci-config.yml) | Workspace persistence between jobs |

Set `GRID_ZONE`, `MAX_CARBON`, and optional API tokens as environment variables.

## Example Workflows

Ready-to-copy files in [`examples/`](examples/):

| Example | Description |
|---------|-------------|
| [`zero-config.yml`](examples/zero-config.yml) | Simplest setup, no inputs needed |
| [`multi-cloud-routing.yml`](examples/multi-cloud-routing.yml) | Route to greenest AWS/GCP/Azure region |
| [`queue-strategy.yml`](examples/queue-strategy.yml) | Find optimal green window within a deadline |
| [`escape-coal.yml`](examples/escape-coal.yml) | Escape dirty grids (India, China, Poland, SA) |

## Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `grid_zone` | `auto:detect` | Single zone or preset. See [Presets](#presets) and [Supported Zones](#supported-zones). |
| `grid_zones` | `auto:detect` | Comma-separated zones with optional runner labels: `CISO:runner-cal,GB:runner-uk`. Or a preset. |
| `max_carbon_intensity` | `250` | Maximum gCO2eq/kWh to allow dispatch. |
| `workflow_id` | — | Workflow to dispatch when green. Omit for inline mode (recommended). |
| `github_token` | — | Required when `workflow_id` is set. |
| `eia_api_key` | — | Higher rate limits for US zones. [Free registration](https://www.eia.gov/opendata/register.php). Built-in demo key works for basic use. |
| `electricity_maps_token` | — | Global coverage (200+ zones). [Free registration](https://portal.electricitymaps.com/), 50 req/hr. |
| `entsoe_token` | — | EU coverage (36 countries). [Free registration](https://transparency.entsoe.eu/), 400 req/min. |
| `gridstatus_api_key` | — | US forecasts (7 ISOs). [Free registration](https://www.gridstatus.io), 1M rows/month. |
| `max_wait` | `0` | Minutes to wait for green energy. Max 360. Billable time. |
| `enable_forecast` | `false` | Fetch forecast when dirty. Free for UK, India, Brazil, SA, Open-Meteo. US needs GridStatus key. |
| `strategy` | `check` | `check`: dispatch if green now. `queue`: find optimal window within `deadline_hours`. |
| `deadline_hours` | `24` | Hours to search ahead for green windows (queue strategy). |
| `runner_provider` | — | Set to `runson` for automatic AWS region-based runner labels. |
| `runner_spec` | `2cpu-linux-x64` | Machine spec for RunsOn. |
| `target_ref` | `main` | Git ref for dispatched workflows. |
| `fail_on_api_error` | `false` | Fail the action on API errors instead of skipping silently. |
| `carbon_policy_path` | `.github/carbon-policy.yml` | Path to org-wide carbon policy. |

## Outputs

| Output | Description |
|--------|-------------|
| `grid_clean` | `true` if a zone was clean enough, `false` otherwise. |
| `carbon_intensity` | Intensity in gCO2eq/kWh, or `unknown` on error. |
| `grid_zone` | Selected zone. |
| `runner_label` | Runner label for the selected zone. |
| `cloud_region` / `gcp_region` / `azure_region` | Nearest region for each cloud provider. Always set. |
| `intensity_trend` | `decreasing`, `increasing`, or `stable`. |
| `forecast_green_at` | ISO 8601 timestamp of next predicted green window. |
| `forecast_intensity` | Predicted intensity at the green window. |
| `co2_saved_grams` | Estimated grams CO2 saved vs. global average (450 gCO2eq/kWh). |
| `carbon_badge_url` | Shields.io badge URL for READMEs: `![carbon](url)` |
| `optimal_dispatch_at` | Best green window (queue strategy). `now` if already green. |
| `optimal_zone` | Zone for the optimal window (queue strategy). |
| `suggested_cron` | Suggested cron schedule for green builds based on zone energy type. |

## Supported Zones & Providers

The action auto-detects the best provider for each zone. Free providers are checked first.

| Provider | Coverage | API Key | Zones |
|----------|----------|---------|-------|
| [EIA](https://www.eia.gov/opendata/) | US (60+ regions) | Free built-in | `CISO`, `ERCO`, `PJM`, `BPAT`, `NYIS`, `MISO`, `ISNE`, `SWPP`... |
| [UK Carbon Intensity](https://carbonintensity.org.uk/) | UK (18 regions) | None | `GB`, `GB-1`..`GB-17` |
| [AEMO](https://aemo.com.au/) | Australia (5 states) | None | `AU-NSW`, `AU-QLD`, `AU-VIC`, `AU-SA`, `AU-TAS` |
| [Grid India](https://report.grid-india.in/) | India (5 regions) | None | `IN-NO`, `IN-SO`, `IN-EA`, `IN-WE`, `IN-NE` |
| [ONS Brazil](https://integra.ons.org.br/) | Brazil (5 regions) | None | `BR-S`, `BR-SE`, `BR-CS`, `BR-NE`, `BR-N` |
| [Eskom](https://www.eskom.co.za/) | South Africa | None | `ZA` |
| [ENTSO-E](https://transparency.entsoe.eu/) | EU (36 countries) | Free token | `DE`, `FR`, `ES`, `NL`, `NO-NO1`, `SE-SE1`..`SE-SE4`, `DK-DK1`... |
| [Electricity Maps](https://www.electricitymaps.com/) | Global (200+) | Free token | Any zone on [their map](https://app.electricitymaps.com/map) |
| [Open-Meteo](https://open-meteo.com/) | Worldwide (90+) | None | Auto-fallback for any zone with known coordinates |
| [GridStatus](https://www.gridstatus.io) | US forecasts (7 ISOs) | Free token | `CISO`, `ERCO`, `ISNE`, `MISO`, `NYIS`, `PJM`, `SWPP` |

**Provider priority:** UK > EIA > AEMO > Grid India > ONS Brazil > Eskom > ENTSO-E (with token) > Open-Meteo (with coordinates) > Electricity Maps (catch-all). If a primary provider fails, the action automatically falls back to Open-Meteo weather-based estimation.

### Forecasts

| Region | Source | Details |
|--------|--------|---------|
| UK | Carbon Intensity API | 48h free forecast, automatic |
| US | GridStatus.io | Solar/wind/load forecasts. Requires `gridstatus_api_key`. |
| EU | ENTSO-E | Day-ahead generation. Requires `entsoe_token`. |
| India | Heuristic | Solar peak 10am–4pm IST. Southern grid (IN-SO) cleanest. Automatic. |
| Brazil | Heuristic | Hydro off-peak cleanest. Evening peak 17–21h BRT dirtier. Automatic. |
| South Africa | Heuristic | Coal-dominant, rarely < 650 gCO2eq/kWh. Recommends escape-coal. |
| Other | Open-Meteo | 48h solar/wind weather forecast. Automatic for 90+ zones. |

### Choosing a Threshold

| Region | Typical Range (gCO2eq/kWh) | Suggested Threshold |
|--------|---------------------------|-------------------|
| Norway, Iceland, Quebec, Paraguay | 10–30 | `50` |
| France, Sweden, Ontario, Brazil (hydro) | 30–80 | `100` |
| California (midday), Costa Rica | 0–150 | `150`–`200` |
| UK, New Zealand | 100–300 | `200` |
| Germany, US average | 200–500 | `300` |
| Poland, Australia (coal states) | 400–800 | `500` or use `auto:escape-coal` |
| India, South Africa | 600–900 | Use `auto:escape-coal:IN` / `auto:escape-coal:ZA` |

### How Carbon Intensity Is Calculated

US zones: real-time hourly fuel mix from EIA, weighted by lifecycle emission factors (Coal: 820, Gas: 490, Oil: 650, Nuclear/Solar/Wind/Hydro: 0 gCO2eq/kWh). UK: pre-calculated by the Carbon Intensity API. Global: direct values from Electricity Maps or ENTSO-E generation mix. Open-Meteo: weather-based estimation from solar irradiance and wind speed.

## Setup Wizard

Validate your configuration before deploying:

```bash
# Test common zones
uv run setup_wizard.py

# Test specific zones
uv run setup_wizard.py --zone CISO
uv run setup_wizard.py --zones "CISO,GB,DE,AU-NSW"

# With API keys
uv run setup_wizard.py --zones "DE,FR" --electricity-maps-token YOUR_TOKEN
```

## Why Carbon-Aware CI/CD?

Data centers consume **2.7% of Europe's energy**. A 2025 study estimates GitHub Actions alone produced **~457 metric tons of CO2e in 2024**, equivalent to the carbon captured by 7,615 urban trees per year ([Saavedra et al., 2025](https://arxiv.org/abs/2510.26413)). Carbon intensity varies dramatically: California swings from 400+ gCO2eq/kWh (evening gas) to near-zero (midday solar). Simply shifting *when* and *where* batch jobs run yields **20-50% carbon reductions** with zero code changes.

| Study | Key Finding |
|-------|-------------|
| [Claßen et al., 2023](https://arxiv.org/abs/2310.18718) | Analyzed 7,392 GitHub Actions workflows. Scheduling CI/CD based on grid intensity effectively reduces emissions. |
| [Saavedra et al., 2025](https://arxiv.org/abs/2510.26413) | 2.2M runs across 18K repos. Recommends deploying runners in cleaner regions (France, UK). |
| [CarbonScaler (Hanafy et al., 2023)](https://arxiv.org/abs/2302.08681) | Up to **51% carbon savings** by adjusting compute based on real-time grid intensity. |
| [Sukprasert et al., 2023](https://arxiv.org/abs/2306.06502) | Even simple scheduling policies capture most achievable carbon reductions. |
| [Yang et al., 2025](https://arxiv.org/abs/2508.05949) | Survey of 50+ works reports 10–51% emission reductions from carbon-aware scheduling. |

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `carbon_intensity = unknown` | API unreachable. Check API key/network. Set `fail_on_api_error: 'true'` to surface errors. |
| `forecast_green_at = none_in_forecast` | Grid won't go below threshold in forecast horizon. Raise threshold or use multi-zone / `auto:green`. |
| EIA `429` errors | Hitting demo key limit (~30 req/hr). [Register free](https://www.eia.gov/opendata/register.php) for 1,000 req/hr. |
| Zones silently skipped | Zone needs API token that isn't set. Check logs for "Skipping zone" messages. |
| Zone not found (Electricity Maps) | Zone codes are case-sensitive. Check [app.electricitymaps.com/map](https://app.electricitymaps.com/map). |

All timestamps are UTC (ISO 8601).

## License

[MIT](LICENSE)
