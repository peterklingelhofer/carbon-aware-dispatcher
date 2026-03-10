# Carbon-Aware Dispatcher

A GitHub Action that delays compute-heavy CI/CD workflows until the energy grid is powered by clean, renewable energy. Runs on a cron schedule as a gatekeeper: checks live carbon intensity data, and dispatches your heavy workflow only when the grid is green.

**Zero config: no API keys required.** Uses the [EIA API](https://www.eia.gov/opendata/) for US zones and the [UK Carbon Intensity API](https://carbonintensity.org.uk/) for UK zones. Both are free, open, and need no authentication.

**Multi-zone support:** provide multiple grid zones (optionally mapped to self-hosted runner labels) and the action picks the zone with the lowest carbon intensity, routing your workload to the greenest available region.

## How It Works

1. Action runs on a cron schedule (e.g., hourly)
2. Fetches real-time fuel mix data and calculates carbon intensity
3. If intensity is below your threshold, dispatches your heavy workflow
4. If the grid is dirty, reports the trend (and forecast for UK), then exits cleanly

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
  workflow_dispatch:        # Allow manual triggers for testing

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

That's it: no API keys, no secrets to configure.

## Quick Start (UK)

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    grid_zone: 'GB'                  # or GB-13 for London, GB-16 for Scotland
    max_carbon_intensity: '150'
    workflow_id: 'heavy-batch.yml'
    github_token: ${{ secrets.GITHUB_TOKEN }}
```

UK zones also get free 48h forecasts; the action will tell you when the grid is expected to be green.

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

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `grid_zone` | No* | none | Single zone (US: EIA BA code, UK: `GB`/`GB-1`..`GB-17`) |
| `grid_zones` | No* | none | Comma-separated zones, optionally with runner labels |
| `eia_api_key` | No | none | Optional EIA API key for higher rate limits. [Register free](https://www.eia.gov/opendata/). Built-in `DEMO_KEY` works for basic use. |
| `max_carbon_intensity` | No | `250` | Maximum gCO2eq/kWh to allow dispatch |
| `workflow_id` | Yes | none | Filename of the workflow to dispatch |
| `github_token` | Yes | none | GitHub token with Actions write permission |
| `target_ref` | No | `main` | Git ref to dispatch the workflow on |
| `fail_on_api_error` | No | `false` | Fail the action on API errors instead of skipping |
| `enable_forecast` | No | `false` | Fetch forecast when grid is dirty (UK zones always get this free) |

\* One of `grid_zone` or `grid_zones` is required.

## Outputs

| Output | Description |
|--------|-------------|
| `grid_clean` | `true` if a zone was clean enough, `false` otherwise |
| `carbon_intensity` | Carbon intensity in gCO2eq/kWh, or `unknown` on error |
| `grid_zone` | The zone that was selected |
| `runner_label` | Runner label for the selected zone (multi-zone mode) |
| `intensity_trend` | Recent trend: `decreasing`, `increasing`, or `stable` |
| `forecast_green_at` | ISO 8601 timestamp of next predicted green window (UK only) |
| `forecast_intensity` | Predicted intensity at the next green window (UK only) |

## Forecast & Trend

When the grid is over threshold, the action provides extra context:

**Trend (US + UK):** Reports whether intensity is `decreasing`, `increasing`, or `stable` based on recent hourly history.

**Forecast (UK only):** Predicts when the grid will next be below your threshold, up to 48h ahead. Automatic and free.

```yaml
- if: steps.carbon-check.outputs.grid_clean == 'false'
  run: |
    echo "Grid dirty. Trend: ${{ steps.carbon-check.outputs.intensity_trend }}"
    echo "Next green window: ${{ steps.carbon-check.outputs.forecast_green_at }}"
```

## Supported Zones

### US Zones (EIA API: No Key Required)

Uses hourly fuel mix data from the [U.S. Energy Information Administration](https://www.eia.gov/electricity/gridmonitor/) to calculate real-time carbon intensity using standard lifecycle emission factors.

| Zone | Region | Zone | Region |
|------|--------|------|--------|
| `CISO` | California ISO | `MISO` | Midcontinent |
| `ERCO` | Texas (ERCOT) | `ISNE` | New England |
| `PJM` | Mid-Atlantic/Midwest | `SWPP` | Southwest Power Pool |
| `NYIS` | New York ISO | `BPAT` | Bonneville Power |

60+ balancing authorities supported. Full list at [EIA Grid Monitor](https://www.eia.gov/electricity/gridmonitor/).

### UK Zones (Carbon Intensity API: No Key Required)

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

## How Carbon Intensity Is Calculated

For US zones, the action fetches the real-time hourly fuel mix from the EIA API and calculates carbon intensity using standard lifecycle emission factors:

| Fuel | gCO2eq/kWh | Fuel | gCO2eq/kWh |
|------|-----------|------|-----------|
| Coal | 820 | Nuclear | 0 |
| Natural Gas | 490 | Solar | 0 |
| Oil/Petroleum | 650 | Wind | 0 |
| Other | 200 | Hydro/Geo | 0 |

For UK zones, the Carbon Intensity API provides pre-calculated intensity values directly.

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

## License

[MIT](LICENSE)
