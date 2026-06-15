"""Tests for the Azure Retail Prices lookup."""

from unittest import mock

from providers import azure_pricing


def _clear_cache():
    azure_pricing._cache.clear()


class TestGetRegionPrice:
    def test_empty_region(self):
        assert azure_pricing.get_region_price("") is None

    @mock.patch("providers.azure_pricing.base.request")
    def test_returns_retail_price(self, mock_request):
        _clear_cache()
        mock_request.return_value = {
            "Items": [
                {"retailPrice": 0.096, "productName": "Virtual Machines Dv5", "skuName": "D2s v5"}
            ]
        }
        assert azure_pricing.get_region_price("westus2") == 0.096

    @mock.patch("providers.azure_pricing.base.request")
    def test_skips_windows_and_spot(self, mock_request):
        _clear_cache()
        mock_request.return_value = {
            "Items": [
                {"retailPrice": 0.20, "productName": "Virtual Machines Windows", "skuName": "x"},
                {"retailPrice": 0.05, "productName": "Virtual Machines Dv5 Spot", "skuName": "y"},
                {"retailPrice": 0.10, "productName": "Virtual Machines Dv5", "skuName": "D2s v5"},
            ]
        }
        assert azure_pricing.get_region_price("eastus") == 0.10

    @mock.patch("providers.azure_pricing.base.request")
    def test_none_on_empty_items(self, mock_request):
        _clear_cache()
        mock_request.return_value = {"Items": []}
        assert azure_pricing.get_region_price("nowhere") is None

    @mock.patch("providers.azure_pricing.base.request")
    def test_caches_per_region(self, mock_request):
        _clear_cache()
        mock_request.return_value = {
            "Items": [{"retailPrice": 0.07, "productName": "Virtual Machines", "skuName": "z"}]
        }
        azure_pricing.get_region_price("westeurope")
        azure_pricing.get_region_price("westeurope")
        assert mock_request.call_count == 1
