"""Tests for the Energinet (Denmark) provider."""

from unittest import mock

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

    @mock.patch("providers.energinet.request")
    def test_no_data(self, req):
        req.return_value = None
        assert energinet.check_carbon_intensity("DK1", 200) == (None, None)

    @mock.patch("providers.energinet.request")
    def test_empty_records(self, req):
        req.return_value = {"records": []}
        assert energinet.check_carbon_intensity("DK1", 200) == (None, None)

    @mock.patch("providers.energinet.request")
    def test_area_in_filter(self, req):
        req.return_value = {"records": [{"CO2Emission": 100.0}]}
        energinet.check_carbon_intensity("DK-DK2", 200)
        url = req.call_args.args[0]
        assert "DK2" in url and "CO2Emis" in url


class TestRouting:
    def test_detect_provider_routes_denmark(self):
        for zone in ("DK-DK1", "DK-DK2", "DK1", "DK2"):
            assert providers.detect_provider(zone) == providers.PROVIDER_ENERGINET
