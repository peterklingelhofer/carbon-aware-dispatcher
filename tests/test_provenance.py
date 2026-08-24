"""The apparatus has to survive someone adding a provider in a hurry.

These are contract tests: they assert that provenance cannot be
detached from a reported number, that every citekey in the code resolves, and
that every factor in the shared corpus states its basis. Each one is here
because the corresponding mistake is easy to make and invisible once made.
"""

import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import check_grid  # noqa: E402
from citations_generated import CITATION_IDS  # noqa: E402
from providers import factor_corpus, provenance  # noqa: E402


class TestEveryIntensityCarriesProvenance:
    def test_only_one_place_writes_carbon_intensity(self):
        """A number must not be able to reach a consumer without its method.

        set_intensity_outputs is the single writer. If a new code path calls
        set_output("carbon_intensity", ...) directly it bypasses the method,
        citekeys and evidence tier, and nothing else would catch that.
        """
        with open(os.path.join(ROOT, "check_grid.py")) as fh:
            source = fh.read()
        writes = re.findall(r'set_output\(\s*"carbon_intensity"', source)
        assert len(writes) == 1, (
            f"{len(writes)} places write carbon_intensity directly; "
            "route them through set_intensity_outputs so provenance travels with the number"
        )

    def test_every_provider_resolves_to_a_method(self):
        from check_grid import _PROVIDER_MODULES

        for name in _PROVIDER_MODULES:
            assert name in provenance.PROVIDER_METHODS, (
                f"provider {name} has no entry in PROVIDER_METHODS, so its readings "
                "would silently fall back to a method it may not use"
            )

    def test_every_method_has_a_description_and_tier(self):
        for method in provenance.METHOD_CITATIONS:
            assert provenance.METHOD_DESCRIPTIONS.get(method)
            assert provenance.METHOD_TIERS.get(method) in set("ABCDE")

    def test_only_the_weather_heuristic_has_no_citations(self):
        """An empty citation list is allowed exactly once, and it is tier E.

        The Open-Meteo estimate has no source. Everything else that
        produces an intensity must name what justifies it.
        """
        uncited = [m for m, c in provenance.METHOD_CITATIONS.items() if not c]
        assert uncited == [provenance.WEATHER_HEURISTIC]
        assert provenance.METHOD_TIERS[provenance.WEATHER_HEURISTIC] == "E"

    @pytest.mark.parametrize("provider", sorted(provenance.PROVIDER_METHODS))
    def test_record_is_complete_for_every_provider(self, provider):
        record = provenance.provenance(provider)
        assert record["source"] == provider
        assert record["method_description"]
        assert record["evidence_tier"] in set("ABCDE")
        assert record["factors"].startswith("emission-factors-v")
        if provider != "open_meteo":
            assert record["citations"], f"{provider} reports an intensity with no citekey"

    def test_flow_tracing_changes_the_declared_method(self):
        """A traced reading is consumption-based; saying otherwise misreports the basis."""
        production = provenance.provenance("entsoe")
        traced = provenance.provenance("entsoe", consumption_traced=True)
        assert production["method"] != traced["method"]
        assert traced["method"] == provenance.CONSUMPTION_TRACED

    def test_confidence_is_carried_when_present_and_absent_otherwise(self):
        assert "confidence_r_squared" not in provenance.provenance("eia")
        assert provenance.provenance("eia", confidence=0.42)["confidence_r_squared"] == 0.42


class TestCitekeysResolve:
    def test_every_method_citekey_is_in_the_corpus(self):
        for method, keys in provenance.METHOD_CITATIONS.items():
            for key in keys:
                assert key in CITATION_IDS, f"{method} cites unknown citekey {key}"

    def test_generated_ids_match_the_json(self):
        with open(os.path.join(ROOT, "docs", "CITATIONS.csl.json")) as fh:
            entries = json.load(fh)
        assert sorted(entry["id"] for entry in entries) == sorted(CITATION_IDS), (
            "citations_generated.py is stale; run scripts/generate_citations.py"
        )

    def test_no_duplicate_citekeys(self):
        assert len(CITATION_IDS) == len(set(CITATION_IDS))


class TestFactorCorpusStatesItsBasis:
    def test_every_factor_has_a_citation_or_an_admitted_assumption(self):
        for key, record in factor_corpus._RECORDS.items():
            has_basis = record.get("citation") or record.get("assumption")
            assert has_basis, f"factor {key} states no basis at all"

    def test_assumed_factors_are_tier_e(self):
        for key, record in factor_corpus._RECORDS.items():
            if record.get("citation") is None:
                assert record["evidence_tier"] == "E", (
                    f"factor {key} has no citation but claims tier {record['evidence_tier']}"
                )

    def test_cited_factors_resolve(self):
        for key, record in factor_corpus._RECORDS.items():
            citation = record.get("citation")
            if citation is not None:
                assert citation in CITATION_IDS, f"factor {key} cites unknown {citation}"

    def test_aliased_factors_hold_the_same_value(self):
        """Two names for one fuel priced differently would be a silent split table."""
        for key, record in factor_corpus._RECORDS.items():
            target = record.get("alias_of")
            if target:
                assert record["value"] == factor_corpus._RECORDS[target]["value"], (
                    f"{key} is declared an alias of {target} but their values differ"
                )

    def test_storage_carries_no_factor(self):
        """Discharge is not generation and is not zero-carbon; it must not be priced."""
        for key, record in factor_corpus._RECORDS.items():
            if record.get("storage"):
                assert record["value"] is None
                assert key not in factor_corpus.FUEL_FACTORS


class TestSummaryLine:
    def test_names_the_method_the_tier_and_the_factor_table(self):
        line = provenance.summary_line(provenance.provenance("eia"))
        assert "tier A" in line
        assert "emission-factors-v" in line
        assert "ipcc-ar5-wg3-annex3" in line

    def test_says_so_out_loud_when_there_is_no_source(self):
        line = provenance.summary_line(provenance.provenance("open_meteo"))
        assert "no source" in line


class TestDecisionConfidence:
    def test_reports_nothing_to_judge_without_a_reading(self):
        assert "nothing to judge" in check_grid._decision_confidence(None)

    def test_flags_an_estimate_as_an_estimate(self):
        record = provenance.provenance("open_meteo")
        assert "modeled estimate" in check_grid._decision_confidence(record)

    def test_flags_the_heuristic_curve_against_its_measured_accuracy(self):
        record = provenance.provenance("eia")
        text = check_grid._decision_confidence(record, forecast_heuristic=True)
        assert "worse than persistence" in text
