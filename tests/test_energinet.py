"""Tests for the Energinet (Denmark) provider."""

from unittest import mock

import pytest

import providers
from providers import energinet


class TestCheckCarbonIntensity:
    @mock.patch("providers.energinet.request")
    def test_green(self, req):
        req.return_value = {"records": [{"PriceArea": "DK1", "CO2Emission": 90.0}]}
        assert energinet.check_carbon_intensity("DK-DK1", 200) == (True, 90)

    @mock.patch("providers.energinet.request")
    def test_over_threshold(self, req):
        req.return_value = {"records": [{"CO2Emission": 410.0}]}
        assert energinet.check_carbon_intensity("DK-DK2", 200) == (False, 410)

    @pytest.mark.parametrize(
        "response",
        [None, {"records": []}],
        ids=["no_data", "empty_records"],
    )
    @mock.patch("providers.energinet.request")
    def test_returns_none_none_without_usable_data(self, req, response):
        req.return_value = response
        assert energinet.check_carbon_intensity("DK1", 200) == (None, None)

    @mock.patch("providers.energinet.request")
    def test_area_in_filter(self, req):
        req.return_value = {"records": [{"CO2Emission": 100.0}]}
        energinet.check_carbon_intensity("DK-DK2", 200)
        url = req.call_args.args[0]
        assert "DK2" in url and "CO2Emis" in url


class TestRouting:
    @pytest.mark.parametrize("zone", ["DK-DK1", "DK-DK2", "DK1", "DK2"])
    def test_detect_provider_routes_denmark(self, zone):
        assert providers.detect_provider(zone) == providers.PROVIDER_ENERGINET
