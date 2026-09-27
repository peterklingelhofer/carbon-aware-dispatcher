"""Tests for the EirGrid (Ireland) provider."""

from unittest import mock

import pytest

import providers
from providers import eirgrid


class TestCheckCarbonIntensity:
    @mock.patch("providers.eirgrid.request")
    def test_green(self, req):
        req.return_value = {
            "Rows": [
                {"EffectiveTime": "17-Jun-2026 13:00:00", "Value": 120.0},
                {"EffectiveTime": "17-Jun-2026 13:15:00", "Value": 95.4},
            ]
        }
        is_green, intensity = eirgrid.check_carbon_intensity("IE", 200)
        assert is_green is True
        assert intensity == 95  # latest non-null, rounded

    @mock.patch("providers.eirgrid.request")
    def test_over_threshold(self, req):
        req.return_value = {"Rows": [{"EffectiveTime": "t", "Value": 410.0}]}
        is_green, intensity = eirgrid.check_carbon_intensity("IE-NI", 200)
        assert is_green is False and intensity == 410

    @mock.patch("providers.eirgrid.request")
    def test_skips_trailing_nulls(self, req):
        req.return_value = {
            "Rows": [
                {"Value": 100.0},
                {"Value": None},
                {"Value": 88.0},
                {"Value": None},
            ]
        }
        _, intensity = eirgrid.check_carbon_intensity("IE", 200)
        assert intensity == 88  # last non-null

    @pytest.mark.parametrize(
        "response",
        [None, {"Rows": [{"Value": None}]}],
        ids=["no_data", "all_null"],
    )
    @mock.patch("providers.eirgrid.request")
    def test_returns_none_none_without_usable_data(self, req, response):
        req.return_value = response
        assert eirgrid.check_carbon_intensity("IE", 200) == (None, None)

    @mock.patch("providers.eirgrid.request")
    def test_region_mapping_in_url(self, req):
        req.return_value = {"Rows": [{"Value": 100.0}]}
        eirgrid.check_carbon_intensity("IE-NI", 200)
        url = req.call_args.args[0]
        assert "region=NI" in url and "area=co2intensity" in url


class TestRouting:
    @pytest.mark.parametrize("zone", ["IE", "IE-ROI", "IE-NI", "IE-ALL"])
    def test_detect_provider_routes_ireland(self, zone):
        assert providers.detect_provider(zone) == providers.PROVIDER_EIRGRID
