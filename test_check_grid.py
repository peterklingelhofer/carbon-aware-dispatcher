"""Tests for carbon-aware dispatcher."""

import os
import tempfile
from unittest import mock

import pytest

import check_grid
from providers import (
    AUTO_GREEN_ZONES,
    PROVIDER_EIA,
    PROVIDER_ELECTRICITY_MAPS,
    PROVIDER_UK,
    detect_provider,
)
from providers import eia, electricity_maps, gridstatus, uk
from providers.base import api_request, api_request_with_header, compute_trend


@pytest.fixture(autouse=True)
def _clear_env():
    """Ensure test env vars don't leak between tests."""
    keys = [
        "GRID_ZONE", "GRID_ZONES", "EIA_API_KEY", "GRID_STATUS_API_KEY",
        "ELECTRICITY_MAPS_TOKEN", "MAX_CARBON", "WORKFLOW_ID", "GITHUB_TOKEN",
        "TARGET_REPO", "TARGET_REF", "FAIL_ON_API_ERROR", "ENABLE_FORECAST",
        "MAX_WAIT", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY",
    ]
    old = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class TestParseZonesInput:
    def test_single_zone(self):
        result = parse("CISO")
        assert result == [{"zone": "CISO", "runner_label": None}]

    def test_multiple_zones(self):
        result = parse("CISO, ERCO, PJM")
        assert len(result) == 3
        assert result[1]["zone"] == "ERCO"

    def test_zones_with_labels(self):
        result = parse("CISO:runner-cal, GB:runner-uk")
        assert result[0] == {"zone": "CISO", "runner_label": "runner-cal"}
        assert result[1] == {"zone": "GB", "runner_label": "runner-uk"}

    def test_mixed_labels(self):
        result = parse("GB:runner-uk, CISO, ERCO:runner-tex")
        assert result[0]["runner_label"] == "runner-uk"
        assert result[1]["runner_label"] is None
        assert result[2]["runner_label"] == "runner-tex"

    def test_empty_string(self):
        assert parse("") == []

    def test_trailing_commas(self):
        result = parse("CISO,,ERCO,")
        assert len(result) == 2

    def test_auto_green(self):
        result = parse("auto:green")
        assert result == list(AUTO_GREEN_ZONES)
        assert len(result) >= 5
        # Should include a mix of providers
        zones = [z["zone"] for z in result]
        assert "CISO" in zones  # US
        assert "GB-16" in zones  # UK
        assert "NO-NO1" in zones  # Global

    def test_auto_green_case_insensitive(self):
        result = parse("Auto:Green")
        assert result == list(AUTO_GREEN_ZONES)

    def test_auto_green_with_whitespace(self):
        result = parse("  auto:green  ")
        assert result == list(AUTO_GREEN_ZONES)


def parse(s):
    return check_grid.parse_zones_input(s)


class TestDetectProvider:
    def test_uk_national(self):
        assert detect_provider("GB") == PROVIDER_UK

    def test_uk_region(self):
        assert detect_provider("GB-13") == PROVIDER_UK

    def test_uk_national_alias(self):
        assert detect_provider("GB-national") == PROVIDER_UK

    def test_eia_zone(self):
        assert detect_provider("CISO") == PROVIDER_EIA

    def test_eia_us_zone(self):
        assert detect_provider("ERCO") == PROVIDER_EIA

    def test_unknown_zone_uses_electricity_maps(self):
        assert detect_provider("XX-UNKNOWN") == PROVIDER_ELECTRICITY_MAPS


class TestApiRequest:
    @mock.patch("providers.base.requests.get")
    def test_success_no_auth(self, mock_get):
        mock_get.return_value = mock.Mock(
            status_code=200,
            json=lambda: {"data": "value"},
        )
        result = api_request("https://example.com")
        assert result == {"data": "value"}
        call_headers = mock_get.call_args[1].get("headers", {})
        assert "auth-token" not in call_headers

    @mock.patch("providers.base.requests.get")
    def test_success_with_auth(self, mock_get):
        mock_get.return_value = mock.Mock(
            status_code=200,
            json=lambda: {"ok": True},
        )
        result = api_request("https://example.com", "my-token")
        assert result == {"ok": True}
        call_headers = mock_get.call_args[1].get("headers", {})
        assert call_headers.get("auth-token") == "my-token"

    @mock.patch("providers.base.requests.get")
    def test_retries_on_500(self, mock_get):
        fail = mock.Mock(status_code=500, text="Server Error")
        success = mock.Mock(status_code=200, json=lambda: {"ok": True})
        mock_get.side_effect = [fail, success]
        result = api_request("https://example.com")
        assert result == {"ok": True}
        assert mock_get.call_count == 2

    @mock.patch("providers.base.requests.get")
    def test_returns_none_on_all_failures(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=500, text="Server Error")
        result = api_request("https://example.com")
        assert result is None

    @mock.patch("providers.base.requests.get")
    def test_auth_error_no_retry(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=403, text="Forbidden")
        result = api_request("https://example.com")
        assert result is None
        assert mock_get.call_count == 1

    @mock.patch("providers.base.requests.get")
    def test_invalid_json(self, mock_get):
        resp = mock.Mock(status_code=200, text="not json")
        resp.json.side_effect = ValueError("bad")
        mock_get.return_value = resp
        result = api_request("https://example.com")
        assert result is None


# ---------------------------------------------------------------------------
# UK Carbon Intensity API tests
# ---------------------------------------------------------------------------

class TestUkCheckCarbonIntensity:
    @mock.patch("providers.uk.api_request")
    def test_national_green(self, mock_api):
        mock_api.return_value = {
            "data": [{"from": "2026-03-10T00:00Z", "to": "2026-03-10T00:30Z",
                       "intensity": {"forecast": 100, "actual": 95, "index": "low"}}]
        }
        is_green, intensity = uk.check_carbon_intensity("GB", 250)
        assert is_green is True
        assert intensity == 100

    @mock.patch("providers.uk.api_request")
    def test_national_dirty(self, mock_api):
        mock_api.return_value = {
            "data": [{"intensity": {"forecast": 400, "actual": 410, "index": "high"}}]
        }
        is_green, intensity = uk.check_carbon_intensity("GB", 250)
        assert is_green is False
        assert intensity == 400

    @mock.patch("providers.uk.api_request")
    def test_regional_green(self, mock_api):
        mock_api.return_value = {
            "data": [{"data": [{"intensity": {"forecast": 50, "index": "very low"}}]}]
        }
        is_green, intensity = uk.check_carbon_intensity("GB-16", 250)
        assert is_green is True
        assert intensity == 50

    @mock.patch("providers.uk.api_request")
    def test_api_error(self, mock_api):
        mock_api.return_value = None
        is_green, intensity = uk.check_carbon_intensity("GB", 250)
        assert is_green is None
        assert intensity is None

    @mock.patch("providers.uk.api_request")
    def test_unknown_zone(self, mock_api):
        is_green, intensity = uk.check_carbon_intensity("GB-99", 250)
        assert is_green is None
        assert intensity is None
        mock_api.assert_not_called()

    @mock.patch("providers.uk.api_request")
    def test_malformed_response(self, mock_api):
        mock_api.return_value = {"data": [{}]}
        is_green, intensity = uk.check_carbon_intensity("GB", 250)
        assert is_green is None
        assert intensity is None


class TestUkGetForecast:
    @mock.patch("providers.uk.api_request")
    def test_finds_green_window(self, mock_api):
        mock_api.return_value = {
            "data": [
                {"from": "2026-03-10T00:00Z", "intensity": {"forecast": 300}},
                {"from": "2026-03-10T06:00Z", "intensity": {"forecast": 120}},
            ]
        }
        dt, intensity = uk.get_forecast("GB", 200)
        assert dt == "2026-03-10T06:00Z"
        assert intensity == 120

    @mock.patch("providers.uk.api_request")
    def test_no_green_window(self, mock_api):
        mock_api.return_value = {
            "data": [
                {"from": "2026-03-10T00:00Z", "intensity": {"forecast": 300}},
                {"from": "2026-03-10T06:00Z", "intensity": {"forecast": 350}},
            ]
        }
        dt, intensity = uk.get_forecast("GB", 200)
        assert dt == "none_in_forecast"
        assert intensity is None

    @mock.patch("providers.uk.api_request")
    def test_api_error(self, mock_api):
        mock_api.return_value = None
        dt, intensity = uk.get_forecast("GB", 200)
        assert dt is None
        assert intensity is None


class TestUkGetHistoryTrend:
    @mock.patch("providers.uk.api_request")
    def test_decreasing(self, mock_api):
        mock_api.return_value = {
            "data": [
                {"intensity": {"forecast": 400}}, {"intensity": {"forecast": 380}},
                {"intensity": {"forecast": 360}}, {"intensity": {"forecast": 300}},
                {"intensity": {"forecast": 280}}, {"intensity": {"forecast": 260}},
            ]
        }
        assert uk.get_history_trend("GB") == "decreasing"

    @mock.patch("providers.uk.api_request")
    def test_api_error(self, mock_api):
        mock_api.return_value = None
        assert uk.get_history_trend("GB") is None


# ---------------------------------------------------------------------------
# EIA tests
# ---------------------------------------------------------------------------

class TestEiaFuelMixToIntensity:
    def test_all_gas(self):
        data = [{"fueltype": "NG", "value": 100}]
        assert eia._fuel_mix_to_intensity(data) == 490

    def test_all_wind(self):
        data = [{"fueltype": "WND", "value": 100}]
        assert eia._fuel_mix_to_intensity(data) == 0

    def test_mixed(self):
        data = [
            {"fueltype": "NG", "value": 50},   # 50 * 490 = 24500
            {"fueltype": "WND", "value": 50},   # 50 * 0 = 0
        ]
        # 24500 / 100 = 245
        assert eia._fuel_mix_to_intensity(data) == 245

    def test_negative_values_ignored(self):
        data = [
            {"fueltype": "NG", "value": 100},
            {"fueltype": "SUN", "value": -10},  # Negative (consuming), ignored
        ]
        assert eia._fuel_mix_to_intensity(data) == 490

    def test_none_values_ignored(self):
        data = [
            {"fueltype": "NG", "value": 100},
            {"fueltype": "SUN", "value": None},
        ]
        assert eia._fuel_mix_to_intensity(data) == 490

    def test_empty_data(self):
        assert eia._fuel_mix_to_intensity([]) is None

    def test_all_zero(self):
        data = [{"fueltype": "NG", "value": 0}]
        assert eia._fuel_mix_to_intensity(data) is None


class TestEiaCheckCarbonIntensity:
    @mock.patch("providers.eia.api_request")
    def test_green_grid(self, mock_api):
        mock_api.return_value = {
            "response": {
                "data": [
                    {"period": "2026-03-09T06", "respondent": "CISO", "fueltype": "WND", "value": 500},
                    {"period": "2026-03-09T06", "respondent": "CISO", "fueltype": "SUN", "value": 300},
                    {"period": "2026-03-09T06", "respondent": "CISO", "fueltype": "NG", "value": 100},
                ]
            }
        }
        is_green, intensity = eia.check_carbon_intensity("CISO", 250)
        assert is_green is True
        # (100*490) / (500+300+100) = 49000/900 = 54
        assert intensity == 54

    @mock.patch("providers.eia.api_request")
    def test_dirty_grid(self, mock_api):
        mock_api.return_value = {
            "response": {
                "data": [
                    {"period": "2026-03-09T06", "respondent": "ERCO", "fueltype": "COL", "value": 500},
                    {"period": "2026-03-09T06", "respondent": "ERCO", "fueltype": "NG", "value": 500},
                ]
            }
        }
        is_green, intensity = eia.check_carbon_intensity("ERCO", 250)
        assert is_green is False
        # (500*820 + 500*490) / 1000 = 655
        assert intensity == 655

    @mock.patch("providers.eia.api_request")
    def test_api_error(self, mock_api):
        mock_api.return_value = None
        is_green, intensity = eia.check_carbon_intensity("CISO", 250)
        assert is_green is None
        assert intensity is None

    @mock.patch("providers.eia.api_request")
    def test_empty_data(self, mock_api):
        mock_api.return_value = {"response": {"data": []}}
        is_green, intensity = eia.check_carbon_intensity("CISO", 250)
        assert is_green is None
        assert intensity is None

    @mock.patch("providers.eia.api_request")
    def test_uses_demo_key_by_default(self, mock_api):
        mock_api.return_value = {"response": {"data": []}}
        eia.check_carbon_intensity("CISO", 250)
        call_url = mock_api.call_args[0][0]
        assert "DEMO_KEY" in call_url

    @mock.patch("providers.eia.api_request")
    def test_uses_custom_key(self, mock_api):
        mock_api.return_value = {"response": {"data": []}}
        eia.check_carbon_intensity("CISO", 250, eia_api_key="my-key")
        call_url = mock_api.call_args[0][0]
        assert "my-key" in call_url
        assert "DEMO_KEY" not in call_url


class TestEiaGetHistoryTrend:
    @mock.patch("providers.eia.api_request")
    def test_decreasing(self, mock_api):
        rows = []
        gas_amounts = [100, 150, 200, 300, 350, 400]  # newest to oldest
        wind_amounts = [400, 350, 300, 200, 150, 100]
        for i in range(6):
            period = f"2026-03-09T{6-i:02d}"
            rows.append({"period": period, "fueltype": "NG", "value": gas_amounts[i]})
            rows.append({"period": period, "fueltype": "WND", "value": wind_amounts[i]})

        mock_api.return_value = {"response": {"data": rows}}
        result = eia.get_history_trend("CISO")
        assert result == "decreasing"

    @mock.patch("providers.eia.api_request")
    def test_api_error(self, mock_api):
        mock_api.return_value = None
        assert eia.get_history_trend("CISO") is None


# ---------------------------------------------------------------------------
# Electricity Maps tests
# ---------------------------------------------------------------------------

class TestElectricityMapsCheckCarbonIntensity:
    @mock.patch("providers.electricity_maps.api_request_with_header")
    def test_green(self, mock_api):
        mock_api.return_value = {"carbonIntensity": 85.3}
        is_green, intensity = electricity_maps.check_carbon_intensity("DE", 200, "key")
        assert is_green is True
        assert intensity == 85

    @mock.patch("providers.electricity_maps.api_request_with_header")
    def test_dirty(self, mock_api):
        mock_api.return_value = {"carbonIntensity": 450.7}
        is_green, intensity = electricity_maps.check_carbon_intensity("DE", 200, "key")
        assert is_green is False
        assert intensity == 451

    @mock.patch("providers.electricity_maps.api_request_with_header")
    def test_api_error(self, mock_api):
        mock_api.return_value = None
        is_green, intensity = electricity_maps.check_carbon_intensity("DE", 200, "key")
        assert is_green is None
        assert intensity is None

    def test_no_api_key(self):
        is_green, intensity = electricity_maps.check_carbon_intensity("DE", 200, "")
        assert is_green is None
        assert intensity is None

    @mock.patch("providers.electricity_maps.api_request_with_header")
    def test_no_intensity_in_response(self, mock_api):
        mock_api.return_value = {"zone": "DE"}
        is_green, intensity = electricity_maps.check_carbon_intensity("DE", 200, "key")
        assert is_green is None
        assert intensity is None


class TestElectricityMapsGetForecast:
    @mock.patch("providers.electricity_maps.api_request_with_header")
    def test_finds_green_window(self, mock_api):
        mock_api.return_value = {
            "forecast": [
                {"carbonIntensity": 300, "datetime": "2026-03-10T12:00Z"},
                {"carbonIntensity": 80, "datetime": "2026-03-10T14:00Z"},
            ]
        }
        dt, intensity = electricity_maps.get_forecast("DE", 200, "key")
        assert dt == "2026-03-10T14:00Z"
        assert intensity == 80

    @mock.patch("providers.electricity_maps.api_request_with_header")
    def test_no_green_window(self, mock_api):
        mock_api.return_value = {
            "forecast": [
                {"carbonIntensity": 300, "datetime": "2026-03-10T12:00Z"},
                {"carbonIntensity": 350, "datetime": "2026-03-10T14:00Z"},
            ]
        }
        dt, intensity = electricity_maps.get_forecast("DE", 200, "key")
        assert dt == "none_in_forecast"
        assert intensity is None

    @mock.patch("providers.electricity_maps.api_request_with_header")
    def test_api_error(self, mock_api):
        mock_api.return_value = None
        dt, intensity = electricity_maps.get_forecast("DE", 200, "key")
        assert dt is None
        assert intensity is None

    def test_no_api_key(self):
        dt, intensity = electricity_maps.get_forecast("DE", 200, "")
        assert dt is None
        assert intensity is None


class TestElectricityMapsGetHistoryTrend:
    @mock.patch("providers.electricity_maps.api_request_with_header")
    def test_decreasing(self, mock_api):
        mock_api.return_value = {
            "history": [
                {"carbonIntensity": 400}, {"carbonIntensity": 380},
                {"carbonIntensity": 360}, {"carbonIntensity": 300},
                {"carbonIntensity": 280}, {"carbonIntensity": 260},
            ]
        }
        assert electricity_maps.get_history_trend("DE", "key") == "decreasing"

    @mock.patch("providers.electricity_maps.api_request_with_header")
    def test_api_error(self, mock_api):
        mock_api.return_value = None
        assert electricity_maps.get_history_trend("DE", "key") is None

    def test_no_api_key(self):
        assert electricity_maps.get_history_trend("DE", "") is None


# ---------------------------------------------------------------------------
# GridStatus.io forecast tests
# ---------------------------------------------------------------------------

class TestGridstatusApiRequest:
    @mock.patch("providers.base.requests.get")
    def test_success(self, mock_get):
        mock_get.return_value = mock.Mock(
            status_code=200,
            json=lambda: {"data": [{"interval_start_utc": "2026-03-10T12:00:00+00:00"}]},
        )
        result = api_request_with_header("https://api.gridstatus.io/v1/test", "x-api-key", "my-key")
        assert result is not None
        call_headers = mock_get.call_args[1].get("headers", {})
        assert call_headers.get("x-api-key") == "my-key"

    @mock.patch("providers.base.requests.get")
    def test_auth_error(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=401, text="Unauthorized")
        result = api_request_with_header("https://api.gridstatus.io/v1/test", "x-api-key", "bad-key")
        assert result is None
        assert mock_get.call_count == 1


class TestGridstatusGetForecast:
    @mock.patch("providers.gridstatus._get_load_forecast")
    @mock.patch("providers.gridstatus._get_renewable_forecast")
    def test_finds_green_window(self, mock_renew, mock_load):
        mock_renew.return_value = {
            "2026-03-10T12:00:00+00:00": {"solar_mw": 100, "wind_mw": 50},
            "2026-03-10T18:00:00+00:00": {"solar_mw": 8000, "wind_mw": 2000},
        }
        mock_load.return_value = {
            "2026-03-10T12:00:00+00:00": 10000,
            "2026-03-10T18:00:00+00:00": 10000,
        }
        dt, intensity = gridstatus.get_forecast("CISO", 250, "key")
        assert dt == "2026-03-10T18:00:00+00:00"
        assert intensity == 0

    @mock.patch("providers.gridstatus._get_load_forecast")
    @mock.patch("providers.gridstatus._get_renewable_forecast")
    def test_no_green_window(self, mock_renew, mock_load):
        mock_renew.return_value = {
            "2026-03-10T12:00:00+00:00": {"solar_mw": 100, "wind_mw": 50},
        }
        mock_load.return_value = {
            "2026-03-10T12:00:00+00:00": 10000,
        }
        dt, intensity = gridstatus.get_forecast("CISO", 100, "key")
        assert dt == "none_in_forecast"
        assert intensity is None

    @mock.patch("providers.gridstatus._get_renewable_forecast")
    def test_no_renewable_data(self, mock_renew):
        mock_renew.return_value = {}
        dt, intensity = gridstatus.get_forecast("CISO", 250, "key")
        assert dt is None
        assert intensity is None

    def test_unsupported_zone(self):
        dt, intensity = gridstatus.get_forecast("BPAT", 250, "key")
        assert dt is None
        assert intensity is None

    @mock.patch("providers.gridstatus._get_load_forecast")
    @mock.patch("providers.gridstatus._get_renewable_forecast")
    def test_no_key_returns_none(self, mock_renew, mock_load):
        """get_forecast returns None for US zones without GridStatus key."""
        dt, intensity = check_grid.get_forecast("CISO", 250, PROVIDER_EIA, "")
        assert dt is None
        assert intensity is None
        mock_renew.assert_not_called()

    @mock.patch("providers.gridstatus._get_load_forecast")
    @mock.patch("providers.gridstatus._get_renewable_forecast")
    def test_get_forecast_with_key(self, mock_renew, mock_load):
        """get_forecast calls gridstatus when key is provided."""
        mock_renew.return_value = {
            "2026-03-10T18:00:00+00:00": {"solar_mw": 9000, "wind_mw": 1000},
        }
        mock_load.return_value = {
            "2026-03-10T18:00:00+00:00": 10000,
        }
        dt, intensity = check_grid.get_forecast(
            "CISO", 250, PROVIDER_EIA, "my-gridstatus-key"
        )
        assert dt == "2026-03-10T18:00:00+00:00"
        assert intensity == 0


class TestGridstatusRenewableForecast:
    @mock.patch("providers.gridstatus._query_dataset")
    def test_single_dataset_with_location_filter(self, mock_query):
        """CAISO-style: single dataset with location filter."""
        mock_query.return_value = [
            {"interval_start_utc": "2026-03-10T18:00:00+00:00", "location": "CAISO",
             "solar_mw": 8000, "wind_mw": 1500},
            {"interval_start_utc": "2026-03-10T18:00:00+00:00", "location": "NP15",
             "solar_mw": 2000, "wind_mw": 500},
        ]
        iso_config = gridstatus.GRIDSTATUS_ISO_MAP["CISO"]
        result = gridstatus._get_renewable_forecast(iso_config, "key", "2026-03-10")
        assert "2026-03-10T18:00:00+00:00" in result
        assert result["2026-03-10T18:00:00+00:00"]["solar_mw"] == 8000
        assert result["2026-03-10T18:00:00+00:00"]["wind_mw"] == 1500

    @mock.patch("providers.gridstatus._query_dataset")
    def test_separate_solar_wind_datasets(self, mock_query):
        """PJM-style: separate solar and wind datasets."""
        mock_query.side_effect = [
            # solar
            [{"interval_start_utc": "2026-03-10T18:00:00+00:00", "solar_forecast": 3000}],
            # wind
            [{"interval_start_utc": "2026-03-10T18:00:00+00:00", "wind_forecast": 2000}],
        ]
        iso_config = gridstatus.GRIDSTATUS_ISO_MAP["PJM"]
        result = gridstatus._get_renewable_forecast(iso_config, "key", "2026-03-10")
        assert result["2026-03-10T18:00:00+00:00"]["solar_mw"] == 3000
        assert result["2026-03-10T18:00:00+00:00"]["wind_mw"] == 2000


# ---------------------------------------------------------------------------
# Provider-agnostic tests
# ---------------------------------------------------------------------------

class TestComputeTrend:
    def test_decreasing(self):
        points = [400, 380, 360, 300, 280, 260]
        assert compute_trend(points) == "decreasing"

    def test_increasing(self):
        points = [100, 120, 130, 200, 250, 300]
        assert compute_trend(points) == "increasing"

    def test_stable(self):
        points = [200, 200, 200, 200, 200, 200]
        assert compute_trend(points) == "stable"

    def test_insufficient_data(self):
        assert compute_trend([100, 200]) is None


class TestCheckMultipleZones:
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.detect_provider", return_value=PROVIDER_EIA)
    def test_picks_greenest(self, _mock_detect, mock_check):
        mock_check.side_effect = [
            (True, 200),   # zone A
            (True, 50),    # zone B (best)
            (False, 400),  # zone C
        ]
        zones = [
            {"zone": "CISO", "runner_label": "label-a"},
            {"zone": "NYIS", "runner_label": "label-b"},
            {"zone": "ERCO", "runner_label": "label-c"},
        ]
        zone, intensity, label, skipped = check_grid.check_multiple_zones(zones, 250)
        assert zone == "NYIS"
        assert intensity == 50
        assert label == "label-b"
        assert skipped == []

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.detect_provider", return_value=PROVIDER_EIA)
    def test_all_dirty(self, _mock_detect, mock_check):
        mock_check.side_effect = [(False, 400), (False, 500)]
        zones = [{"zone": "ERCO"}, {"zone": "PJM"}]
        zone, intensity, label, skipped = check_grid.check_multiple_zones(zones, 250)
        assert zone is None

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.detect_provider", return_value=PROVIDER_EIA)
    def test_all_errors(self, _mock_detect, mock_check):
        mock_check.side_effect = [(None, None), (None, None)]
        zones = [{"zone": "CISO"}, {"zone": "ERCO"}]
        zone, intensity, label, skipped = check_grid.check_multiple_zones(zones, 250)
        assert zone is None
        assert len(skipped) == 2

    def test_skips_emaps_zones_without_token(self):
        """Zones needing Electricity Maps token are skipped with warning."""
        zones = [{"zone": "DE"}, {"zone": "FR"}]
        zone, intensity, label, skipped = check_grid.check_multiple_zones(
            zones, 250, emaps_api_key=""
        )
        assert zone is None
        assert len(skipped) == 2
        assert skipped[0] == ("DE", "no electricity_maps_token")
        assert skipped[1] == ("FR", "no electricity_maps_token")

    @mock.patch("check_grid.check_carbon_intensity")
    def test_mixed_providers_skip_and_check(self, mock_check):
        """Mix of EIA and Electricity Maps zones, no token: EIA checked, global skipped."""
        mock_check.return_value = (True, 100)
        zones = [
            {"zone": "CISO", "runner_label": "us"},
            {"zone": "DE", "runner_label": "eu"},
        ]
        zone, intensity, label, skipped = check_grid.check_multiple_zones(
            zones, 250, emaps_api_key=""
        )
        assert zone == "CISO"
        assert intensity == 100
        assert len(skipped) == 1
        assert skipped[0][0] == "DE"
        # check_carbon_intensity should only be called for CISO
        assert mock_check.call_count == 1


class TestTriggerWorkflow:
    @mock.patch("check_grid.requests.post")
    def test_success(self, mock_post):
        mock_post.return_value = mock.Mock(status_code=204)
        check_grid.trigger_workflow("owner/repo", "build.yml", "token", "main")
        mock_post.assert_called_once()

    @mock.patch("check_grid.requests.post")
    def test_failure_exits(self, mock_post):
        mock_post.return_value = mock.Mock(status_code=422, text="Validation Failed")
        with pytest.raises(SystemExit) as exc_info:
            check_grid.trigger_workflow("owner/repo", "build.yml", "token", "main")
        assert exc_info.value.code == 1

    @mock.patch("check_grid.requests.post")
    def test_network_error_exits(self, mock_post):
        mock_post.side_effect = check_grid.requests.RequestException("timeout")
        with pytest.raises(SystemExit) as exc_info:
            check_grid.trigger_workflow("owner/repo", "build.yml", "token", "main")
        assert exc_info.value.code == 1


class TestSetOutput:
    def test_writes_to_github_output(self):
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as f:
            path = f.name
        try:
            os.environ["GITHUB_OUTPUT"] = path
            check_grid.set_output("grid_clean", "true")
            with open(path) as f:
                content = f.read()
            assert "grid_clean=true" in content
        finally:
            os.unlink(path)
            os.environ.pop("GITHUB_OUTPUT", None)


class TestGetRequiredEnv:
    def test_missing_var_exits(self):
        os.environ.pop("NONEXISTENT_VAR_XYZ", None)
        with pytest.raises(SystemExit) as exc_info:
            check_grid.get_required_env("NONEXISTENT_VAR_XYZ")
        assert exc_info.value.code == 1

    def test_empty_var_exits(self):
        os.environ["EMPTY_VAR_TEST"] = ""
        try:
            with pytest.raises(SystemExit) as exc_info:
                check_grid.get_required_env("EMPTY_VAR_TEST")
            assert exc_info.value.code == 1
        finally:
            os.environ.pop("EMPTY_VAR_TEST", None)

    def test_present_var_returns(self):
        os.environ["PRESENT_VAR_TEST"] = "value123"
        try:
            assert check_grid.get_required_env("PRESENT_VAR_TEST") == "value123"
        finally:
            os.environ.pop("PRESENT_VAR_TEST", None)


class TestHandleDirtyGrid:
    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.get_history_trend")
    @mock.patch("check_grid.set_output")
    def test_uk_always_gets_forecast(self, mock_output, mock_trend, mock_forecast):
        """UK zones get forecast even without enable_forecast since it's free."""
        mock_trend.return_value = "decreasing"
        mock_forecast.return_value = ("2026-03-10T06:00Z", 120)

        check_grid.handle_dirty_grid("GB", 250, 400, enable_forecast=False)

        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["grid_clean"] == "false"
        assert output_calls["carbon_intensity"] == "400"
        assert output_calls["intensity_trend"] == "decreasing"
        assert output_calls["forecast_green_at"] == "2026-03-10T06:00Z"
        mock_forecast.assert_called_once()

    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.get_history_trend")
    @mock.patch("check_grid.set_output")
    def test_eia_no_forecast_without_key(self, mock_output, mock_trend, mock_forecast):
        """EIA zones without GridStatus key don't have forecasts."""
        mock_trend.return_value = "increasing"
        mock_forecast.return_value = (None, None)

        check_grid.handle_dirty_grid("CISO", 250, 400, enable_forecast=True)

        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["grid_clean"] == "false"
        assert output_calls["intensity_trend"] == "increasing"
        assert "forecast_green_at" not in output_calls

    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.get_history_trend")
    @mock.patch("check_grid.set_output")
    def test_eia_with_gridstatus_key_gets_forecast(self, mock_output, mock_trend, mock_forecast):
        """EIA zones with GridStatus key get forecasts."""
        mock_trend.return_value = "decreasing"
        mock_forecast.return_value = ("2026-03-10T18:00:00+00:00", 50)

        check_grid.handle_dirty_grid("CISO", 250, 400, enable_forecast=True,
                                     gridstatus_api_key="gs-key")

        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["forecast_green_at"] == "2026-03-10T18:00:00+00:00"
        assert output_calls["forecast_intensity"] == "50"
        mock_forecast.assert_called_once_with("CISO", 250, PROVIDER_EIA, "gs-key", "")

    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.get_history_trend")
    @mock.patch("check_grid.set_output")
    def test_unknown_intensity(self, mock_output, mock_trend, mock_forecast):
        mock_trend.return_value = None
        mock_forecast.return_value = (None, None)

        check_grid.handle_dirty_grid("GB", 250, None, enable_forecast=False)

        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["carbon_intensity"] == "unknown"

    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.get_history_trend")
    @mock.patch("check_grid.set_output")
    def test_no_green_in_forecast(self, mock_output, mock_trend, mock_forecast):
        mock_trend.return_value = "stable"
        mock_forecast.return_value = ("none_in_forecast", None)

        check_grid.handle_dirty_grid("GB", 250, 400, enable_forecast=False)

        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["forecast_green_at"] == "none_in_forecast"
        assert "forecast_intensity" not in output_calls

    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.get_history_trend")
    @mock.patch("check_grid.set_output")
    def test_electricity_maps_always_gets_forecast(self, mock_output, mock_trend, mock_forecast):
        """Electricity Maps zones get forecast even without enable_forecast."""
        mock_trend.return_value = "stable"
        mock_forecast.return_value = ("2026-03-10T14:00Z", 90)

        check_grid.handle_dirty_grid("DE", 250, 400, enable_forecast=False,
                                     emaps_api_key="em-key")

        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["forecast_green_at"] == "2026-03-10T14:00Z"
        mock_forecast.assert_called_once()

    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.get_history_trend")
    @mock.patch("check_grid.set_output")
    def test_returns_trend_and_forecast(self, mock_output, mock_trend, mock_forecast):
        """handle_dirty_grid returns (trend, forecast_at, forecast_intensity)."""
        mock_trend.return_value = "decreasing"
        mock_forecast.return_value = ("2026-03-10T14:00Z", 90)

        result = check_grid.handle_dirty_grid("GB", 250, 400, enable_forecast=False)
        assert result == ("decreasing", "2026-03-10T14:00Z", 90)


class TestWriteJobSummary:
    def test_writes_summary_green(self):
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as f:
            path = f.name
        try:
            os.environ["GITHUB_STEP_SUMMARY"] = path
            check_grid.write_job_summary("CISO", 45, True, 200)
            with open(path) as f:
                content = f.read()
            assert "Carbon-Aware Dispatcher" in content
            assert "CISO" in content
            assert "45" in content
            assert "clean" in content.lower()
        finally:
            os.unlink(path)
            os.environ.pop("GITHUB_STEP_SUMMARY", None)

    def test_writes_summary_dirty_with_forecast(self):
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as f:
            path = f.name
        try:
            os.environ["GITHUB_STEP_SUMMARY"] = path
            check_grid.write_job_summary(
                "PJM", 380, False, 200,
                trend="decreasing",
                forecast_at="2026-03-10T14:00Z",
                forecast_intensity=150,
            )
            with open(path) as f:
                content = f.read()
            assert "dirty" in content.lower()
            assert "380" in content
            assert "decreasing" in content
            assert "2026-03-10T14:00Z" in content
        finally:
            os.unlink(path)
            os.environ.pop("GITHUB_STEP_SUMMARY", None)

    def test_writes_summary_with_skipped_zones(self):
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as f:
            path = f.name
        try:
            os.environ["GITHUB_STEP_SUMMARY"] = path
            check_grid.write_job_summary(
                "CISO", 100, True, 200,
                skipped=[("DE", "no electricity_maps_token")],
            )
            with open(path) as f:
                content = f.read()
            assert "DE" in content
            assert "no electricity_maps_token" in content
        finally:
            os.unlink(path)
            os.environ.pop("GITHUB_STEP_SUMMARY", None)

    def test_no_summary_without_env(self):
        """Does nothing if GITHUB_STEP_SUMMARY is not set."""
        os.environ.pop("GITHUB_STEP_SUMMARY", None)
        # Should not raise
        check_grid.write_job_summary("CISO", 45, True, 200)


class TestSmartWaitSingle:
    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid._time.sleep")
    def test_becomes_green_after_wait(self, mock_sleep, mock_check, mock_forecast):
        """Grid goes green on second check."""
        mock_check.return_value = (True, 100)
        mock_forecast.return_value = (None, None)

        is_green, intensity, waited = check_grid.smart_wait_single(
            "CISO", 250, 10, PROVIDER_EIA
        )
        assert is_green is True
        assert intensity == 100
        mock_sleep.assert_called_once()

    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid._time.sleep")
    @mock.patch("check_grid._time.time")
    def test_stays_dirty_after_max_wait(self, mock_time, mock_sleep, mock_check, mock_forecast):
        """Grid stays dirty — returns after max_wait exceeded."""
        # Simulate time passing: start at 0, then exceed deadline
        mock_time.side_effect = [0, 0, 601, 601]  # start, loop check, loop check (past deadline), final
        mock_check.return_value = (False, 400)
        mock_forecast.return_value = (None, None)

        is_green, intensity, waited = check_grid.smart_wait_single(
            "CISO", 250, 10, PROVIDER_EIA
        )
        assert is_green is False
        assert intensity == 400


class TestSmartWaitMulti:
    @mock.patch("check_grid.check_multiple_zones")
    @mock.patch("check_grid._time.sleep")
    def test_zone_goes_green(self, mock_sleep, mock_multi):
        """A zone becomes green during wait."""
        mock_multi.return_value = ("CISO", 50, "us-west", [])

        zone, intensity, label, waited, skipped = check_grid.smart_wait_multi(
            [{"zone": "CISO"}, {"zone": "ERCO"}], 250, 10
        )
        assert zone == "CISO"
        assert intensity == 50
        mock_sleep.assert_called_once()


class TestInlineMode:
    """Test that inline mode (no workflow_id) doesn't require token/repo."""

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_inline_mode_green(self, mock_summary, mock_output, mock_check):
        """Inline mode sets outputs but doesn't dispatch."""
        mock_check.return_value = (True, 50)

        os.environ["GRID_ZONE"] = "GB"
        os.environ["WORKFLOW_ID"] = ""
        os.environ.pop("GITHUB_TOKEN", None)
        os.environ.pop("TARGET_REPO", None)

        # Should not raise (no required env check for token/repo)
        check_grid.main()

        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["grid_clean"] == "true"
        assert output_calls["carbon_intensity"] == "50"
