"""Tests for the RTE (France) eco2mix provider."""

from unittest import mock

import pytest

import providers
from providers import rte


class TestCheckCarbonIntensity:
    @mock.patch("providers.rte.request")
    def test_green(self, req):
        req.return_value = {"records": [{"fields": {"taux_co2": 37}}]}
        assert rte.check_carbon_intensity("FR", 200) == (True, 37)

    @mock.patch("providers.rte.request")
    def test_skips_null_taux(self, req):
        req.return_value = {
            "records": [
                {"fields": {"taux_co2": None}},
                {"fields": {"taux_co2": 52}},
            ]
        }
        assert rte.check_carbon_intensity("FR", 200) == (True, 52)

    @mock.patch("providers.rte.request")
    def test_over_threshold(self, req):
        req.return_value = {"records": [{"fields": {"taux_co2": 240}}]}
        assert rte.check_carbon_intensity("FR", 200) == (False, 240)

    @pytest.mark.parametrize(
        "response",
        [None, {"records": [{"fields": {"taux_co2": None}}]}],
        ids=["no_data", "all_null"],
    )
    @mock.patch("providers.rte.request")
    def test_returns_none_none_without_usable_data(self, req, response):
        req.return_value = response
        assert rte.check_carbon_intensity("FR", 200) == (None, None)


class TestRouting:
    def test_detect_provider_routes_france(self):
        assert providers.detect_provider("FR") == providers.PROVIDER_RTE
