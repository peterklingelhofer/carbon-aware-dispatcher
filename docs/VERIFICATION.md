# Verification record

Seven of this project's claims chased to primary sources. Five produced fixes,
one is unresolvable and is surfaced as an assumption, and one is blocked on a
credential.

Verdict vocabulary:

- **RESOLVED**: the primary text was obtained and the specific passage read. The
  claim stands as stated.
- **FIXED**: primary text obtained, the claim did not hold, and the code changed.
- **PARTIALLY RESOLVED**: some sub-claims proven, others not.
- **UNRESOLVABLE**: searched properly and never found. The number is surfaced as an
  assumption rather than dressed in a plausible citation.
- **BLOCKED**: the check is well-defined and could not be run here. The gap is
  named.

| # | Item | Verdict |
|---|---|---|
| 1 | `FUEL_FACTORS` are "IPCC AR5 lifecycle medians" | **FIXED.** True for 7 of 14 values. The other seven trace to four different non-IPCC sources, three of which were found and one of which remains unresolvable |
| 2 | The 17-provider and 200+-zone badges | **FIXED.** Both overstate; nine further count discrepancies found |
| 3 | `CI_JOB_POWER_KW = 0.05`, from "2-4 vCPU machines drawing roughly 30-60W" | **FIXED.** About 4x too high, from a unit-of-allocation error. Corrected, with an error bar |
| 4 | `GLOBAL_AVG_INTENSITY = 450` | **FIXED.** Wrong value, wrong units and wrong system boundary. Replaced with a boundary-matched figure |
| 5 | The EPA equivalence factors | **FIXED.** All three had drifted; the tree figure was off by a factor of 2.9 |
| 6 | Any direct measurement of GitHub-hosted runner energy | **UNRESOLVABLE.** None exists publicly; the hypervisor blocks RAPL |
| 7 | Whether the marginal estimator is accurate | **BLOCKED.** Needs a WattTime credential. See `VALIDATION.md` section 7 |

---

## 1. The claim that the emission factors are IPCC AR5 lifecycle medians

**Verdict: FIXED. Seven of fourteen values trace exactly to the cited
table. The other seven don't appear in it at all, or correspond to a different
row than their label implies, and the code comment claimed all of them did. Three
of the strays were chased to their real primary sources, which turn out to be a
2006 UK parliamentary briefing, a 2006 consultancy report for Friends of the
Earth, and the UK grid operator's own direct-basis factor table.**

### 1.1 Evidence trail

`providers/base.py` says, above the whole table:

> Canonical lifecycle emission factors in gCO2eq/kWh by generic fuel name.
> IPCC AR5 (2014) lifecycle medians.

Primary text obtained and read: IPCC WG3 AR5 Annex III, *Technology-specific Cost
and Performance Parameters*, downloaded from
`https://www.ipcc.ch/site/assets/uploads/2018/02/ipcc_wg3_ar5_annex-iii.pdf`
on 2026-08-23 (SHA-256 `dec39383e03caf843f833ad8f4b373f72be3b86ada6d826bd172e8955ffe24c2`,
22 pages), text extracted with `pdftotext -layout`. Table A.III.2 appears on report
page 1335.

Note that `ipcc.ch` returns HTTP 403 to some automated clients. It serves the PDF
to a browser user-agent. Anyone re-checking this should expect that rather than
assume the URL is dead.

Table caption, verbatim:

> Table A.III.2 | Emissions of selected electricity supply technologies (gCO2eq / kWh)

Its lifecycle `Min / Median / Max` column, transcribed in full for the
commercially-available rows:

| Row | Lifecycle min / **median** / max |
|---|---|
| Coal (PC) | 740 / **820** / 910 |
| Gas (Combined Cycle) | 410 / **490** / 650 |
| Biomass (cofiring) | 620 / **740** / 890 |
| Biomass (dedicated) | 130 / **230** / 420 |
| Geothermal | 6.0 / **38** / 79 |
| Hydropower | 1.0 / **24** / 2200 |
| Nuclear | 3.7 / **12** / 110 |
| Concentrated Solar Power | 8.8 / **27** / 63 |
| Solar PV (rooftop) | 26 / **41** / 60 |
| Solar PV (utility) | 18 / **48** / 180 |
| Wind onshore | 7.0 / **11** / 56 |
| Wind offshore | 8.0 / **12** / 35 |
| Ocean | 5.6 / **17** / 28 |

Plus four pre-commercial CCS rows, which this project doesn't use.

**That's the entire table.** There's no oil-fired row, no lignite or brown-coal
row, and no municipal-waste row anywhere in it.

### 1.2 What matches

Seven values are exact, unambiguous matches to a single named row:

| Code | Value | AR5 row |
|---|---|---|
| `coal` | 820 | Coal (PC) |
| `gas` | 490 | Gas (Combined Cycle) |
| `nuclear` | 12 | Nuclear |
| `hydro` | 24 | Hydropower |
| `geothermal` | 38 | Geothermal |
| `biomass` | 230 | Biomass (dedicated) |
| `marine` | 17 | Ocean |

These are correctly cited and need no change beyond pinning the table and row.

### 1.3 What doesn't match, and where it actually came from

**`solar = 45` isn't an AR5 median, and it's the thread that unravels the rest.**
AR5 splits PV into rooftop (41) and utility (48). 45 is neither. It's the rounded
mean of the two, and it's published in exactly that form by **Electricity Maps**,
in their `config/defaults.yaml`, under the label `source: IPCC 2014`.

A table read out of AR5 would contain 41, 48, or 27. It wouldn't contain 45.
Combined with `oil: 650`, which Electricity Maps also carries and AR5 doesn't,
this establishes that **this repo's factor table was copied from Electricity Maps
rather than read out of IPCC AR5**, and inherited a mislabel in the process. Two
values rule out a pure copy (`wind` is 12 here and 11 there, `thermal_mix` is 750
here and `unknown` is 700 there), so it's a copy with edits, or a copy of a fork.

**`oil = 650` is UK POSTnote 268, October 2006.** Verbatim: "The average carbon
footprint of oil-fired electricity generation plants in the UK is ~650gCO2eq/kWh."
A UK fleet average, from 2006, for a fuel supplying 1% of UK generation, reaching
this repo via Electricity Maps and presented as a global IPCC lifecycle median.
AR5 does contain an oil figure, but only in Chapter 7 and only as a range
inherited from SRREN, 510 to 1170, with no median. 650 isn't in AR5.

**`waste = 580` is Eunomia's 2006 report for Friends of the Earth,** Table 1,
"Incinerator, electricity only". It's wrong for this use on four counts at once:
it's the **Future** modelled column rather than the current measured one (510),
it's **UK-specific**, it's **direct** rather than lifecycle, and it **excludes
biogenic carbon**. The same row including biogenic carbon reads **1405**. So a
factor presented as a lifecycle median may understate waste-to-energy by more
than half.

Note the near-miss that makes this worth documenting: AR5 Annex III section
A.III.4.2.5 does discuss municipal solid waste, and its Table A.III.11 contains
the number **0.58**. That's tonnes of CO2eq per tonne of MSW landfilled. It's
not 580 gCO2eq/kWh, and the resemblance is a coincidence a careless audit would
have accepted.

**`other = 300` is National Grid ESO's "Other" row,** from the published
methodology behind the UK Carbon Intensity API this project consumes. The origin
is therefore known and citable. What has no support is the **use**: ESO's table
is direct gCO2/kWh, scoring hydro, nuclear, solar and wind at exactly zero, and
this project's table is lifecycle CO2eq. Importing one row across that boundary
is a category error rather than a missing citation. It also matters more than its
size suggests, because `other` is the catch-all applied to imports and to any
fuel a feed doesn't classify: a zone whose unclassified bucket is really coal is
understated by over 500 gCO2eq/kWh. Note too that ESO's own oil factor is **935**,
so this repo did not take oil from the API it actually reads.

**`lignite = 1050` remains unresolvable.** It isn't in AR5, and it doesn't merely
sit off-row: it exceeds the *maximum* of the AR5 coal-PC range (910). Electricity
Maps has no lignite mode at all. No source found publishes 1050 for lignite. The
two nearest candidates are the German Federal Environment Agency's 1054 for
Braunkohle (UBA CLIMATE CHANGE 35/2021, Tabelle 98) and the exact midpoint of
Turconi et al. (2013)'s 800 to 1300 lignite range, which is 1050 precisely. But
1050 is *also* Turconi's upper bound for hard coal, so a derivation and a
transcription slip fit the evidence equally well. Recorded as an assumption.

**`wind = 12` is the AR5 offshore row.** Onshore is 11 and dominates both global
capacity and what these feeds report. This was the source of the disagreement with
the companion project `carbon-lens`, which uses 11.

**Resolution.** `wind` is 11 and `solar` is 48, and neither value
is defined in this repository any more: both load from the shared corpus vendored
at `data/emission-factors.json`, which `carbon-lens` owns and both projects
consume. The supporting evidence for taking the onshore row is EIA's 2023 US net
generation, which is 421,007 GWh onshore against 134 GWh offshore. `solar` moved
from 45 (the Electricity Maps composite, which is no AR5 row) to the AR5 utility
median of 48, because balancing-authority feeds report utility-scale generation
and not behind-the-meter rooftop. Nine pinned test expectations moved with them;
reported intensities shift by at most about 1 gCO2eq/kWh on the wind fraction and
about 3 on the solar fraction.

**`thermal_mix = 750` remains unresolvable.** Not Electricity Maps' `unknown`
(700), not ESO's `Other` (300), not any IPCC row. It's a reasoned guess between
the coal and gas factors, weighted towards coal for India, and the weighting was
never derived from published Indian generation shares.

### 1.4 Disposition

The factor table no longer lives in this repository's source at all. It's a
**shared, versioned corpus** at `data/emission-factors.json`, owned by the
companion project `carbon-lens` and vendored here as JSON rather than taken as a
dependency, because this action has to run inside a GitHub workflow with no
install step. Both projects now read the same bytes, which is the whole point:
they previously published different numbers for the same physical quantity while
both citing IPCC AR5.

`providers/factor_corpus.py` loads it strictly. Each record must either resolve
its `citation` against `docs/CITATIONS.csl.json` or carry an `assumption` string
and evidence tier E. Anything else raises at import rather than degrading
quietly, because a wrong factor table makes every intensity this tool reports
wrong. Storage records (battery, pumped storage) carry a null value by design and
are excluded from the mix rather than priced at zero.
`tests/test_provenance.py` re-asserts every one of those invariants independently
of the loader, so the contract survives the loader being rewritten.

Two of this project's values moved in the process, both towards the published AR5
median the label always claimed: `solar` 45 to 48 and `wind` 12 to 11, as
described above. The convergence corrected two larger errors on the `carbon-lens`
side at the same time (coal 900 to 820, gas 430 to 490).

Beyond the numbers: seven undeclared deviations are now declared, `lignite` and
`thermal_mix` are labelled as assumptions rather than citations, storage is
explicitly unpriced, and the claim that the table is "IPCC AR5 lifecycle medians"
has been removed from the code because it was false.

---

## 2. The provider and zone badge counts

**Verdict: FIXED. The provider badge overstated by one, the zone badge
was the vendor's paid-catalog number rather than anything the code resolves, and
nine further count claims in the README disagreed with the code or with each
other.**

### 2.1 Method

Counts taken by importing the actual registries and measuring them. The README
prose was ignored. Every figure below was reproduced directly against
`check_grid._PROVIDER_MODULES`, `providers/__init__.py` and the per-provider zone
maps.

### 2.2 Providers: 16

`check_grid._PROVIDER_MODULES` has exactly 16 entries, and `providers/__init__.py`
defines exactly 16 `PROVIDER_*` constants, and `setup_wizard.py` lists exactly 16.
The badge said 17 because it tracked the row count of the README table, which
includes GridStatus.

GridStatus is a real external API but is **forecast-only**: it has no
`check_carbon_intensity`, is absent from `_PROVIDER_MODULES`, and can never answer
"what is the carbon intensity of zone X". WattTime is also a real external API and
was counted **nowhere**, despite being closer to a provider than GridStatus is: it
returns a marginal percentile rather than gCO2eq/kWh.

So the repo's own definition of "provider" was being applied inconsistently in
both directions. Fixed by defining it precisely: a provider is a module that
resolves a zone to a carbon intensity. That gives 16. Badge changed to
`grid data providers: 16`.

### 2.3 Zones: 196 keyless

The `200+` figure is Electricity Maps' published catalog size, quoted in this
repo's own table two ways on the same page: as the badge's headline and as
"1 zone (free tier) / 200+ (paid)". The free tier is one registered zone, so the
tool as a free user experiences it can't reach the 200.

Measured against the registries, without any credential:

| Count | Basis |
|---|---|
| 231 | Raw accepted identifiers across all keyless registries |
| 213 | After collapsing the 36 UK alias keys to 18 distinct regions |
| 208 | After collapsing `DK1`/`DK-DK1`, `IE`/`IE-ROI`, `IESO`/`CA-ON`, `AESO`/`CA-AB` |
| **196** | After also removing the 12 EIA regional aggregates, which are roll-ups of balancing authorities already counted |

Of those 196, **126 reach a real grid-operator feed** and **70 resolve only to
Open-Meteo**, which the module's own docstring calls "a rough estimate: it doesn't
know the actual grid mix". A badge reading "200+ zones" invites a reader to
assume 200 measured grids. Badge changed to `keyless zones: 196`, with the
126/70 split stated in the README next to the provider table.

### 2.4 Seven further discrepancies, all corrected

| Claim | Was | Is |
|---|---|---|
| `auto:green` preset | "10 curated green zones across 5 continents" | 11 zones, 4 continents. Asia was dropped when Grid India was excluded for being geo-restricted, and the prose was not updated |
| `auto:green:full` preset | "21 zones" | 19 |
| `auto:cleanest` preset | "Checks all free-provider zones" | 16 curated zones. The code comment said "a smart subset"; the README said "all" |
| ENTSO-E coverage | "36 countries", in four places | 44 bidding zones across 31 countries. The same README stated 44 and 36 for the same thing without distinguishing zones from countries |
| ENTSO-E token | "Optional free tokens add coverage" | Adds accuracy. All 44 ENTSO-E zones already resolve keyless via Energy-Charts, RTE, Energinet or EirGrid, so coverage is unchanged. Set difference against the keyless union is empty, and `providers/__init__.py` says as much in a comment |
| Open-Meteo coverage | "90+" in two places | Exactly 100 |
| Source count in the opening paragraph | "dozens of sources" | 16, contradicting this file's own badge ten lines above it |

The remaining counts held: UK 18 regions, AEMO 5, Grid India 5, ONS Brazil 5,
GridStatus 7 ISOs, the Electricity Maps free tier of 1, and "no API keys
required" all checked out exactly as written.

---

## 3. The CI job power assumption

**Verdict: FIXED. `CI_JOB_POWER_KW = 0.05` was about four times too
high, and the error has a specific, nameable cause.**

The code said:

> Average CI job power draw in kW. GitHub-hosted runners are 2-4 vCPU machines
> drawing roughly 30-60W. We use 0.05 kW as a conservative estimate.

Three things are wrong with that.

**The hardware claim is stale.** GitHub's docs source of record
(`github/docs`, `data/reusables/actions/supported-github-runners.md`, read
2026-08-23) gives standard Linux runners as **4 vCPU / 16 GB on public
repositories** and **2 vCPU / 8 GB on private ones**. Public runners moved from
2-core to 4-core in early 2024. "2-4 vCPU" has not described the public case for
over two years.

**The number has no source.** No citation was given, and none was found for
"30-60 W" as the draw of a runner.

**The unit of allocation is wrong, which is the actual defect.** GitHub runs
these on AMD EPYC 7763 hosts, which expose 128 hardware threads; a 4-vCPU runner
is **4/128 of one server**. 30 to 60 W is approximately the idle draw of a whole
server socket. Applying it to a four-thread slice implies a server drawing about
1,600 W behind those 128 threads. Real ones draw 260 to 850 W.

Every estimate that was actually computed, from constants fetched directly from
their sources, is in `docs/VALIDATION.md` section 6. They span 6 to 22 W for a
4-vCPU runner. **50 W is above all of them.**

`CI_JOB_POWER_KW` is now **0.013**, with the range **0.006 to 0.025 kW**
propagated into new `co2_emitted_grams_low` and `co2_emitted_grams_high` outputs
and into the job summary. A user who supplies `job_energy_kwh` or
`job_power_watts` has measured the thing the range bounds, so their bounds
collapse to a point.

Two consequences worth stating rather than burying. First, **this cuts every
emissions and savings figure the tool reports by roughly 4x.** Second, the
lifetime totals already accumulated in anyone's ledger were banked on the old
basis and are **not** restated, so a long-running ledger now mixes two bases.

### 3.1 The unresolvable part

**Verdict: UNRESOLVABLE. There's no public direct measurement of a
GitHub-hosted runner's energy use.**

The obvious way to settle this is to measure it: run a known workload on a
hosted runner and read RAPL. That doesn't work. Green Coding Solutions, who
publish the closest thing to a measured figure, report that on GitHub Actions
runners the hypervisor blocks the relevant counters entirely. Their numbers,
which are the low end of the range above, are an XGBoost model fitted to the
SPECpower database, and they say so.

So the corrected value is a well-sourced *model*, and it's
recorded as such rather than being promoted to something firmer. If anyone finds
or performs a direct measurement, this entry is the one to overturn.

---

## 4. The global average baseline

**Verdict: FIXED on three separate axes: value, units, and system
boundary.**

The code said:

> Global average grid carbon intensity (~450 gCO2eq/kWh).

No source was given. It's commonly assumed to be an IEA figure. It isn't.

**The value.** The IEA's *Electricity 2026* reports **435 g CO2/kWh for 2025**
("declining by 14% to 435 g CO2/kWh in 2025"), and 445 for 2024 in the prior
edition. A search of the report for "450 g" returns nothing.

**The units and boundary, which matter more.** The IEA figure is **direct** CO2
at the point of generation. IEA's own documentation states that under it "the
intensities corresponding to renewable sources (including biofuels) and nuclear
are equal to zero". That's definitionally not a lifecycle CO2-equivalent figure.
This project's fuel factors are lifecycle CO2eq, in which nuclear is 12 and wind
is 11 or 12, not zero. Benchmarking one against the other compares two different
quantities, and it does so in the flattering direction: a lifecycle baseline is
*higher* than a direct one, so using the direct number understates the baseline
and therefore understates the savings the tool would claim.

`GLOBAL_AVG_INTENSITY` is now **458**, from Ember's *Global Electricity Review
2026*: "the emissions intensity of electricity has dropped 14% over the last
decade, from 533 grams of CO2 equivalent per kWh (gCO2e/kWh) in 2015 to 458
gCO2e/kWh in 2025". Ember reports CO2-equivalent and incorporates IPCC lifecycle
intensities, so it shares a boundary with the factor table. It's free and
citable, which the IEA's emission-factors database isn't.

The 435-versus-458 gap for the same year, from two competent organisations, is
itself the clearest available demonstration that the boundary question isn't
pedantry.

---

## 5. The EPA equivalence factors

**Verdict: FIXED. All three had drifted from the EPA page they cite,
and the tree figure was off by a factor of 2.9.**

Source read directly on 2026-08-23: the EPA Greenhouse Gases Equivalencies
Calculator "Calculations and References" page, which states its own last update
as 2026-08-04 and uses eGRID2022-vintage data.

| Constant | Was | EPA says | Now |
|---|---|---|---|
| `CO2_GRAMS_PER_KM_DRIVEN` | 250, from "~400 gCO2/mile" | 4.29 tCO2e per vehicle-year over 10,917 miles = 393 gCO2e/mile | 244 |
| `CO2_GRAMS_PER_PHONE_CHARGE` | 8.22 | "1.24 x 10-5 metric tons CO2/smartphone charged" | 12.4 |
| `CO2_GRAMS_PER_TREE_YEAR` | 21000 | "0.060 metric ton CO2 per urban tree planted per year" | 60000 |

The driving figure was close and is now exact to EPA's arithmetic. Note that EPA
publishes no per-kilometre figure at all, so that conversion is ours and is
labelled as such.

The phone figure was 34% low, simply from an older EPA vintage. EPA's current
calculation uses **delivered** electricity, including transmission and
distribution losses, which is worth knowing if it's ever compared to a
generation-basis number.

**The tree figure is the interesting one.** 21,000 g was not EPA's number at all.
The old code comment read "~21 kg = 21000 gCO2 (EPA, ~0.06 g/min)", and 0.06 is
EPA's actual figure, in **metric tons per tree per year**. Someone appears to
have carried the digits 0.06 across into an unrelated unit and landed on a
constant that contradicts the source named beside it.

The correction makes the tool's output *less* impressive, by a factor of 2.9,
which is the direction that suggests the original was not a neutral slip.

And the scenario matters as much as the number. EPA's metric is titled "Number
of urban tree seedlings grown for 10 years", and its own text is explicit:

> The medium growth coniferous and deciduous trees are raised in a nursery for
> one year... after 5 years... the probability of survival is 68 percent; after
> 10 years, the probability declines to 59 percent.

> This method is best used as an estimation for suburban/urban areas... and is
> not appropriate for reforestation projects.

It's a survival-weighted average over the first ten years of a newly planted
seedling, and readers routinely take it for a mature tree's annual
sequestration rate. That caveat now lives in the code beside the constant.

---

## 6. What this audit did not settle

Recorded so the gaps are visible rather than absent:

- **`FOSSIL_AVG_INTENSITY = 550`** and its "typical US fossil mix (~60% gas, ~30%
  coal, ~10% oil)". Still unsourced, still undated, and the real mix moves every
  year. Not checked against EIA generation data in this pass. Listed in the
  `CITATIONS.md` gaps ledger.
- **The time-of-day heuristic curves** for Grid India, ONS Brazil, Eskom and
  Quebec. The code already calls them "rough generalizations", which is honest,
  but no source is attached and none was sought here. `VALIDATION.md` section 2
  measured what a diurnal curve is worth in GB and found it worse than
  persistence under six hours, which is reason to doubt these too.
- **Whether the marginal estimator is accurate.** Blocked on a WattTime
  credential. See `VALIDATION.md` section 7. This is the largest single hole.
- **A `MARGINAL_CLAMP` defect found while validating.** Clamping a negative
  regression slope to zero discards a physically meaningful signal (load rose
  while emissions fell, because renewables came on) and biases the estimate
  upward. On CISO at a six-hour window this fires in 27.8% of hours. Documented
  in `VALIDATION.md` section 4 and left unfixed, because the right fix depends on
  the WattTime backtest that's blocked.
