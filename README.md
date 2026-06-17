# Carbon-Aware Dispatcher

[![tests](https://github.com/peterklingelhofer/carbon-aware-dispatcher/actions/workflows/test.yml/badge.svg)](https://github.com/peterklingelhofer/carbon-aware-dispatcher/actions/workflows/test.yml) ![Providers](https://img.shields.io/badge/providers-12-blue) ![Zones](https://img.shields.io/badge/zones-200%2B-blue) ![CI Platforms](https://img.shields.io/badge/CI-GitHub%20%7C%20GitLab%20%7C%20Bitbucket%20%7C%20CircleCI-orange)

<!--
  Live lifetime-CO2-saved badge, powered by this repo eating its own dog food
  (.github/workflows/self-track.yml). Once you have created the ledger gist and
  set the CARBON_LEDGER_GIST variable, replace GIST_ID below and add this badge
  to the row above:
  ![CO2 saved](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/peterklingelhofer/GIST_ID/raw/carbon-badge.json)
-->

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
presets. Optional free tokens add coverage: `entsoe_token` (EU, 44 zones), `electricity_maps_token`
(one registered zone on the free tier), `gridstatus_api_key` (US forecasts). `eia_api_key`
is optional too, only to raise the built-in US demo key's rate limit. See [Inputs](#inputs).

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

### Cost + carbon routing

Among multiple candidate zones, pick one that's both clean *and* cheap. Set
`cost_weight` (0–1) to blend each zone's carbon intensity with a representative
cloud price from the public Azure Retail Prices API (no key, keyed off each
zone's nearest Azure region):

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  id: carbon
  with:
    grid_zones: 'CISO,GB,FR,AU-NSW'
    cost_weight: '0.5'   # 0 = cleanest only, 1 = cheapest only, 0.5 = balance
```

The chosen zone minimizes `cost_weight x price + (1 - cost_weight) x carbon`
(both min-max normalized across the candidates). `selected_cost_usd_hr` reports
the winner's price. If pricing is unavailable it falls back to carbon-only. No
effect in single-zone mode. See [`examples/cost-aware-routing.yml`](examples/cost-aware-routing.yml).

**Other clouds (AWS/GCP/on-prem):** only Azure has a free live pricing API, so
to price any other cloud, supply your own rates via `cost_price_map`, a JSON
object of `zone -> USD/hour`. Mapped zones use your prices; the rest fall back to
live Azure:

```yaml
with:
  grid_zones: 'CISO,GB,FR'
  cost_weight: '0.5'
  cost_price_map: '{"CISO":"0.096","GB":"0.101","FR":"0.088"}'
```

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

## Carbon-adaptive CI (the dial)

Skipping builds is a blunt instrument; teams want their CI to run. Instead of a
binary gate, the action classifies the grid into a `carbon_tier` so downstream
jobs can *right-size* their work: full matrix when green, critical-path when
amber, smoke test when red. CI always makes progress; the heaviest compute
shifts to the cleanest hours.

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  id: carbon
  with:
    tier_thresholds: '120,280'   # green <=120, amber <=280, red above
```

`carbon_tier` is `green`, `amber`, or `red` (and `carbon_tier_reason` explains
why). Use it to drive matrix includes, conditional steps, or job-level `if:`.
See [`examples/adaptive-ci.yml`](examples/adaptive-ci.yml) for a full matrix that
scales test suites to the tier.

## Carbon budgets as code

Cap how much CO2 your CI is allowed to emit per month. With the [ledger](#watch-your-impact)
enabled, the action tracks month-to-date emissions and exposes `budget_exceeded`,
so non-essential builds pause once you hit the cap and resume next month.

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  id: carbon
  with:
    ledger: 'gist:${{ vars.CARBON_LEDGER_GIST }}'
    gist_token: ${{ secrets.GIST_TOKEN }}
    monthly_budget_grams: '2000'   # 2 kg CO2eq/month
```

Then gate downstream work on it:

```yaml
build:
  needs: carbon
  if: needs.carbon.outputs.budget_exceeded != 'true'
```

Outputs: `budget_used_pct`, `budget_remaining_grams`, `budget_exceeded`, and
`budget_state` (`ok` / `warning` at 80% / `exceeded`). Budgeting needs the
`ledger` input, which is where month-to-date spend is tracked. See
[`examples/carbon-budget.yml`](examples/carbon-budget.yml).

## Doctor mode (diagnostics)

To check that a zone works and your token is wired up, run the action in
`mode: doctor` for a one-click health check. It probes each configured zone,
shows which provider handles it, whether a required token is set or missing,
and whether live data actually came back, plus which optional features are on.

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    mode: 'doctor'
    grid_zones: 'GB,FR,CISO'   # blank probes a keyless sample
```

Output (job summary):

| Zone | Provider | Token | Status | Detail |
|---|---|---|---|---|
| `GB` | uk_carbon_intensity | n/a | OK | 203 gCO2eq/kWh |
| `FR` | open_meteo | n/a | OK | 550 gCO2eq/kWh |

See [`examples/doctor.yml`](examples/doctor.yml).

## Marginal emissions (WattTime)

Average grid intensity tells you how clean the grid is overall; **marginal**
emissions (MOER) tell you the emissions of the generator that responds to *your*
added load, the signal that actually matters for deciding *when* to shift
flexible compute. With free WattTime credentials, the action emits a
`marginal_percentile` (0–100, lower = cleaner) and a `marginal_clean` flag:

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  id: carbon
  with:
    watttime_username: ${{ secrets.WATTTIME_USERNAME }}
    watttime_password: ${{ secrets.WATTTIME_PASSWORD }}
    marginal_max_percentile: '33'   # clean = cleanest third of the last 2 weeks
```

Gate deferrable work on `marginal_clean == 'true'`. WattTime's free tier covers
`CAISO_NORTH`; other regions need WattTime Pro. See
[`examples/marginal-timing.yml`](examples/marginal-timing.yml).

## Weekly digest

Run the action in `mode: digest` on a schedule to post (and keep updating) a
single GitHub issue summarizing your CI's carbon impact: builds, CO2
saved/emitted over the last 7 and 30 days, a daily-savings sparkline, lifetime
total, and budget status. It reads the same ledger your build workflow writes.

```yaml
permissions:
  issues: write
jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - uses: peterklingelhofer/carbon-aware-dispatcher@v1
        with:
          mode: 'digest'
          ledger: 'gist:${{ vars.CARBON_LEDGER_GIST }}'
          gist_token: ${{ secrets.GIST_TOKEN }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
```

See [`examples/weekly-digest.yml`](examples/weekly-digest.yml).

## Notifications

Get pinged when something actionable happens: the grid goes clean, a build is
deferred, or your carbon budget is blown. Point `notify_webhook` at a Slack,
Discord, or generic webhook (the payload shape is auto-detected from the URL):

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    notify_webhook: ${{ secrets.SLACK_WEBHOOK }}
    notify_on: 'green,exceeded'   # green | dirty | exceeded | always
```

Notifications never fail the build; a webhook error degrades to a warning.

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

## Use outside GitHub Actions (CLI & container)

CI is a small load. The real carbon wins are large, deferrable workloads:
nightly ML training, ETL, batch inference. The same engine ships as a standalone
`carbon-aware` CLI so any scheduler (cron, systemd timers, Kubernetes CronJobs,
Airflow, Nomad) can gate or time that work. It composes through exit codes, so no
glue code is needed:

```bash
pipx install carbon-aware-dispatcher        # or use the container (below)

# Run a batch job only if the grid is clean right now
carbon-aware check --zones auto:green --max-carbon 200 && ./train.sh

# Or block until a green window opens (up to 6h), then run
carbon-aware wait-for-green --zones GB,CISO --max-carbon 200 --max-wait 6h && ./train.sh

# Plan ahead: print the cleanest upcoming window from forecasts
carbon-aware best-window --zones GB --hours 24 --json

# Emit an SCI carbon report for sustainability reporting (energy/PUE/embodied)
carbon-aware report --zones GB --energy-kwh 12 --pue 1.12 --embodied-grams 40 > sci.json

# Best of all: shift a recurring job to its cleanest hour, once
carbon-aware suggest-cron --zones GB --energy-kwh 12
#  -> Suggested schedule: 0 12 * * *   (~150 kg CO2/yr cleaner than your average run time)

# For a multi-hour batch job, target the cleanest contiguous window
carbon-aware suggest-cron --zones GB --duration-hours 4 --energy-kwh 20
#  -> start a 4h job at 11:00 UTC (cleanest 4h window)

# WHERE often beats WHEN: move a flexible workload to the cleanest region
carbon-aware suggest-region --zones CISO,PJM,GB,FR --current PJM --energy-kwh 12
#  -> Run in CISO instead of PJM: ~N kg CO2/yr saved (mind latency/egress)

# Both at once: the cleanest (region, hour) across candidates
carbon-aware plan --zones CISO,GB,FR --current PJM --energy-kwh 12
#  -> Run your job in CISO at 03:00 UTC (cron: 0 3 * * *)

# Audit a whole repo: rank every shiftable schedule by savings
carbon-aware audit --zones GB --dir .github/workflows --energy-kwh 5
#  -> ranked list of crons to shift + total potential kg CO2/yr

# Inspect the hour-of-day curve the recommendation is based on
carbon-aware curve --zones GB

# Gut-check whether scheduling is even worth it here (flat grids: no)
carbon-aware worth-it --zones GB    # exit 0 worth it, 1 not, 2 can't assess
```

`report` writes a machine-readable [SCI](https://sci.greensoftware.foundation/)
record per run (energy, intensity, PUE, embodied, and total emitted) that
aggregates for CSRD / GHG-Protocol reporting.

### Shift the schedule once

For *recurring* jobs, blocking with `wait-for-green` keeps the machine powered on
while it polls; that idle energy is itself carbon. The higher-impact move is to
shift the schedule **once** to the grid's cleanest hour: it saves on every future
run with zero idle waste. `suggest-cron` recommends that cron expression from the
best signal available, a multi-day **historical hour-of-day curve**, else the
live forecast, else a per-zone heuristic. The curve comes from a free historical
API where one exists (GB today), and **otherwise builds itself**: with a
[ledger](#watch-your-impact) configured, each run records its `(hour, intensity)`
into a tiny per-zone aggregate, so `curve` / `worth-it` / `suggest-cron` start
working for any zone once ~6 different hours have been sampled. (A job that only
ever runs at one fixed hour won't fill the curve; the hourly self-check pattern
samples across the day.) Inspect it with `carbon-aware curve`. Reserve
`wait-for-green` for one-off, deadline-bound work.

**Or let it open the PR for you.** Run the action in `mode: suggest` (on a
schedule) pointed at a workflow file, and it opens a pull request moving that
workflow's daily cron to the cleanest hour; you just review and merge:

```yaml
permissions:
  contents: write
  pull-requests: write
jobs:
  suggest:
    runs-on: ubuntu-latest
    steps:
      - uses: peterklingelhofer/carbon-aware-dispatcher@v1
        with:
          mode: 'suggest'
          grid_zones: 'GB'
          suggest_target: '.github/workflows/nightly-batch.yml'
          github_token: ${{ secrets.GITHUB_TOKEN }}
```

Only simple daily crons are rewritten (cadence and minute preserved). See
[`examples/suggest-cron-pr.yml`](examples/suggest-cron-pr.yml).

And before adding any of this, ask `carbon-aware worth-it`: on a flat,
baseload-dominated grid the intensity barely moves across the day, so shifting
saves little; the tool will say so plainly rather than have you add complexity
for nothing.

Exit codes: `0` green/clean, `1` dirty or timed out, `2` no data. Info logs go to
stderr; stdout carries only the result (add `--json` for machine output).

For a container (no Python needed), pull the published image or build locally:

```bash
docker run --rm ghcr.io/peterklingelhofer/carbon-aware-dispatcher:latest \
  check --zones GB,CISO --max-carbon 200

# or build it yourself
docker build -t carbon-aware . && docker run --rm carbon-aware check --zones GB
```

Ready-to-copy schedulers: a [Kubernetes CronJob](examples/standalone/k8s-cronjob.yaml)
(carbon-gated via an initContainer) and a [cron/systemd wrapper](examples/standalone/cron-wrapper.sh).

## Example workflows

Ready-to-copy files in [`examples/`](examples/):

| Example | Description |
|---------|-------------|
| [`zero-config.yml`](examples/zero-config.yml) | Simplest setup, no inputs needed |
| [`multi-cloud-routing.yml`](examples/multi-cloud-routing.yml) | Route to greenest AWS/GCP/Azure region |
| [`queue-strategy.yml`](examples/queue-strategy.yml) | Find optimal green window within a deadline |
| [`escape-coal.yml`](examples/escape-coal.yml) | Escape dirty grids (India, China, Poland, SA) |
| [`track-impact.yml`](examples/track-impact.yml) | All-in-one: lifetime ledger, live badge, and sticky PR comment |
| [`adaptive-ci.yml`](examples/adaptive-ci.yml) | Scale the test matrix to the carbon tier (green/amber/red) |
| [`carbon-budget.yml`](examples/carbon-budget.yml) | Cap monthly CO2 and pause non-essential builds over budget |
| [`cost-aware-routing.yml`](examples/cost-aware-routing.yml) | Pick a zone that's both clean and cheap |
| [`weekly-digest.yml`](examples/weekly-digest.yml) | Weekly impact issue from the ledger |
| [`marginal-timing.yml`](examples/marginal-timing.yml) | Gate flexible compute on WattTime marginal emissions |
| [`doctor.yml`](examples/doctor.yml) | One-click diagnostic of zones, tokens, and live data |
| [`suggest-cron-pr.yml`](examples/suggest-cron-pr.yml) | Auto-open a PR shifting a workflow to the cleanest hour |

## Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `grid_zone` | `auto:detect` | Single zone or preset. See [Presets](#presets) and [Supported zones](#supported-zones--providers). |
| `grid_zones` | `auto:detect` | Comma-separated zones with optional runner labels: `CISO:runner-cal,GB:runner-uk`. Or a preset. |
| `max_carbon_intensity` | `250` | Maximum gCO2eq/kWh to allow dispatch. |
| `workflow_id` | none | Workflow to dispatch when green. Omit for inline mode (recommended). |
| `github_token` | none | Required when `workflow_id` is set. |
| `eia_api_key` | none | Higher rate limits for US zones. [Free registration](https://www.eia.gov/opendata/register.php). Built-in demo key works for basic use. |
| `electricity_maps_token` | none | One zone per free token (chosen at registration), 50 req/hr. Paid plans cover 200+ zones. [Register](https://portal.electricitymaps.com/). |
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
| `ledger` | none | Persist cumulative savings: `gist:<id>` (live badge + dashboard, needs `gist_token`) or `file:<path>`. See [Watch your impact](#watch-your-impact). |
| `gist_token` | none | Token with `gist` scope for the `gist:` ledger backend. Store as a secret. |
| `pr_comment` | `false` | Post a sticky carbon-verdict comment on pull requests. Needs `pull-requests: write`. |
| `tier_thresholds` | `150,300` | Two gCO2eq/kWh boundaries `green,amber` for the `carbon_tier` dial. See [Carbon-adaptive CI](#carbon-adaptive-ci-the-dial). |
| `monthly_budget_grams` | none | Monthly carbon cap in gCO2eq. Needs `ledger`. See [Carbon budgets](#carbon-budgets-as-code). |
| `cost_weight` | `0` | Blend cloud cost with carbon when choosing among zones (0 = clean only, 1 = cheap only). See [Cost + carbon routing](#cost--carbon-routing). |
| `notify_webhook` | none | Webhook URL for carbon-event notifications (Slack/Discord/generic). See [Notifications](#notifications). |
| `notify_on` | `green,exceeded` | Events to notify on: `green`, `dirty`, `exceeded`, or `always`. |

## Outputs

| Output | Description |
|--------|-------------|
| `grid_clean` | `true` if a zone was clean enough, `false` otherwise. |
| `carbon_intensity` | Intensity in gCO2eq/kWh, or `unknown` on error. |
| `carbon_tier` | Adaptive-CI dial: `green` / `amber` / `red` (plus `carbon_tier_reason`). |
| `budget_used_pct` / `budget_remaining_grams` / `budget_exceeded` / `budget_state` | Monthly carbon budget status (needs `monthly_budget_grams` + `ledger`). |
| `selected_cost_usd_hr` | Representative price of the selected zone when `cost_weight` > 0. |
| `grid_zone` | Selected zone. |
| `runner_label` | Runner label for the selected zone. |
| `cloud_region` / `gcp_region` / `azure_region` | Nearest region for each cloud provider. Always set. |
| `intensity_trend` | `decreasing`, `increasing`, or `stable`. |
| `forecast_green_at` | ISO 8601 timestamp of next predicted green window. |
| `forecast_intensity` | Predicted intensity at the green window. |
| `co2_saved_grams` | Estimated grams CO2 saved vs. global average (450 gCO2eq/kWh). |
| `co2_saved_equivalent` | Human-relatable phrase for this run's saving, e.g. `~1.8 km not driven`. |
| `carbon_badge_url` | Shields.io badge URL for READMEs: `![carbon](url)` |
| `co2_saved_total_grams` | Cumulative grams saved across all runs (requires the `ledger` input). |
| `co2_saved_total_equivalent` | Human-relatable phrase for the lifetime saving (requires `ledger`). |
| `lifetime_badge_url` | Live shields.io badge URL for lifetime CO2 saved (requires `ledger: gist:<id>`). |
| `status_badge_url` | Live shields.io badge URL for the current grid zone/intensity/tier (requires `ledger: gist:<id>`). |
| `optimal_dispatch_at` | Best green window (queue strategy). `now` if already green. |
| `optimal_zone` | Zone for the optimal window (queue strategy). |
| `suggested_cron` | Suggested cron schedule for green builds based on zone energy type. |
| `dry_run` | `true` when the action ran in report-only mode. |
| `would_defer` | In `dry_run` mode, `true` if the grid was dirty and the build would have been deferred under enforcement. |

## Methodology & accounting

Carbon claims are easy to inflate, so here is exactly what the numbers mean:

- **`co2_emitted_grams` (trust this one).** What the run actually produced:
  carbon intensity × estimated energy. This is the Green Software Foundation
  [SCI](https://sci.greensoftware.foundation/) operational term (embodied
  hardware excluded) and maps to GHG Protocol Scope 2 (location-based). It's
  the figure to report.
- **`co2_saved_grams` (a benchmark).** A comparison against a
  fixed global-average grid (450 gCO2eq/kWh): "how much cleaner than a
  world-average grid was this run". It's **not** marginal or *additional*
  avoided emissions: on a grid where shifted load just rides baseload, the real
  avoided emissions can be far lower. The basis is stated in the
  `co2_saved_basis` output so it travels with the number.
- **Marginal intensity isn't available everywhere.** The metric that reflects true avoided
  emissions from shifting load is *marginal* intensity, which is free only for
  `CAISO_NORTH` (via WattTime, see [Marginal emissions](#marginal-emissions-watttime)).
  We don't fake it for other regions.

Use `co2_emitted_grams` for reporting and `co2_saved_grams` as a
directional benchmark that makes no offset claim.

**Make the energy figure real.** By default emitted assumes a typical CI job
(50 W for 15 min). For actual workloads, a GPU training run or an ETL batch, set
your real energy so the number means something:

```yaml
with:
  job_energy_kwh: '12'        # measured kWh (best); overrides the estimate
  # or describe it:
  # job_power_watts: '300'
  # job_duration_minutes: '120'
```

For a fuller [SCI](https://sci.greensoftware.foundation/) total, add datacenter
overhead and embodied hardware carbon (both opt-in, default off):

```yaml
with:
  pue: '1.12'             # facility energy / IT energy (cloud DCs run ~1.1-1.2)
  embodied_grams: '40'    # amortized manufacturing CO2 for this run
```

`co2_emitted_grams` then equals `energy x intensity x PUE + embodied`.

## Watch your impact

Every run estimates the CO2 it saved, but a number that vanishes after one build
is easy to ignore. These features make the impact persistent and visible to
everyone who reads the repo, including people who never open the Actions tab.

### Human-relatable equivalents

The job summary, the `co2_saved_equivalent` output, and the PR comment translate
grams into things people feel: `~1.8 km not driven`, `~14 phone charges`. Factors
come from the US EPA Greenhouse Gas Equivalencies Calculator, with nothing to enable.

### Lifetime ledger and live badge

Set the `ledger` input to accumulate savings across every run into a lifetime
total, exposed via `co2_saved_total_grams` and a live, self-updating badge.

```yaml
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    ledger: gist:YOUR_GIST_ID        # or file:.carbon/ledger.json
    gist_token: ${{ secrets.GIST_TOKEN }}
```

One-time setup for the gist backend:

1. Create a public gist (any placeholder content) and copy its id from the URL.
2. Create a personal access token with the `gist` scope and store it as a secret
   named `GIST_TOKEN`. (The built-in `GITHUB_TOKEN` can't write gists.)
3. The action writes `carbon-ledger.json` (full data) and `carbon-badge.json`
   (a shields.io endpoint badge) to the gist on every run.

Embed the live lifetime badge in your README:

```markdown
![CO2 saved](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/YOUR_USER/YOUR_GIST_ID/raw/carbon-badge.json)
```

The `file:` backend needs no token and writes a local JSON file, handy for
self-hosted runners or if you commit the ledger yourself.

A second, **live current-grid** badge is published alongside it (`carbon-now.json`),
showing the latest zone, intensity, and tier color, and exposed via the
`status_badge_url` output:

```markdown
![grid now](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/YOUR_USER/YOUR_GIST_ID/raw/carbon-now.json)
```

### Impact dashboard (GitHub Pages)

[`dashboard/index.html`](dashboard/index.html) is a self-contained, no-build,
no-CDN page that reads your ledger gist and renders the lifetime total, real-world
equivalents, and a savings-over-time chart. Drop the `dashboard/` folder on
GitHub Pages and open it with `?gist=<id>`:

```
https://YOUR_USER.github.io/YOUR_REPO/?gist=YOUR_GIST_ID
```

It reads the gist through the CORS-enabled GitHub REST API, so it works for any
public ledger gist with zero server-side code.

### Sticky PR comment

Set `pr_comment: 'true'` to post the carbon verdict as a single comment on the
pull request, updated in place on each run, so reviewers see whether the build
ran on clean energy (and how much it saved) without opening the Actions tab.

```yaml
permissions:
  pull-requests: write
# ...
- uses: peterklingelhofer/carbon-aware-dispatcher@v1
  with:
    pr_comment: 'true'
```

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
| [Electricity Maps](https://www.electricitymaps.com/) | 1 zone (free tier) / 200+ (paid) | Token | The single zone registered to your token; see [their map](https://app.electricitymaps.com/map) |
| [Open-Meteo](https://open-meteo.com/) | Worldwide (90+) | None | Auto-fallback for any zone with known coordinates |
| [GridStatus](https://www.gridstatus.io) | US forecasts (7 ISOs) | Free token | `CISO`, `ERCO`, `ISNE`, `MISO`, `NYIS`, `PJM`, `SWPP` |

**Provider priority:** UK > EIA > AEMO > Grid India > ONS Brazil > Eskom > Canada > Taiwan > ENTSO-E (with token) > Open-Meteo (with coordinates) > Electricity Maps (last resort; free tier is one registered zone). If a primary provider fails, the action automatically falls back to Open-Meteo weather-based estimation.

**Reliability notes:**
- **Grid India** is reachable only from Indian IPs, so it always fails from GitHub-hosted (US/EU) runners. India zones are therefore left out of the curated `auto:*` presets. They still work if you pass `grid_zones: 'IN-SO'` explicitly from a runner inside India.
- **`auto:detect`** needs a cloud-region environment variable, which GitHub-hosted runners don't provide. On those runners it falls back to `auto:cleanest` (greenest free zone worldwide) and says so in the log. Set `grid_zones` explicitly to pin a region.

### Forecasts

| Region | Source | Details |
|--------|--------|---------|
| UK | Carbon Intensity API | 48h free forecast, automatic |
| US | GridStatus.io | Solar/wind/load forecasts. Requires `gridstatus_api_key`. |
| EU | ENTSO-E | Real day-ahead forecast: wind+solar (A69) and load (A65) forecasts give the hourly renewable share. Requires `entsoe_token`. |
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
