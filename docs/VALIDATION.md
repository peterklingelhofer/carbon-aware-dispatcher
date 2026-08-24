# Validation

This document tests whether the tool's central claim holds, with measurements
against real grid data rather than with an argument.

Most carbon-aware scheduling tools report a savings number and stop. This one
grades itself, publishes the runs where shifting made emissions *worse*, and
publishes the case where its own accounting method disagrees with its own
savings figure. Where a check could not be run, it says so instead of quietly
omitting it.

Every number below is computed by [`scripts/run_validation.py`](../scripts/run_validation.py)
from the CSV snapshots committed under [`data/validation/`](../data/validation/),
which were fetched by [`scripts/fetch_validation_data.py`](../scripts/fetch_validation_data.py).
The analyses call this repo's own shipped functions (`carbon_curve.profile_from_samples`,
`marginal.estimate_marginal`, `providers.eia._fuel_mix_totals`) rather than
reimplementing them, so what is graded is the code that ships. To reproduce:

```bash
uv run python scripts/fetch_validation_data.py   # optional: refresh the snapshots
uv run python scripts/run_validation.py          # recomputes results.json and this file
```

The tables between `<!-- generated:... -->` markers are written by that script.
Editing them by hand will be overwritten.

## Summary of findings

| # | Question | Verdict |
|---|---|---|
| 1 | Is the GB (Great Britain) intensity the tool displays accurate? | **Yes, within ~10 gCO2eq/kWh.** The tool shows a forecast of the live half-hour, and it flips the green/dirty verdict at the 200 threshold 3.6% of the time |
| 2 | Does the diurnal curve beat doing nothing? | **No below 6 hours ahead.** Climatology is worse than persistence at every horizon below 6 h. This is a defect, documented below |
| 3 | Does deferring to a green window actually reduce emissions? | **Yes on average, but not always.** 14-18% of deferrals landed on a *dirtier* grid than the one they left |
| 4 | Is the savings number the badge shows what shifting avoided? | **No.** The ledger benchmark is 8-13x larger than the emissions the shift actually avoided |
| 5 | Does the saving survive marginal accounting? | **Not demonstrably.** Under the project's own marginal estimator the saving falls to roughly zero or goes negative. We can't yet tell whether that's the grid or the estimator |
| 6 | Is the CI power assumption sourced? | **Yes.** 13 W from published server power curves, with a 6-25 W error bar propagated into the outputs; the prior 50 W was about 4x high |
| 7 | Is the marginal estimator accurate? | **Unresolved, blocked.** No WattTime credential; see section 7 |

## Data

GB national carbon intensity, half-hourly, from the NESO / National Grid Carbon
Intensity API. Each settlement period carries both the forecast published ahead
of time and the settled actual, which is the ground truth a forecast study needs.
This is the only free feed the project consumes that grades itself.

<!-- generated:window -->
| Series | Rows | From (UTC) | To (UTC) |
|---|---|---|---|
| GB national, 30 min | 4320 | 2026-05-25T16:30:00+00:00 | 2026-08-23T16:00:00+00:00 |
| CISO, hourly fuel mix | 1430 | 2026-06-24T17:00:00+00:00 | 2026-08-23T06:00:00+00:00 |
| PJM, hourly fuel mix | 1427 | 2026-06-24T17:00:00+00:00 | 2026-08-23T03:00:00+00:00 |
<!-- /generated:window -->

US fuel mix, hourly, from the EIA v2 `fuel-type-data` endpoint for CISO and PJM:
one renewables-heavy zone and one fossil-heavy zone. Stored as raw per-fuel
generation rows rather than pre-computed intensities, so the analysis can be
re-run if the emission factors change.

Ninety days of one national grid in one season is a small sample. It's enough to
establish the direction and rough size of the effects below and not enough to
establish seasonal behaviour. Section 8 lists what that limits.

## 1. How wrong the badge number is

`providers/uk.py` reads `intensity.forecast` for the current half-hour, because
`intensity.actual` is still null while a period is live. That's the only
available choice, but it means the intensity the action reports, the badge shows
and the ledger banks is a **forecast of the current half-hour, made before any
measurement of it exists**. Once the period settles the actual arrives, so the error is measurable
after the fact.

<!-- generated:nowcast -->
| Metric | Value |
|---|---|
| Half-hours compared | 4320 |
| MAE | 9.8 gCO2eq/kWh |
| Bias (forecast - actual) | -1.2 gCO2eq/kWh |
| RMSE | 14.1 gCO2eq/kWh |
| 90th percentile absolute error | 21.0 gCO2eq/kWh |
<!-- /generated:nowcast -->

The number is good. What matters more than the average error is how often it
changes the *decision*, which is a threshold comparison:

<!-- generated:nowcast-flips -->
| Threshold | Verdict flips | of which false green |
|---|---|---|
| 100 gCO2eq/kWh | 249 (5.76%) | 145 (3.36%) |
| 150 gCO2eq/kWh | 282 (6.53%) | 149 (3.45%) |
| 200 gCO2eq/kWh | 157 (3.63%) | 47 (1.09%) |
| 250 gCO2eq/kWh | 6 (0.14%) | 1 (0.02%) |
<!-- /generated:nowcast-flips -->

"False green" is the consequential direction: the action reported the grid clean
and let the build run, when the settled figure was above the threshold. At the
200 gCO2eq/kWh threshold this repo's own `self-track.yml` uses, that happens in
about 1% of half-hours.

## 2. The forecast versus doing nothing

A forecast that can't beat "assume it stays put" isn't adding
information. Two naive baselines:

- **Persistence**: predict that intensity at `t + h` equals the actual at `t`.
- **Climatology**: predict the hour-of-day mean, built by the project's own
  `carbon_curve.profile_from_samples`. This is the fallback the tool uses for
  every zone whose provider publishes no forecast, which is most of them.

The diurnal profile is trained on the first 30 days and scored only on the
remaining 60, so climatology is never graded on data it was fitted to. Skill is
`1 - MAE_model / MAE_persistence`: positive means better than persistence,
negative means worse, zero means no better.

<!-- generated:horizon -->
| Horizon | Persistence MAE | Climatology MAE | Climatology skill | Published forecast MAE | Published skill |
|---|---|---|---|---|---|
| 0.5 h | 5.2 | 36.0 | -5.923 | 10.3 | -0.981 |
| 1 h | 9.5 | 36.0 | -2.789 | 10.3 | -0.084 |
| 2 h | 17.8 | 36.0 | -1.022 | 10.3 | 0.421 |
| 3 h | 25.2 | 36.0 | -0.429 | 10.3 | 0.591 |
| 6 h | 43.5 | 35.9 | 0.175 | 10.3 | 0.763 |
| 12 h | 58.9 | 35.6 | 0.396 | 10.3 | 0.825 |
| 24 h | 34.0 | 35.7 | -0.05 | 10.3 | 0.697 |
| 48 h | 47.0 | 35.6 | 0.243 | 10.3 | 0.781 |
<!-- /generated:horizon -->

**The headline result: the diurnal curve is worse than
persistence at every horizon below 6 hours.** At 30 minutes ahead it's nearly
seven times worse. This isn't a subtle statistical point: for short deferrals,
a zone that falls back to the heuristic curve would do better ignoring the curve
entirely and assuming the grid stays put. The curve only starts to earn its
place at 6 to 12 hours, which is exactly where a diurnal signal should
dominate and a persistence forecast should decay.

Two consequences, neither of which the code currently implements:

1. For heuristic-forecast zones with a short wait budget, persistence is the
   better predictor and the curve shouldn't be consulted.
2. `carbon_curve` output should carry the horizon it's valid for.

The 24 h row is the diurnal signature showing up in the baseline rather than in
the model: at a 24 h horizon persistence is itself a climatology, so both
predictors converge and neither has skill over the other.

### A caveat on the published-forecast column

The published-forecast MAE is flat across horizons because the API archive
stores **one** forecast per settlement period and doesn't record the lead time
at which it was issued. That column therefore says "the archived forecast of
record is accurate to about 10 gCO2eq/kWh", which is a real and useful result,
but it does **not** establish that a 48-hour-ahead forecast is as good as a
30-minute-ahead one. Its skill column rises with horizon only because
persistence, the denominator, gets worse.

Resolving this needs lead-time-stamped forecasts, which can only be collected
going forward. [`scripts/capture_gb_forecast.py`](../scripts/capture_gb_forecast.py)
and the `forecast-capture` workflow now log the 48-hour-ahead forecast every hour
into `data/validation/gb-fw48h-log.csv`, so a lead-time-stratified version of this
table becomes computable once that log has accumulated. Until then, treat the
published-forecast row as lead-time-agnostic.

## 3. Whether the savings actually happened

Replay the defer-to-a-green-window policy over the real GB record. At each
decision hour the tool sees `forecast(t)` as "now" (section 1). If that's above
200 gCO2eq/kWh it looks forward for the first period the forecast calls green and
defers there. The counterfactual is then settled with **actuals**: what the grid
really was at the original time, against what it really was when the job ran.

Every deferral that made things worse stays in the count.

<!-- generated:deferral -->
| Wait budget | Deferrals | Mean realized delta | Median | Made it worse | p10 / p90 delta |
|---|---|---|---|---|---|
| 3 h | 79 | 20.9 g/kWh | 16.0 g/kWh | 14 (17.7%) | -4.0 / 53.0 |
| 6 h | 115 | 26.9 g/kWh | 22.0 g/kWh | 19 (16.5%) | -5.0 / 68.0 |
| 12 h | 163 | 32.3 g/kWh | 38.0 g/kWh | 24 (14.7%) | -4.0 / 66.0 |
| 24 h | 171 | 32.6 g/kWh | 38.0 g/kWh | 24 (14.0%) | -4.0 / 66.0 |
<!-- /generated:deferral -->

Two things stand out.

**Shifting works, on average.** Every wait budget shows a positive mean and
median realized delta, and the benefit grows with the budget, as it should.

**It isn't reliable per-run.** Between 14% and 18% of deferrals landed on a grid
that was *dirtier* than the one they left, and the 10th-percentile deferral lost
4 to 5 gCO2eq/kWh. This is the direct consequence of section 1: the decision is
made on a forecast, and when the forecast is wrong in the unlucky direction the
shift backfires. Any claim of the form "this build ran clean because we moved it"
is true in expectation and false about one run in six.

Note also that in the sampled window the GB grid was already below 200 gCO2eq/kWh
at 1269 of 1440 decision hours. In a British summer the threshold rarely binds,
so most runs aren't shifted at all and the tool's realized effect is small
regardless of how well it forecasts.

### What the ledger banks versus what the shift avoided

`ledger.py` accumulates a lifetime savings figure. That figure is a **benchmark**:
`(450 - reported_intensity) x energy`, comparing the run against a fixed global
average. `check_grid.py` is already explicit about this in `SAVINGS_BASIS` and
doesn't call it avoided emissions. But the badge says "CO2 saved", and a reader
will take that as the emissions the tool prevented. Here is the gap between the
two readings, over the same replayed deferrals:

<!-- generated:deferral-claim -->
| Wait budget | Realized saving | Ledger would bank | Overstatement |
|---|---|---|---|
| 3 h | 5.4 g | 69.7 g | 13.0x |
| 6 h | 10.1 g | 100.9 g | 10.0x |
| 12 h | 17.1 g | 142.1 g | 8.3x |
| 24 h | 18.1 g | 149.0 g | 8.2x |
<!-- /generated:deferral-claim -->

The banked benchmark is **8 to 13 times larger** than the emissions the shifting
actually avoided. Both numbers are defensible answers to different questions. Only
one of them is the question the word "saved" implies.

Note that the benchmark figure doesn't even require a shift to occur: a build
that runs in a clean zone and is never deferred still banks the full difference
against 450. The realized column, by contrast, is zero when nothing moves.

## 4. Average versus marginal accounting

Shifting a kWh from `t` to `t'` changes emissions by `marginal(t) - marginal(t')`.
The ledger scores it as `average(t) - average(t')`. `marginal.py`'s own docstring
argues, correctly, that marginal is the right signal. The ledger doesn't use it.
So: recompute the same shifts both ways.

The trailing window the regression sees isn't a free parameter, and reporting a
single number for "the marginal saving" would hide that. Both ends of the
plausible range are reported.

<!-- generated:marginal -->
| Zone | Window | Shift events | Mean saving (average) | Mean saving (marginal) | Marginal / average | Marginal saving negative |
|---|---|---|---|---|---|---|
| CISO | 6 h | 371 | 61.4 g/kWh | 117.5 g/kWh | 1.91x | 30.2% |
| CISO | 24 h | 369 | 61.5 g/kWh | -0.3 g/kWh | -0.0x | 49.1% |
| PJM | 6 h | 222 | 18.9 g/kWh | -72.7 g/kWh | -3.84x | 71.6% |
| PJM | 24 h | 219 | 19.0 g/kWh | -0.1 g/kWh | -0.01x | 46.1% |
<!-- /generated:marginal -->

**Under average accounting these shifts save 19 to 61 gCO2eq/kWh. Under the
project's own marginal estimator the saving collapses to roughly zero at a 24-hour
window, and in PJM at a 6-hour window it goes sharply negative.** If the marginal
estimate is right, shifting load in PJM on this rule was actively counterproductive
while the ledger was reporting a saving.

### Why the window changes the answer

<!-- generated:marginal-resolution -->
| Zone | Window | Median average | Median marginal | Hourly step, average | Hourly step, marginal | Median r2 | At clamp floor |
|---|---|---|---|---|---|---|---|
| CISO | 6 h | 189.9 | 174.5 | 6.0 | 19 | 0.51 | 396 (27.8%) |
| CISO | 24 h | 191.4 | 100.0 | 5.9 | 2 | 0.104 | 55 (3.9%) |
| PJM | 6 h | 379.0 | 402 | 4.2 | 37.0 | 0.81 | 4 (0.3%) |
| PJM | 24 h | 379.3 | 413 | 4.2 | 3.0 | 0.845 | 0 (0.0%) |
<!-- /generated:marginal-resolution -->

This table is the reason not to take either marginal column at face value.

At a 24-hour window the estimate is stable but **too smooth to price a few-hour
shift**: the median hour-to-hour movement in the marginal estimate is 2 to 3
gCO2eq/kWh against 4 to 6 in the average intensity it's meant to improve on.
Consecutive 24-hour windows share 23 hours of data, so `marginal(t)` and
`marginal(t')` are nearly the same number by construction and their difference is
near zero by arithmetic alone. The "saving collapses to
zero" result at 24 h is therefore mostly an artifact.

At a 6-hour window the estimate does move, but CISO's estimate lands on the
`MARGINAL_CLAMP` floor of 0 in 27.8% of hours. A negative regression slope is
physically meaningful (load rose while emissions fell, because renewables came
on), and clamping it to zero discards that information and biases the estimate
upward. The clamp is defensible as a guard against nonsense; it isn't
defensible as a silent one.

<!-- generated:marginal-bands -->
| Zone | Window | r2 band | n | Mean saving (average) | Mean saving (marginal) |
|---|---|---|---|---|---|
| CISO | 6 h | r2 < 0.3 | 160 | 70.7 g/kWh | 122.2 g/kWh |
| CISO | 6 h | 0.3-0.7 | 144 | 60.3 g/kWh | 119.5 g/kWh |
| CISO | 6 h | r2 >= 0.7 | 67 | 41.5 g/kWh | 101.8 g/kWh |
| CISO | 24 h | r2 < 0.3 | 367 | 61.7 g/kWh | -0.3 g/kWh |
| CISO | 24 h | 0.3-0.7 | 2 | 35.0 g/kWh | 7 g/kWh |
| CISO | 24 h | r2 >= 0.7 | 0 | - | - |
| PJM | 6 h | r2 < 0.3 | 16 | 20.0 g/kWh | -139.4 g/kWh |
| PJM | 6 h | 0.3-0.7 | 80 | 16.6 g/kWh | 12.2 g/kWh |
| PJM | 6 h | r2 >= 0.7 | 126 | 20.3 g/kWh | -118.1 g/kWh |
| PJM | 24 h | r2 < 0.3 | 0 | - | - |
| PJM | 24 h | 0.3-0.7 | 1 | 34.0 g/kWh | -11 g/kWh |
| PJM | 24 h | r2 >= 0.7 | 218 | 18.9 g/kWh | -0.1 g/kWh |
<!-- /generated:marginal-bands -->

The `r2` bands don't rescue it. CISO never reaches `r2 >= 0.7` at a 24-hour
window at all, and PJM's high-confidence band at 6 hours is where the marginal
saving is *most* negative.

**Conclusion: this project can't currently price its own savings on a
marginal basis.** Average accounting says shifting saves; the project's own
marginal estimator says it saves approximately nothing or costs. We can't tell
whether that's a fact about these grids or a defect in the estimator, because
the estimator has never been checked against real marginal data. That check is
section 7, and it's blocked.

That uncertainty is itself the finding, and it's the reason the savings figure
should keep carrying its `co2_saved_basis` label rather than being promoted to a
claim about avoided emissions.

## 5. Reproducibility

The snapshots in `data/validation/` are committed, so every table above can be
regenerated without network access and a third party can check the arithmetic
against the same bytes. `scripts/fetch_validation_data.py` re-pulls from the two
upstream APIs; the GB feed needs no credential, the EIA feed needs a free key.

## 6. The power assumption

<!-- generated:power -->
| Basis | Watts | Source |
|---|---|---|
| eco-ci power curve for the EPYC 7763, idle | 2.1 | Green Coding `machine-power-data/github_EPYC_7763_4_CPU_shared.sh`, entry [0.00] = 1.7586 W |
| eco-ci power curve for the EPYC 7763, 50% CPU | 6.1 | same file, entry [50.00] = 5.1641 W. Modelled on the processor GitHub actually runs, at the actual 4/128-thread split |
| eco-ci power curve for the EPYC 7763, 100% CPU | 9.7 | same file, entry [100.00] = 8.1796 W |
| Cloud Carbon Footprint, Azure AMD EPYC 3rd Gen, 50% CPU | 11.4 | `AzureFootprintEstimationConstants.ts`: 0.45-2.02 W/vCPU, 128 GB/chip included, 0.392 W/GB on the 12 GB excess, PUE 1.185 |
| Cloud Carbon Footprint, Azure AMD EPYC 3rd Gen, 100% CPU | 15.1 | same constants at full utilization |
| Cloud Carbon Footprint, generic Azure averages, 100% CPU | 22.4 | same file's provider-wide `MIN_WATTS_AVG` 0.74 / `MAX_WATTS_AVG` 3.54, i.e. not matched to the actual processor. The conservative end |
| Prior assumption, to 2026-08-23 | 50.0 | Code comment: "GitHub-hosted runners are 2-4 vCPU machines drawing roughly 30-60W". No citation, and outside every estimate above |

**The prior 50 W figure was about four times too high, for a specific reason: it's approximately the idle draw of a whole server, applied as though it were the draw of a four-thread slice of a 128-thread machine.** To make 50 W correct for 4 vCPUs, the server behind those 128 threads would have to draw about 1,600 W; real ones draw 260 to 850 W. `CI_JOB_POWER_KW` is now **0.013 kW**, with the published range **6 to 25 W** propagated into the `co2_emitted_grams_low` and `co2_emitted_grams_high` outputs. Two things this doesn't claim. First, no direct instrumented measurement of a GitHub-hosted runner exists: the hypervisor blocks RAPL, so even Green Coding's numbers are a model over the SPECpower database rather than a meter reading, and the search for one is recorded as unresolvable rather than quietly dropped. Second, the correction cuts every emissions and savings figure this tool reports by about 4x, including the historical entries already accumulated in the ledger, which were banked on the old basis and aren't restated.
<!-- /generated:power -->

## 7. Marginal estimator versus WattTime: BLOCKED

WattTime publishes real marginal emissions data, free for CAISO_NORTH, which is
the only ground truth available for section 4. Running that backtest requires a
WattTime credential, which this environment doesn't have. The check has not been
run and no result is claimed for it.

This is the highest-value outstanding item in this document, because it's the
only thing that can distinguish "shifting doesn't help much on a
marginal basis" from "our marginal estimator isn't good enough to tell". Until
it's run, section 4's conclusion stands as an unresolved disagreement rather
than a finding about the grid.

`providers/watttime.py` already implements the client. The backtest needs a
credential and a period of overlapping history, and belongs in
`scripts/run_validation.py` as a fourth analysis.

## 8. Threats to validity

- **One grid, one season.** GB, late May to late August. The diurnal result in
  particular is likely to differ in winter, when GB's heating load and wind
  profile are different. Nothing here establishes seasonal stability.
- **The replay is a simulation.** It applies the
  tool's decision rule to real grid data. It doesn't re-run the tool, so it can't
  capture bugs in the wiring between the decision rule and the dispatch.
- **The published-forecast lead time is unknown.** See section 2.
- **The marginal estimator is unvalidated.** See section 7. Every marginal number
  in section 4 inherits that.
- **The EIA `OTH` bucket.** CISO reports large negative values under `OTH`
  (net imports). `providers/eia._fuel_mix_totals` drops non-positive rows, so
  those hours are priced on domestic generation only, which understates the
  carbon content of an import-heavy hour. Flow tracing exists to fix this and is
  not applied here.
- **No uncertainty propagation.** The tables report point estimates. The grams
  columns additionally inherit whatever error the power assumption carries
  (section 6).
