"""Tests for the EirGrid (Ireland) provider."""

from unittest import mock

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

    @mock.patch("providers.eirgrid.request")
    def test_no_data(self, req):
        req.return_value = None
        assert eirgrid.check_carbon_intensity("IE", 200) == (None, None)

    @mock.patch("providers.eirgrid.request")
    def test_all_null(self, req):
        req.return_value = {"Rows": [{"Value": None}]}
        assert eirgrid.check_carbon_intensity("IE", 200) == (None, None)

    @mock.patch("providers.eirgrid.request")
    def test_region_mapping_in_url(self, req):
        req.return_value = {"Rows": [{"Value": 100.0}]}
        eirgrid.check_carbon_intensity("IE-NI", 200)
        url = req.call_args.args[0]
        assert "region=NI" in url and "area=co2intensity" in url


class TestRouting:
    def test_detect_provider_routes_ireland(self):
        for zone in ("IE", "IE-ROI", "IE-NI", "IE-ALL"):
            assert providers.detect_provider(zone) == providers.PROVIDER_EIRGRID
