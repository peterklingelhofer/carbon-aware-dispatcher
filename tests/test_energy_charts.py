"""Tests for the Energy-Charts (Fraunhofer ISE) EU provider."""

from unittest import mock

import pytest

import providers
from providers import energy_charts


class TestCheckCarbonIntensity:
    @mock.patch("providers.energy_charts.request")
    def test_green_latest_nonnull(self, req):
        req.return_value = {"co2eq": [300.0, 250.0, None]}  # latest non-null is 250
        assert energy_charts.check_carbon_intensity("DE", 300) == (True, 250)

    @mock.patch("providers.energy_charts.request")
    def test_over_threshold(self, req):
        req.return_value = {"co2eq": [664.7]}
        assert energy_charts.check_carbon_intensity("DE", 200) == (False, 665)

    @pytest.mark.parametrize(
        "response",
        [None, {"co2eq": [None, None]}],
        ids=["no_data", "all_null"],
    )
    @mock.patch("providers.energy_charts.request")
    def test_returns_none_none_without_usable_data(self, req, response):
        req.return_value = response
        assert energy_charts.check_carbon_intensity("IT", 200) == (None, None)

    @mock.patch("providers.energy_charts.request")
    def test_country_lowercased_in_url(self, req):
        req.return_value = {"co2eq": [100.0]}
        energy_charts.check_carbon_intensity("NL", 200)
        assert "country=nl" in req.call_args.args[0]


class TestForecast:
    @mock.patch("providers.energy_charts.request")
    def test_picks_first_future_green_slot(self, req):
        # far-future timestamps so they count as future, second is under threshold
        future = 4102444800  # 2100-01-01
        req.return_value = {
            "unix_seconds": [future, future + 3600],
            "co2eq_forecast": [400.0, 90.0],
        }
        at, val = energy_charts.get_forecast("DE", 200)
        assert val == 90 and at.endswith("Z")

    @mock.patch("providers.energy_charts.request")
    def test_none_in_forecast(self, req):
        future = 4102444800
        req.return_value = {"unix_seconds": [future], "co2eq_forecast": [500.0]}
        assert energy_charts.get_forecast("DE", 200) == ("none_in_forecast", None)


class TestRouting:
    @pytest.mark.parametrize("zone", ["DE", "ES", "IT", "NL", "PL"])
    def test_detect_provider_routes_eu_zones(self, zone):
        assert providers.detect_provider(zone) == providers.PROVIDER_ENERGY_CHARTS

    def test_token_still_prefers_entsoe(self):
        assert providers.detect_provider("DE", entsoe_token="tok") == providers.PROVIDER_ENTSOE
