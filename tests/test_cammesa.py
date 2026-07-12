"""Tests for the CAMMESA (Argentina) provider."""

from unittest import mock

import providers
from providers import cammesa


class TestIntensity:
    def test_weighted_mix(self):
        # 100 MW hydro(24) + 100 thermal(490) + 100 nuclear(12) + 100 renewable(25)
        # = (2400+49000+1200+2500)/400 = 137.75 -> 138
        rec = {"hidraulico": 100, "termico": 100, "nuclear": 100, "renovable": 100}
        assert cammesa._intensity(rec) == 138

    def test_excludes_imports(self):
        # imports are not in _FACTORS, so they don't affect the result
        rec = {"termico": 100, "importacion": 9999}
        assert cammesa._intensity(rec) == 490

    def test_zero_generation(self):
        assert cammesa._intensity({"hidraulico": 0, "termico": 0}) is None


class TestCheckCarbonIntensity:
    @mock.patch("providers.cammesa.request")
    def test_uses_latest_record(self, req):
        req.return_value = [
            {"sumTotal": 100, "termico": 100},  # older
            {"sumTotal": 100, "hidraulico": 100},  # latest -> hydro 24
        ]
        assert cammesa.check_carbon_intensity("AR", 200) == (True, 24)

    @mock.patch("providers.cammesa.request")
    def test_over_threshold(self, req):
        req.return_value = [{"sumTotal": 100, "termico": 100}]
        assert cammesa.check_carbon_intensity("AR", 200) == (False, 490)

    @mock.patch("providers.cammesa.request")
    def test_no_data(self, req):
        req.return_value = None
        assert cammesa.check_carbon_intensity("AR", 200) == (None, None)

    @mock.patch("providers.cammesa.request")
    def test_empty_list(self, req):
        req.return_value = []
        assert cammesa.check_carbon_intensity("AR", 200) == (None, None)


class TestRouting:
    def test_detect_provider_routes_argentina(self):
        assert providers.detect_provider("AR") == providers.PROVIDER_CAMMESA
