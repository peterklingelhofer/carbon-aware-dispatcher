"""Where a carbon intensity number came from, and who says the method is right.

Every reading this project emits is the product of two separable choices: which
feed supplied the data, and which accounting method turned that data into a
gCO2eq/kWh figure. `data_source_for` in check_grid answers the first. This
answers the second, and attaches the citekeys that justify it.

The citekeys are annotated `CitationId`, a Literal generated from
docs/CITATIONS.csl.json, so a key that is not in the corpus is a mypy error
rather than a string that quietly means nothing. The factor table is checked the
other way round, at load time in providers/factor_corpus.py, because its keys
come from JSON that mypy never sees.
"""

from typing import Any, Optional

from citations_generated import CitationId
from providers.factor_corpus import CORPUS_VERSION

# Methods, keyed by the identifier that travels in the action's output.
PRODUCTION_AVERAGE = "production-average"
OPERATOR_PUBLISHED = "operator-published"
CONSUMPTION_VENDOR = "consumption-vendor"
CONSUMPTION_TRACED = "consumption-flow-traced"
WEATHER_HEURISTIC = "weather-heuristic"
DIURNAL_CLIMATOLOGY = "diurnal-climatology"
MARGINAL_REGRESSION = "marginal-regression"

METHOD_DESCRIPTIONS = {
    PRODUCTION_AVERAGE: "production-based generation-weighted average over the reported fuel mix",
    OPERATOR_PUBLISHED: "carbon intensity as published by the grid operator, using their factors",
    CONSUMPTION_VENDOR: "consumption-based intensity as published by a commercial provider",
    CONSUMPTION_TRACED: "consumption-based intensity via flow tracing over the traced EU network",
    WEATHER_HEURISTIC: "renewable potential inferred from weather; not a carbon measurement",
    DIURNAL_CLIMATOLOGY: "hour-of-day average over accumulated history",
    MARGINAL_REGRESSION: "marginal rate regressed from interval-to-interval fuel-mix changes",
}

# What justifies each method. An unknown citekey here fails mypy.
METHOD_CITATIONS: "dict[str, tuple[CitationId, ...]]" = {
    PRODUCTION_AVERAGE: ("ipcc-ar5-wg3-annex3", "electricity-maps-default-factors"),
    OPERATOR_PUBLISHED: ("neso-carbon-intensity-methodology",),
    CONSUMPTION_VENDOR: ("tranberg-2019-flow-tracing",),
    CONSUMPTION_TRACED: (
        "tranberg-2019-flow-tracing",
        "bialek-1996-tracing-electricity",
        "kirschen-1997-contributions",
    ),
    # Deliberately empty: the weather model is a coverage heuristic with no
    # source, and the evidence tier below is what says so out loud
    WEATHER_HEURISTIC: (),
    DIURNAL_CLIMATOLOGY: ("miller-2022-hourly-accounting", "murphy-1992-skill-standards"),
    MARGINAL_REGRESSION: (
        "siler-evans-2012-marginal-factors",
        "hawkes-2010-marginal-emissions",
        "gagnon-2022-short-run-omits",
    ),
}

# Evidence tier for the METHOD, on the A-E scheme in docs/CITATIONS.md. This is
# not the tier of the underlying factors, which the factor corpus carries
# per fuel; it is how much weight the accounting approach itself can bear.
METHOD_TIERS = {
    PRODUCTION_AVERAGE: "A",
    OPERATOR_PUBLISHED: "A",
    CONSUMPTION_VENDOR: "C",
    CONSUMPTION_TRACED: "B",
    WEATHER_HEURISTIC: "E",
    DIURNAL_CLIMATOLOGY: "B",
    MARGINAL_REGRESSION: "C",
}

# Which method each provider module's reading is produced by.
PROVIDER_METHODS = {
    "aemo": PRODUCTION_AVERAGE,
    "cammesa": PRODUCTION_AVERAGE,
    "canada": PRODUCTION_AVERAGE,
    "eia": PRODUCTION_AVERAGE,
    "entsoe": PRODUCTION_AVERAGE,
    "eskom": PRODUCTION_AVERAGE,
    "grid_india": PRODUCTION_AVERAGE,
    "ons_brazil": PRODUCTION_AVERAGE,
    "taiwan": PRODUCTION_AVERAGE,
    "uk_carbon_intensity": OPERATOR_PUBLISHED,
    "eirgrid": OPERATOR_PUBLISHED,
    "energinet": OPERATOR_PUBLISHED,
    "rte": OPERATOR_PUBLISHED,
    "energy_charts": OPERATOR_PUBLISHED,
    "electricity_maps": CONSUMPTION_VENDOR,
    "open_meteo": WEATHER_HEURISTIC,
}


def method_for(provider: str, consumption_traced: bool = False) -> str:
    """The accounting method behind a provider's reading.

    consumption_traced overrides the provider's own method: flow tracing
    replaces a production-based number with a consumption-based one, so the
    provenance has to say so rather than keep reporting the original basis.
    """
    if consumption_traced:
        return CONSUMPTION_TRACED
    return PROVIDER_METHODS.get(provider, PRODUCTION_AVERAGE)


def provenance(
    provider: str,
    consumption_traced: bool = False,
    confidence: Optional[float] = None,
) -> dict:
    """The full provenance record for one reading.

    confidence is the r_squared from the marginal estimator where one applies,
    and None otherwise. Every method here resolves to a non-empty description
    and a tier; only the weather heuristic resolves to an empty citation list,
    and it is tier E precisely so that emptiness is visible rather than silent.
    """
    method = method_for(provider, consumption_traced=consumption_traced)
    record: dict[str, Any] = {
        "source": provider,
        "method": method,
        "method_description": METHOD_DESCRIPTIONS[method],
        "factors": f"emission-factors-v{CORPUS_VERSION}",
        "citations": list(METHOD_CITATIONS[method]),
        "evidence_tier": METHOD_TIERS[method],
    }
    if confidence is not None:
        record["confidence_r_squared"] = confidence
    return record


def summary_line(record: dict) -> str:
    """One line for the job summary: how this number was produced."""
    cites = ", ".join(record["citations"]) or "no source (assumption)"
    return (
        f"{record['method_description']} "
        f"(tier {record['evidence_tier']}; factors {record['factors']}; {cites})"
    )
