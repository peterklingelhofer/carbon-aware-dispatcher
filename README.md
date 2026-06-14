# Carbon-Aware Dispatcher

[![tests](https://github.com/peterklingelhofer/carbon-aware-dispatcher/actions/workflows/test.yml/badge.svg)](https://github.com/peterklingelhofer/carbon-aware-dispatcher/actions/workflows/test.yml) ![Providers](https://img.shields.io/badge/providers-12-blue) ![Zones](https://img.shields.io/badge/zones-200%2B-blue) ![CI Platforms](https://img.shields.io/badge/CI-GitHub%20%7C%20GitLab%20%7C%20Bitbucket%20%7C%20CircleCI-orange)

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

The action auto-detects your cloud region (AWS, GCP, Azure) or checks zones across free providers worldwide. Replace the `echo` with your build commands.

## How it works

1. Runs on a schedule (e.g. hourly)
2. Fetches real-time fuel mix and computes carbon intensity (gCO2eq/kWh)
3. Below your threshold: sets `grid_clean=true`, the build runs
4. Above it: sets `grid_clean=false`, skips the build, reports the next green window

Best for non-urgent jobs that can wait for clean energy: ML training, batch processing, media rendering, database migrations.

## Try it risk-free (report-only)

To try it without gating your builds, add the action with `dry_run: 'true'` and it
changes **nothing** about your workflow: it measures the grid, reports what it
*would* have done, and estimates the CO2 you'd save, all in the job summary. Run
it for a week, see the numbers, then turn enforcement on.

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  id: carbon
  with:
    grid_zones: 'auto:green'
    dry_run: 'true'          # report-only: never blocks the build
```

In report-only mode `grid_clean` is always `true` (so existing gates keep
passing); read the `would_defer` output to see the real verdict.

## Presets

Use a preset instead of looking up zone codes:

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

## API keys

**None required.** US, UK, Australia, India, Brazil, South Africa, and the
worldwide Open-Meteo fallback work with no setup, which covers the `auto:*`
presets. Optional free tokens add coverage: `entsoe_token` (EU), `electricity_maps_token`
(global, 200+ zones), `gridstatus_api_key` (US forecasts). `eia_api_key` is
optional too, only to raise the built-in US demo key's rate limit. See [Inputs](#inputs).

## Quick Setup Options

### One-liner (generates workflow file for you)

```bash
curl -fsSL https://raw.githubusercontent.com/peterklingelhofer/carbon-aware-dispatcher/main/setup.sh | bash
```

Options: `--threshold 200`, `--zones "auto:green"`, `--strategy queue`, `--cron "0 6 * * *"`. Run with `--help` for details.

### Reusable workflow (no files to copy)

Call the carbon check directly from another workflow:

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

US, UK, Australia, India, Brazil, and South Africa need no keys. EU zones use a free `entsoe_token`; other global zones use a free `electricity_maps_token`.

### Dispatch mode (trigger a separate workflow)

A gatekeeper pattern that triggers a separate heavy workflow when green:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zone: 'CISO'
    max_carbon_intensity: '200'
    workflow_id: 'heavy-batch.yml'        # triggers this workflow when green
    github_token: ${{ secrets.GITHUB_TOKEN }}
```

The target workflow needs a `workflow_dispatch` trigger. Inline mode (the default, shown at the top) is simpler for most users.

## Routing to a clean region

### Deploy to the greenest region (no special runners)

The most common case: keep CI on a standard GitHub-hosted runner, but send the
**deployment or workload** to whichever region is cleanest. The action always
outputs `cloud_region` / `gcp_region` / `azure_region`, so feed them straight
into your deploy step:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  id: carbon
  with:
    grid_zones: 'CISO,PJM,GB'
    max_carbon_intensity: '200'

- if: steps.carbon.outputs.grid_clean == 'true'
  run: |
    aws s3 sync ./dist s3://my-bucket --region ${{ steps.carbon.outputs.cloud_region }}
    # or: terraform apply -var region=${{ steps.carbon.outputs.cloud_region }}
    # or: gcloud run deploy --region ${{ steps.carbon.outputs.gcp_region }}
```

| Output | Example |
|--------|---------|
| `cloud_region` | `us-west-1` (AWS) |
| `gcp_region` | `us-west1` (GCP) |
| `azure_region` | `westus2` (Azure) |

This is usually what matters most: the CI runner is a short-lived machine, while
the deployed service or batch job is where the real energy is spent.

### Relocating the CI runner itself

To run the build *job* in a specific region, set `runs-on` from the action's
`runner_label`. This needs a two-job pattern, since `runs-on` is fixed at
job-definition time:

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

The `zone:label` syntax maps each zone to a runner label. **This only works if
the label matches a runner that exists.** GitHub-hosted runners (`ubuntu-latest`
etc.) have no region concept, so use **self-hosted runners** registered with those
labels, or [RunsOn](#runson-integration). With GitHub-hosted runners only, prefer
the deploy-region pattern above.

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

## Escape coal-heavy grids

Route jobs from a coal-dependent region to the nearest clean alternative:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zones: 'auto:escape-coal'       # global clean routing
    # Or escape from a specific zone:
    # grid_zones: 'auto:escape-coal:IN'  # India -> Iceland, Norway, France
    # grid_zones: 'auto:escape-coal:CN'  # China -> NZ, Tasmania, Pacific NW
    # grid_zones: 'auto:escape-coal:PL'  # Poland -> Nordic clean
    # grid_zones: 'auto:escape-coal:ZA'  # South Africa -> Iceland, Norway
    max_carbon_intensity: '150'
```

## Smart wait & queue strategy

### Wait for a green window

Wait up to N minutes for the grid to become clean:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zone: 'CISO'
    max_wait: '120'                      # wait up to 2 hours
    enable_forecast: 'true'
```

Forecast data is used to sleep efficiently. **Note:** GitHub Actions bills for wait time.

### Find the optimal green window

`strategy: queue` searches forecasts across all zones for the best time within your deadline:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  id: carbon
  with:
    grid_zones: 'auto:cleanest'
    strategy: 'queue'
    deadline_hours: '24'                 # find best window in next 24h
    max_wait: '120'                      # actually wait if window is within 2h
```

Outputs `optimal_dispatch_at` (ISO 8601) and `optimal_zone`. Good for nightly ML training or weekly reports.

## Organization-wide defaults

Drop a `.github/carbon-policy.yml` in your repo:

```yaml
# .github/carbon-policy.yml
max_carbon_intensity: 150
grid_zones: auto:cleanest
enable_forecast: true
strategy: queue
deadline_hours: 24
```

Action inputs override policy values, letting platform teams set green CI defaults across all workflows.

## Other CI platforms

The core Python script runs on any CI platform. Templates in [`ci-templates/`](ci-templates/):

| Platform | Template | How It Works |
|----------|----------|-------------|
| GitLab CI | [`gitlab-ci.yml`](ci-templates/gitlab-ci.yml) | Extend `.carbon-aware-job` in your jobs |
| Bitbucket | [`bitbucket-pipelines.yml`](ci-templates/bitbucket-pipelines.yml) | Artifact-based result passing |
| CircleCI | [`circleci-config.yml`](ci-templates/circleci-config.yml) | Workspace persistence between jobs |

Set `GRID_ZONE`, `MAX_CARBON`, and optional API tokens as environment variables.

## Example workflows

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
| `grid_zone` | `auto:detect` | Single zone or preset. See [Presets](#presets) and [Supported zones](#supported-zones--providers). |
| `grid_zones` | `auto:detect` | Comma-separated zones with optional runner labels: `CISO:runner-cal,GB:runner-uk`. Or a preset. |
| `max_carbon_intensity` | `250` | Maximum gCO2eq/kWh to allow dispatch. |
| `workflow_id` | none | Workflow to dispatch when green. Omit for inline mode (recommended). |
| `github_token` | none | Required when `workflow_id` is set. |
| `eia_api_key` | none | Higher rate limits for US zones. [Free registration](https://www.eia.gov/opendata/register.php). Built-in demo key works for basic use. |
| `electricity_maps_token` | none | Global coverage (200+ zones). [Free registration](https://portal.electricitymaps.com/), 50 req/hr. |
| `entsoe_token` | none | EU coverage (36 countries). [Free registration](https://transparency.entsoe.eu/), 400 req/min. |
| `gridstatus_api_key` | none | US forecasts (7 ISOs). [Free registration](https://www.gridstatus.io), 1M rows/month. |
| `max_wait` | `0` | Minutes to wait for green energy. Max 360. Billable time. |
| `enable_forecast` | `false` | Fetch forecast when dirty. Free for UK, India, Brazil, SA, Open-Meteo. US needs GridStatus key. |
| `strategy` | `check` | `check`: dispatch if green now. `queue`: find optimal window within `deadline_hours`. |
| `deadline_hours` | `24` | Hours to search ahead for green windows (queue strategy). |
| `runner_provider` | none | Set to `runson` for automatic AWS region-based runner labels. |
| `runner_spec` | `2cpu-linux-x64` | Machine spec for RunsOn. |
| `target_ref` | `main` | Git ref for dispatched workflows. |
| `fail_on_api_error` | `false` | Fail the action on API errors instead of skipping silently. |
| `carbon_policy_path` | `.github/carbon-policy.yml` | Path to org-wide carbon policy. |
| `dry_run` | `false` | Report-only mode. Measures and reports but never gates the build (`grid_clean` stays `true`). See [Try it risk-free](#try-it-risk-free-report-only). |
| `consumption_based` | `false` | Use flow-traced consumption intensity for EU zones (single-zone, needs `entsoe_token`). See [Consumption-based intensity](#consumption-based-intensity-eu). |

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
| `dry_run` | `true` when the action ran in report-only mode. |
| `would_defer` | In `dry_run` mode, `true` if the grid was dirty and the build would have been deferred under enforcement. |

## Supported zones & providers

The action picks the best provider per zone, checking free providers first.

| Provider | Coverage | API Key | Zones |
|----------|----------|---------|-------|
| [EIA](https://www.eia.gov/opendata/) | US (60+ regions) | Free built-in | `CISO`, `ERCO`, `PJM`, `BPAT`, `NYIS`, `MISO`, `ISNE`, `SWPP`... |
| [UK Carbon Intensity](https://carbonintensity.org.uk/) | UK (18 regions) | None | `GB`, `GB-1`..`GB-17` |
| [AEMO](https://aemo.com.au/) | Australia (5 states) | None | `AU-NSW`, `AU-QLD`, `AU-VIC`, `AU-SA`, `AU-TAS` |
| [Grid India](https://report.grid-india.in/) | India (5 regions) | None | `IN-NO`, `IN-SO`, `IN-EA`, `IN-WE`, `IN-NE` (geo-restricted, see note) |
| [ONS Brazil](https://integra.ons.org.br/) | Brazil (5 regions) | None | `BR-S`, `BR-SE`, `BR-CS`, `BR-NE`, `BR-N` |
| [Eskom](https://www.eskom.co.za/) | South Africa | None | `ZA` |
| [IESO / AESO / Hydro-Quebec](https://www.ieso.ca/) | Canada (ON, AB, QC) | None | `CA-ON`, `CA-AB`, `CA-QC` |
| [Taipower](https://www.taipower.com.tw/) | Taiwan | None | `TW` |
| [ENTSO-E](https://transparency.entsoe.eu/) | EU (36 countries) | Free token | `DE`, `FR`, `ES`, `NL`, `NO-NO1`, `SE-SE1`..`SE-SE4`, `DK-DK1`... |
| [Electricity Maps](https://www.electricitymaps.com/) | Global (200+) | Free token | Any zone on [their map](https://app.electricitymaps.com/map) |
| [Open-Meteo](https://open-meteo.com/) | Worldwide (90+) | None | Auto-fallback for any zone with known coordinates |
| [GridStatus](https://www.gridstatus.io) | US forecasts (7 ISOs) | Free token | `CISO`, `ERCO`, `ISNE`, `MISO`, `NYIS`, `PJM`, `SWPP` |

**Provider priority:** UK > EIA > AEMO > Grid India > ONS Brazil > Eskom > Canada > Taiwan > ENTSO-E (with token) > Open-Meteo (with coordinates) > Electricity Maps (catch-all). If a primary provider fails, the action automatically falls back to Open-Meteo weather-based estimation.

**Reliability notes:**
- **Grid India** is reachable only from Indian IPs, so it always fails from GitHub-hosted (US/EU) runners. India zones are therefore left out of the curated `auto:*` presets. They still work if you pass `grid_zones: 'IN-SO'` explicitly from a runner inside India.
- **`auto:detect`** needs a cloud-region environment variable, which GitHub-hosted runners don't provide. On those runners it falls back to `auto:cleanest` (greenest free zone worldwide) and says so in the log. Set `grid_zones` explicitly to pin a region.

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

Heuristic and Open-Meteo forecasts are time-of-day or weather estimates. The UK,
ENTSO-E, and GridStatus forecasts are measured day-ahead data. The job summary
labels the estimates "(estimated)" so the two are easy to tell apart.

### Choosing a threshold

| Region | Typical Range (gCO2eq/kWh) | Suggested Threshold |
|--------|---------------------------|-------------------|
| Norway, Iceland, Quebec, Paraguay | 10–30 | `50` |
| France, Sweden, Ontario, Brazil (hydro) | 30–80 | `100` |
| California (midday), Costa Rica | 0–150 | `150`–`200` |
| UK, New Zealand | 100–300 | `200` |
| Germany, US average | 200–500 | `300` |
| Poland, Australia (coal states) | 400–800 | `500` or use `auto:escape-coal` |
| India, South Africa | 600–900 | Use `auto:escape-coal:IN` / `auto:escape-coal:ZA` |

### How carbon intensity is calculated

Fuel-mix providers (EIA, AEMO, ENTSO-E, Grid India, ONS Brazil, Canada, Taipower) weight each source by its IPCC AR5 lifecycle factor in gCO2eq/kWh: coal 820, lignite 1050, gas 490, oil 650, biomass 230, solar 45, geothermal 38, hydro 24, wind 12, nuclear 12. Storage (battery, pumped hydro) is excluded. The UK API returns a pre-calculated value; Electricity Maps returns intensity directly; Open-Meteo estimates from solar irradiance and wind speed.

### Consumption-based intensity (EU)

By default the action reports **production-based** intensity (a zone's own
generation mix). Set `consumption_based: 'true'` (single-zone mode, with an
`entsoe_token`) to instead get **consumption-based** intensity, which flow-traces
imports and exports across the European network so a zone importing clean French
nuclear reads cleaner, and one importing German coal reads dirtier:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zone: 'IT-NO'           # Italy North, a heavy importer
    consumption_based: 'true'
    entsoe_token: ${{ secrets.ENTSOE_TOKEN }}
```

It uses ENTSO-E physical cross-border flows (documentType A11) and solves the
flow-tracing linear system (Tranberg et al., 2019) with Gauss-Seidel iteration,
no extra dependencies. Covered zones: FR, DE, NL, BE, CH, AT, ES, PT, IT-NO, PL,
CZ, GB, IE, DK-DK1. Zones outside this traced network fall back to production
intensity. Note: this costs extra ENTSO-E calls (one per traced zone plus its
borders), so enable it only when the import/export correction matters.

### Known limitations

- **Coverage is best where a free grid-operator API exists.** US, UK, EU (with a free ENTSO-E token), Australia, Canada, Taiwan, Brazil, India, and South Africa use real grid data. Other zones fall back to an Open-Meteo weather estimate, or to Electricity Maps if a token is set. Some regions (e.g. Japan, South Korea, Singapore) have no clean free real-time feed, so measured data there requires an Electricity Maps token.
- **Consumption-based intensity is EU-only and opt-in** (see above). Other regions report production-based intensity. For global consumption-based data, use a commercial source such as Electricity Maps.
- **Some forecasts are heuristic** (see [Forecasts](#forecasts)), labeled as estimates in the job summary.

## Setup wizard

Validate configuration before deploying:

```bash
# Test common zones
uv run setup_wizard.py

# Test specific zones
uv run setup_wizard.py --zone CISO
uv run setup_wizard.py --zones "CISO,GB,DE,AU-NSW"

# With API keys
uv run setup_wizard.py --zones "DE,FR" --electricity-maps-token YOUR_TOKEN
```

## Why carbon-aware CI/CD

GitHub Actions alone produced an estimated **~457 metric tons of CO2e in 2024** ([Saavedra et al., 2025](https://arxiv.org/abs/2510.26413)). Grid intensity swings widely: California ranges from 400+ gCO2eq/kWh (evening gas) to near-zero (midday solar). Shifting *when* and *where* batch jobs run yields **20-50% carbon reductions** with no code changes.

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

### Skipped-zone reasons

In multi-zone mode, the job summary lists any skipped zones with a reason so you
know whether to act:

| Reason | Meaning | What to do |
|--------|---------|------------|
| `auth failed` | The provider rejected the API key/token (HTTP 401/403). | Check the secret is set and valid. |
| `rate limited` | Hit the provider's rate limit (HTTP 429), even after retries. | Transient; add a paid/registered key, or it clears on its own. |
| `network error` | Could not reach the provider after retries. | Usually transient; the zone is retried next run. |
| `HTTP <code>` | An unexpected non-retryable response. | Check the provider's status; the zone code may be wrong. |
| `no electricity_maps_token` | Zone needs an Electricity Maps token and none was set. | Add `electricity_maps_token`, or use a keyless zone. |

A clean run never blocks on a skipped zone: it routes to the cleanest zone that
did respond.

All timestamps are UTC (ISO 8601).

## License

[MIT](LICENSE)
