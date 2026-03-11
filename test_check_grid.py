"""Tests for carbon-aware dispatcher."""

import os
import tempfile
from unittest import mock

import pytest
import requests

import check_grid
from providers import (
    AUTO_CLEANEST_ZONES,
    AUTO_ESCAPE_COAL_ZONES,
    AUTO_GREEN_ZONES,
    ESCAPE_COAL_MAPPINGS,
    PROVIDER_AEMO,
    PROVIDER_EIA,
    PROVIDER_ELECTRICITY_MAPS,
    PROVIDER_ENTSOE,
    PROVIDER_ESKOM,
    PROVIDER_GRID_INDIA,
    PROVIDER_ONS_BRAZIL,
    PROVIDER_OPEN_METEO,
    PROVIDER_UK,
    detect_provider,
    sort_auto_green_by_time,
    _time_priority_score,
)
from providers import (
    aemo, eia, electricity_maps, entsoe, eskom,
    grid_india, gridstatus, ons_brazil, open_meteo, uk,
)
from providers.base import api_request, api_request_with_header, compute_trend
from providers.runners import (
    format_runner_label,
    format_runson_label,
    get_azure_region,
    get_cloud_region,
    get_gcp_region,
    ZONE_TO_AWS_REGION,
    ZONE_TO_GCP_REGION,
    ZONE_TO_AZURE_REGION,
)


@pytest.fixture(autouse=True)
def _clear_env():
    """Ensure test env vars don't leak between tests."""
    keys = [
        "GRID_ZONE", "GRID_ZONES", "EIA_API_KEY", "GRID_STATUS_API_KEY",
        "ELECTRICITY_MAPS_TOKEN", "MAX_CARBON", "WORKFLOW_ID", "GITHUB_TOKEN",
        "TARGET_REPO", "TARGET_REF", "FAIL_ON_API_ERROR", "ENABLE_FORECAST",
        "MAX_WAIT", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY",
        "RUNNER_PROVIDER", "RUNNER_SPEC", "GITHUB_RUN_ID", "ENTSOE_TOKEN",
        "STRATEGY", "DEADLINE_HOURS", "CARBON_POLICY_PATH",
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
        assert len(result) >= 5
        # auto:green now only includes free-provider zones
        zones = [z["zone"] for z in result]
        assert "CISO" in zones  # US (EIA)
        assert "GB-16" in zones  # UK (free)
        assert "AU-TAS" in zones  # Australia (AEMO)
        assert "BR-S" in zones  # Brazil (ONS)

    def test_auto_green_full(self):
        result = parse("auto:green:full")
        zones = [z["zone"] for z in result]
        assert "CISO" in zones  # Free
        assert "NO-NO1" in zones  # Token-requiring
        assert "CA-QC" in zones  # Token-requiring

    def test_auto_green_case_insensitive(self):
        result = parse("Auto:Green")
        zones = {z["zone"] for z in result}
        assert "CISO" in zones

    def test_auto_green_with_whitespace(self):
        result = parse("  auto:green  ")
        zones = {z["zone"] for z in result}
        assert "CISO" in zones


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

    def test_skips_emaps_zones_without_token_or_coordinates(self):
        """Zones needing Electricity Maps token with no Open-Meteo fallback are skipped."""
        # Use fake zones that have no coordinates and no free provider
        zones = [{"zone": "XX-FAKE1"}, {"zone": "XX-FAKE2"}]
        zone, intensity, label, skipped = check_grid.check_multiple_zones(
            zones, 250, emaps_api_key=""
        )
        assert zone is None
        assert len(skipped) == 2
        assert skipped[0] == ("XX-FAKE1", "no electricity_maps_token")
        assert skipped[1] == ("XX-FAKE2", "no electricity_maps_token")

    @mock.patch("check_grid.check_carbon_intensity")
    def test_emaps_zones_fallback_to_open_meteo(self, mock_check):
        """Zones with Open-Meteo coordinates fall back instead of being skipped."""
        mock_check.return_value = (True, 200)
        zones = [{"zone": "DE", "runner_label": "eu"}]
        zone, intensity, label, skipped = check_grid.check_multiple_zones(
            zones, 250, emaps_api_key=""
        )
        # DE has Open-Meteo coordinates, so it should be checked (not skipped)
        assert zone == "DE"
        assert len(skipped) == 0
        assert mock_check.call_count == 1

    @mock.patch("check_grid.check_carbon_intensity")
    def test_mixed_providers_skip_and_check(self, mock_check):
        """Mix of EIA and unknown zones, no token: EIA checked, unknown skipped."""
        mock_check.return_value = (True, 100)
        zones = [
            {"zone": "CISO", "runner_label": "us"},
            {"zone": "XX-NOPE", "runner_label": "eu"},
        ]
        zone, intensity, label, skipped = check_grid.check_multiple_zones(
            zones, 250, emaps_api_key=""
        )
        assert zone == "CISO"
        assert intensity == 100
        assert len(skipped) == 1
        assert skipped[0][0] == "XX-NOPE"
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
        mock_forecast.assert_called_once_with("CISO", 250, PROVIDER_EIA, "gs-key", "", "")

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


# ---------------------------------------------------------------------------
# Runner provider tests
# ---------------------------------------------------------------------------

class TestGetCloudRegion:
    def test_us_zones(self):
        assert get_cloud_region("CISO") == "us-west-1"
        assert get_cloud_region("BPAT") == "us-west-2"
        assert get_cloud_region("PJM") == "us-east-1"
        assert get_cloud_region("ERCO") == "us-east-2"

    def test_uk_zones(self):
        assert get_cloud_region("GB") == "eu-west-2"
        assert get_cloud_region("GB-16") == "eu-west-2"

    def test_europe_zones(self):
        assert get_cloud_region("NO-NO1") == "eu-north-1"
        assert get_cloud_region("FR") == "eu-west-3"
        assert get_cloud_region("DE") == "eu-central-1"

    def test_canada_zones(self):
        assert get_cloud_region("CA-QC") == "ca-central-1"

    def test_asia_pacific(self):
        assert get_cloud_region("JP-TK") == "ap-northeast-1"
        assert get_cloud_region("AU-NSW") == "ap-southeast-2"
        assert get_cloud_region("SG") == "ap-southeast-1"

    def test_latin_america(self):
        assert get_cloud_region("BR-CS") == "sa-east-1"

    def test_unknown_zone_returns_default(self):
        assert get_cloud_region("UNKNOWN-ZONE") == "us-east-1"


class TestFormatRunsonLabel:
    def test_basic(self):
        label = format_runson_label("CISO", "12345")
        assert label == "runs-on=12345/runner=2cpu-linux-x64/region=us-west-1"

    def test_custom_spec(self):
        label = format_runson_label("GB", "99999", "4cpu-linux-arm64")
        assert label == "runs-on=99999/runner=4cpu-linux-arm64/region=eu-west-2"

    def test_europe_region(self):
        label = format_runson_label("NO-NO1", "111")
        assert "region=eu-north-1" in label


class TestFormatRunnerLabel:
    def test_runson_provider(self):
        label = format_runner_label("CISO", "runson", "12345")
        assert label == "runs-on=12345/runner=2cpu-linux-x64/region=us-west-1"

    def test_runson_with_custom_spec(self):
        label = format_runner_label("DE", "runson", "12345", "8cpu-linux-x64")
        assert label == "runs-on=12345/runner=8cpu-linux-x64/region=eu-central-1"

    def test_runson_without_run_id_returns_none(self):
        label = format_runner_label("CISO", "runson", "")
        assert label is None

    def test_unknown_provider_returns_none(self):
        label = format_runner_label("CISO", "unknown-provider", "12345")
        assert label is None

    def test_empty_provider_returns_none(self):
        label = format_runner_label("CISO", "", "12345")
        assert label is None

    def test_case_insensitive(self):
        label = format_runner_label("CISO", "RunsOn", "12345")
        assert "region=us-west-1" in label


class TestSetRunnerOutputs:
    @mock.patch("check_grid.set_output")
    def test_no_provider_with_user_label(self, mock_output):
        check_grid.set_runner_outputs("CISO", "my-runner", "", "", "")
        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["cloud_region"] == "us-west-1"
        assert output_calls["runner_label"] == "my-runner"

    @mock.patch("check_grid.set_output")
    def test_no_provider_no_label(self, mock_output):
        check_grid.set_runner_outputs("CISO", None, "", "", "")
        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["cloud_region"] == "us-west-1"
        assert "runner_label" not in output_calls

    @mock.patch("check_grid.set_output")
    def test_runson_provider(self, mock_output):
        check_grid.set_runner_outputs("DE", None, "runson", "", "12345")
        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["cloud_region"] == "eu-central-1"
        assert "runs-on=12345" in output_calls["runner_label"]
        assert "region=eu-central-1" in output_calls["runner_label"]

    @mock.patch("check_grid.set_output")
    def test_runson_overrides_user_label(self, mock_output):
        """Provider-formatted label takes precedence over user label."""
        check_grid.set_runner_outputs("CISO", "my-label", "runson", "", "12345")
        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert "runs-on=12345" in output_calls["runner_label"]
        assert output_calls["runner_label"] != "my-label"

    @mock.patch("check_grid.set_output")
    def test_runson_fallback_to_user_label_without_run_id(self, mock_output):
        """Falls back to user label if RunsOn can't format (no run_id)."""
        check_grid.set_runner_outputs("CISO", "my-label", "runson", "", "")
        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["runner_label"] == "my-label"


class TestRoutingIntegration:
    """Integration tests: main() sets cloud_region and provider-formatted labels."""

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_single_zone_with_runson_provider(self, mock_summary, mock_output, mock_check):
        mock_check.return_value = (True, 50)

        os.environ["GRID_ZONE"] = "CISO"
        os.environ["WORKFLOW_ID"] = ""
        os.environ["RUNNER_PROVIDER"] = "runson"
        os.environ["RUNNER_SPEC"] = "4cpu-linux-x64"
        os.environ["GITHUB_RUN_ID"] = "98765"

        check_grid.main()

        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["grid_clean"] == "true"
        assert output_calls["cloud_region"] == "us-west-1"
        assert output_calls["runner_label"] == "runs-on=98765/runner=4cpu-linux-x64/region=us-west-1"

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_multi_zone_with_runson_provider(self, mock_summary, mock_output, mock_check):
        mock_check.side_effect = [
            (False, 400),  # ERCO dirty
            (True, 80),    # GB green
        ]

        os.environ["GRID_ZONES"] = "ERCO,GB"
        os.environ["WORKFLOW_ID"] = ""
        os.environ["RUNNER_PROVIDER"] = "runson"
        os.environ["GITHUB_RUN_ID"] = "11111"

        check_grid.main()

        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["grid_zone"] == "GB"
        assert output_calls["cloud_region"] == "eu-west-2"
        assert "region=eu-west-2" in output_calls["runner_label"]

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_cloud_region_output_without_provider(self, mock_summary, mock_output, mock_check):
        """cloud_region is always set even without a runner_provider."""
        mock_check.return_value = (True, 100)

        os.environ["GRID_ZONE"] = "NO-NO1"
        os.environ["WORKFLOW_ID"] = ""
        os.environ.pop("RUNNER_PROVIDER", None)

        check_grid.main()

        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["cloud_region"] == "eu-north-1"


# ---------------------------------------------------------------------------
# AEMO provider tests
# ---------------------------------------------------------------------------

class TestAemoDetectProvider:
    def test_au_nsw(self):
        assert detect_provider("AU-NSW") == PROVIDER_AEMO

    def test_au_tas(self):
        assert detect_provider("AU-TAS") == PROVIDER_AEMO

    def test_au_vic(self):
        assert detect_provider("AU-VIC") == PROVIDER_AEMO


class TestAemoFuelMixToIntensity:
    def test_all_coal(self):
        data = [{"REGIONID": "NSW1", "FUELTYPE": "Black Coal", "GEN_MW": 1000}]
        assert aemo._fuel_mix_to_intensity(data, "NSW1") == 820

    def test_all_wind(self):
        data = [{"REGIONID": "NSW1", "FUELTYPE": "Wind", "GEN_MW": 500}]
        assert aemo._fuel_mix_to_intensity(data, "NSW1") == 0

    def test_mixed(self):
        data = [
            {"REGIONID": "NSW1", "FUELTYPE": "Black Coal", "GEN_MW": 500},
            {"REGIONID": "NSW1", "FUELTYPE": "Solar", "GEN_MW": 500},
        ]
        # (500*820 + 500*0) / 1000 = 410
        assert aemo._fuel_mix_to_intensity(data, "NSW1") == 410

    def test_filters_by_region(self):
        data = [
            {"REGIONID": "NSW1", "FUELTYPE": "Wind", "GEN_MW": 1000},
            {"REGIONID": "QLD1", "FUELTYPE": "Black Coal", "GEN_MW": 1000},
        ]
        assert aemo._fuel_mix_to_intensity(data, "NSW1") == 0

    def test_empty_data(self):
        assert aemo._fuel_mix_to_intensity([], "NSW1") is None

    def test_negative_gen_ignored(self):
        data = [
            {"REGIONID": "NSW1", "FUELTYPE": "Wind", "GEN_MW": 100},
            {"REGIONID": "NSW1", "FUELTYPE": "Battery", "GEN_MW": -50},
        ]
        assert aemo._fuel_mix_to_intensity(data, "NSW1") == 0


class TestAemoCheckCarbonIntensity:
    @mock.patch("providers.aemo._fetch_fuel_data")
    def test_green(self, mock_fetch):
        mock_fetch.return_value = [
            {"REGIONID": "TAS1", "FUELTYPE": "Hydro", "GEN_MW": 900},
            {"REGIONID": "TAS1", "FUELTYPE": "Wind", "GEN_MW": 100},
        ]
        is_green, intensity = aemo.check_carbon_intensity("AU-TAS", 250)
        assert is_green is True
        assert intensity == 0

    @mock.patch("providers.aemo._fetch_fuel_data")
    def test_dirty(self, mock_fetch):
        mock_fetch.return_value = [
            {"REGIONID": "VIC1", "FUELTYPE": "Brown Coal", "GEN_MW": 800},
            {"REGIONID": "VIC1", "FUELTYPE": "Wind", "GEN_MW": 200},
        ]
        is_green, intensity = aemo.check_carbon_intensity("AU-VIC", 250)
        assert is_green is False
        # (800*900 + 200*0) / 1000 = 720
        assert intensity == 720

    @mock.patch("providers.aemo._fetch_fuel_data")
    def test_api_error(self, mock_fetch):
        mock_fetch.return_value = None
        is_green, intensity = aemo.check_carbon_intensity("AU-NSW", 250)
        assert is_green is None
        assert intensity is None

    def test_unknown_zone(self):
        is_green, intensity = aemo.check_carbon_intensity("AU-UNKNOWN", 250)
        assert is_green is None
        assert intensity is None

    def test_forecast_not_available(self):
        dt, intensity = aemo.get_forecast("AU-NSW", 250)
        assert dt is None
        assert intensity is None


# ---------------------------------------------------------------------------
# ENTSO-E provider tests
# ---------------------------------------------------------------------------

class TestEntsoeDetectProvider:
    def test_de_with_token(self):
        assert detect_provider("DE", entsoe_token="my-token") == PROVIDER_ENTSOE

    def test_de_without_token(self):
        # DE now has Open-Meteo coordinates, so it falls back there instead of Electricity Maps
        assert detect_provider("DE") == PROVIDER_OPEN_METEO

    def test_fr_with_token(self):
        assert detect_provider("FR", entsoe_token="tok") == PROVIDER_ENTSOE

    def test_non_eu_zone_with_token(self):
        """Non-EU zone should not use ENTSO-E even with token."""
        assert detect_provider("CISO", entsoe_token="tok") == PROVIDER_EIA


class TestEntsoeParseGenerationXml:
    def test_basic_parse(self):
        xml = """
        <TimeSeries>
            <MktPSRType><psrType>B16</psrType></MktPSRType>
            <Period><Point><quantity>500.0</quantity></Point></Period>
        </TimeSeries>
        <TimeSeries>
            <MktPSRType><psrType>B04</psrType></MktPSRType>
            <Period><Point><quantity>300.0</quantity></Point></Period>
        </TimeSeries>
        """
        result = entsoe._parse_generation_xml(xml)
        assert len(result) == 2
        assert ("B16", 500.0) in result
        assert ("B04", 300.0) in result

    def test_zero_quantity_excluded(self):
        xml = """
        <TimeSeries>
            <MktPSRType><psrType>B16</psrType></MktPSRType>
            <Period><Point><quantity>0</quantity></Point></Period>
        </TimeSeries>
        """
        result = entsoe._parse_generation_xml(xml)
        assert len(result) == 0

    def test_empty_xml(self):
        result = entsoe._parse_generation_xml("")
        assert result == []


class TestEntsoeCheckCarbonIntensity:
    @mock.patch("providers.entsoe.requests.get")
    def test_green(self, mock_get):
        xml = """
        <TimeSeries>
            <MktPSRType><psrType>B19</psrType></MktPSRType>
            <Period><Point><quantity>800</quantity></Point></Period>
        </TimeSeries>
        <TimeSeries>
            <MktPSRType><psrType>B04</psrType></MktPSRType>
            <Period><Point><quantity>200</quantity></Point></Period>
        </TimeSeries>
        """
        mock_get.return_value = mock.Mock(status_code=200, text=xml)
        is_green, intensity = entsoe.check_carbon_intensity("DE", 250, "token")
        assert is_green is True
        # (800*0 + 200*490) / 1000 = 98
        assert intensity == 98

    @mock.patch("providers.entsoe.requests.get")
    def test_dirty(self, mock_get):
        xml = """
        <TimeSeries>
            <MktPSRType><psrType>B05</psrType></MktPSRType>
            <Period><Point><quantity>700</quantity></Point></Period>
        </TimeSeries>
        <TimeSeries>
            <MktPSRType><psrType>B04</psrType></MktPSRType>
            <Period><Point><quantity>300</quantity></Point></Period>
        </TimeSeries>
        """
        mock_get.return_value = mock.Mock(status_code=200, text=xml)
        is_green, intensity = entsoe.check_carbon_intensity("DE", 250, "token")
        assert is_green is False
        # (700*820 + 300*490) / 1000 = 721
        assert intensity == 721

    def test_no_token(self):
        is_green, intensity = entsoe.check_carbon_intensity("DE", 250, "")
        assert is_green is None
        assert intensity is None

    def test_unknown_zone(self):
        is_green, intensity = entsoe.check_carbon_intensity("XX-UNKNOWN", 250, "token")
        assert is_green is None
        assert intensity is None

    @mock.patch("providers.entsoe.requests.get")
    def test_auth_failure(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=401, text="Unauthorized")
        is_green, intensity = entsoe.check_carbon_intensity("DE", 250, "bad-token")
        assert is_green is None
        assert intensity is None

    @mock.patch("providers.entsoe.requests.get")
    def test_rate_limit(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=429, text="Too Many Requests")
        is_green, intensity = entsoe.check_carbon_intensity("DE", 250, "token")
        assert is_green is None
        assert intensity is None

    @mock.patch("providers.entsoe.requests.get")
    def test_network_error(self, mock_get):
        mock_get.side_effect = requests.RequestException("timeout")
        is_green, intensity = entsoe.check_carbon_intensity("DE", 250, "token")
        assert is_green is None
        assert intensity is None


class TestEntsoeForecast:
    @mock.patch("providers.entsoe.requests.get")
    def test_forecast_green(self, mock_get):
        xml = """
        <TimeSeries>
            <MktPSRType><psrType>B19</psrType></MktPSRType>
            <Period><Point><quantity>900</quantity></Point></Period>
        </TimeSeries>
        <TimeSeries>
            <MktPSRType><psrType>B04</psrType></MktPSRType>
            <Period><Point><quantity>100</quantity></Point></Period>
        </TimeSeries>
        """
        mock_get.return_value = mock.Mock(status_code=200, text=xml)
        dt, intensity = entsoe.get_forecast("DE", 250, "token")
        assert dt is not None
        assert intensity == 49  # (900*0 + 100*490) / 1000

    def test_no_token(self):
        dt, intensity = entsoe.get_forecast("DE", 250, "")
        assert dt is None
        assert intensity is None


# ---------------------------------------------------------------------------
# Open-Meteo provider tests
# ---------------------------------------------------------------------------

class TestOpenMeteoEstimateIntensity:
    def test_high_solar_high_wind(self):
        # 40% solar reduction * 25% wind reduction = 0.60 * 0.75 = 0.45
        intensity = open_meteo._estimate_intensity_from_weather(700, 10)
        assert intensity == round(550 * 0.60 * 0.75)

    def test_no_solar_no_wind(self):
        # Night, calm — full base intensity
        intensity = open_meteo._estimate_intensity_from_weather(0, 1)
        assert intensity == 550

    def test_medium_solar_only(self):
        intensity = open_meteo._estimate_intensity_from_weather(400, 1)
        assert intensity == round(550 * 0.80 * 1.0)

    def test_high_wind_only(self):
        intensity = open_meteo._estimate_intensity_from_weather(0, 9)
        assert intensity == round(550 * 1.0 * 0.75)


class TestOpenMeteoCheckCarbonIntensity:
    @mock.patch("providers.open_meteo.requests.get")
    def test_green_zone(self, mock_get):
        mock_get.return_value = mock.Mock(
            status_code=200,
            json=lambda: {
                "current": {
                    "global_tilted_irradiance": 700,
                    "wind_speed_10m": 10,
                }
            },
        )
        is_green, intensity = open_meteo.check_carbon_intensity("ZA", 300)
        assert is_green is True
        assert intensity == round(550 * 0.60 * 0.75)

    @mock.patch("providers.open_meteo.requests.get")
    def test_dirty_zone(self, mock_get):
        mock_get.return_value = mock.Mock(
            status_code=200,
            json=lambda: {
                "current": {
                    "global_tilted_irradiance": 0,
                    "wind_speed_10m": 1,
                }
            },
        )
        is_green, intensity = open_meteo.check_carbon_intensity("ZA", 300)
        assert is_green is False
        assert intensity == 550

    def test_unknown_zone_no_coords(self):
        is_green, intensity = open_meteo.check_carbon_intensity("XX-NONE", 300)
        assert is_green is None
        assert intensity is None

    @mock.patch("providers.open_meteo.requests.get")
    def test_with_explicit_lat_lon(self, mock_get):
        mock_get.return_value = mock.Mock(
            status_code=200,
            json=lambda: {
                "current": {
                    "global_tilted_irradiance": 600,
                    "wind_speed_10m": 5,
                }
            },
        )
        is_green, intensity = open_meteo.check_carbon_intensity(
            "CUSTOM", 500, lat=40.0, lon=-74.0
        )
        assert is_green is True

    @mock.patch("providers.open_meteo.requests.get")
    def test_api_error(self, mock_get):
        mock_get.side_effect = requests.RequestException("timeout")
        is_green, intensity = open_meteo.check_carbon_intensity("ZA", 300)
        assert is_green is None
        assert intensity is None

    @mock.patch("providers.open_meteo.requests.get")
    def test_non_200_response(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=500, text="Server Error")
        is_green, intensity = open_meteo.check_carbon_intensity("ZA", 300)
        assert is_green is None
        assert intensity is None


class TestOpenMeteoForecast:
    @mock.patch("providers.open_meteo.requests.get")
    def test_finds_green_window(self, mock_get):
        mock_get.return_value = mock.Mock(
            status_code=200,
            json=lambda: {
                "hourly": {
                    "time": ["2026-03-10 06:00", "2026-03-10 12:00"],
                    "global_tilted_irradiance": [0, 700],
                    "wind_speed_10m": [2, 10],
                }
            },
        )
        dt, intensity = open_meteo.get_forecast("ZA", 300)
        assert dt is not None
        assert "12:00" in dt

    @mock.patch("providers.open_meteo.requests.get")
    def test_no_green_window(self, mock_get):
        mock_get.return_value = mock.Mock(
            status_code=200,
            json=lambda: {
                "hourly": {
                    "time": ["2026-03-10 06:00"],
                    "global_tilted_irradiance": [0],
                    "wind_speed_10m": [1],
                }
            },
        )
        dt, intensity = open_meteo.get_forecast("ZA", 100)
        assert dt == "none_in_forecast"
        assert intensity is None

    def test_history_trend_returns_none(self):
        assert open_meteo.get_history_trend("ZA") is None


# ---------------------------------------------------------------------------
# Time-aware auto:green sorting tests
# ---------------------------------------------------------------------------

class TestTimePriorityScore:
    def test_solar_peak(self):
        zone = {"zone": "CISO", "utc_offset": -8, "type": "solar"}
        # 12pm local = 20 UTC
        score = _time_priority_score(zone, 20)
        assert score == 100

    def test_solar_night(self):
        zone = {"zone": "CISO", "utc_offset": -8, "type": "solar"}
        # 2am local = 10 UTC
        score = _time_priority_score(zone, 10)
        assert score == 10

    def test_hydro_always_high(self):
        zone = {"zone": "NO-NO1", "utc_offset": 1, "type": "hydro"}
        # Any hour, hydro should be consistently high
        for utc_hour in [0, 6, 12, 18]:
            score = _time_priority_score(zone, utc_hour)
            assert score >= 80

    def test_wind_higher_at_night(self):
        zone = {"zone": "GB-16", "utc_offset": 0, "type": "wind"}
        night_score = _time_priority_score(zone, 2)   # 2am local
        day_score = _time_priority_score(zone, 14)     # 2pm local
        assert night_score > day_score


class TestSortAutoGreenByTime:
    def test_solar_ranked_high_at_noon(self):
        zones = list(AUTO_GREEN_ZONES)
        # 20 UTC = noon in California (UTC-8)
        sorted_zones = sort_auto_green_by_time(zones, 20)
        zone_names = [z["zone"] for z in sorted_zones]
        # CISO (solar, UTC-8) should be near the top at noon local time
        assert zone_names.index("CISO") < 5

    def test_solar_ranked_low_at_night(self):
        zones = list(AUTO_GREEN_ZONES)
        # 10 UTC = 2am in California (UTC-8)
        sorted_zones = sort_auto_green_by_time(zones, 10)
        zone_names = [z["zone"] for z in sorted_zones]
        # CISO should be near the bottom at 2am local time
        assert zone_names.index("CISO") > len(zone_names) // 2

    def test_preserves_all_zones(self):
        zones = list(AUTO_GREEN_ZONES)
        sorted_zones = sort_auto_green_by_time(zones, 12)
        assert len(sorted_zones) == len(zones)
        assert {z["zone"] for z in sorted_zones} == {z["zone"] for z in zones}


# ---------------------------------------------------------------------------
# Expanded auto:green tests
# ---------------------------------------------------------------------------

class TestExpandedAutoGreen:
    def test_has_global_coverage(self):
        """auto:green includes free-provider zones across multiple continents."""
        zones = {z["zone"] for z in AUTO_GREEN_ZONES}
        # Americas (EIA — free)
        assert "CISO" in zones
        assert "BPAT" in zones
        # UK (free)
        assert "GB-16" in zones
        # Australia (AEMO — free)
        assert "AU-TAS" in zones
        # India (Grid India — free)
        assert "IN-SO" in zones
        # Brazil (ONS — free)
        assert "BR-S" in zones

    def test_auto_green_only_free_providers(self):
        """auto:green should NOT include zones requiring API tokens."""
        zones = {z["zone"] for z in AUTO_GREEN_ZONES}
        # These require Electricity Maps or ENTSO-E tokens
        assert "NO-NO1" not in zones
        assert "FR" not in zones
        assert "CA-QC" not in zones
        assert "NZ-NZN" not in zones

    def test_auto_green_full_includes_token_zones(self):
        """auto:green:full includes both free and token-requiring zones."""
        from providers import AUTO_GREEN_ZONES_FULL
        zones = {z["zone"] for z in AUTO_GREEN_ZONES_FULL}
        assert "CISO" in zones      # Free
        assert "NO-NO1" in zones    # Token-requiring
        assert "CA-QC" in zones     # Token-requiring
        assert "NZ-NZN" in zones    # Token-requiring

    def test_all_zones_have_required_fields(self):
        for zone in AUTO_GREEN_ZONES:
            assert "zone" in zone
            assert "runner_label" in zone
            assert "utc_offset" in zone
            assert "type" in zone
            assert zone["type"] in ("solar", "hydro", "wind", "nuclear")


# ---------------------------------------------------------------------------
# Carbon savings estimation tests
# ---------------------------------------------------------------------------

class TestEstimateCarbonSavings:
    def test_green_grid_saves_co2(self):
        # 50 gCO2eq/kWh vs 450 baseline
        saved, badge_url = check_grid.estimate_carbon_savings(50)
        assert saved > 0
        assert badge_url is not None
        assert "shields.io" in badge_url

    def test_dirty_grid_no_savings(self):
        # 500 gCO2eq/kWh — worse than 450 baseline
        saved, badge_url = check_grid.estimate_carbon_savings(500)
        assert saved == 0

    def test_none_intensity(self):
        saved, badge_url = check_grid.estimate_carbon_savings(None)
        assert saved == 0
        assert badge_url is None

    def test_custom_job_minutes(self):
        saved_short, _ = check_grid.estimate_carbon_savings(100, job_minutes=15)
        saved_long, _ = check_grid.estimate_carbon_savings(100, job_minutes=60)
        assert saved_long > saved_short

    def test_badge_url_format(self):
        _, badge_url = check_grid.estimate_carbon_savings(100)
        assert "CO2_saved" in badge_url
        assert "brightgreen" in badge_url


class TestWriteJobSummaryWithCo2:
    def test_summary_includes_co2_saved(self):
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as f:
            path = f.name
        try:
            os.environ["GITHUB_STEP_SUMMARY"] = path
            check_grid.write_job_summary("CISO", 50, True, 250, co2_saved=5.0)
            with open(path) as f:
                content = f.read()
            assert "CO2 Saved" in content
            assert "5" in content
        finally:
            os.unlink(path)
            os.environ.pop("GITHUB_STEP_SUMMARY", None)


# ---------------------------------------------------------------------------
# check_grid.py dispatch routing tests for new providers
# ---------------------------------------------------------------------------

class TestCheckGridDispatchRouting:
    @mock.patch("providers.aemo.check_carbon_intensity")
    def test_routes_to_aemo(self, mock_aemo):
        mock_aemo.return_value = (True, 100)
        is_green, intensity = check_grid.check_carbon_intensity(
            "AU-NSW", 250, PROVIDER_AEMO
        )
        assert is_green is True
        mock_aemo.assert_called_once_with("AU-NSW", 250)

    @mock.patch("providers.entsoe.check_carbon_intensity")
    def test_routes_to_entsoe(self, mock_entsoe):
        mock_entsoe.return_value = (True, 80)
        is_green, intensity = check_grid.check_carbon_intensity(
            "DE", 250, PROVIDER_ENTSOE, entsoe_token="token"
        )
        assert is_green is True
        mock_entsoe.assert_called_once_with("DE", 250, "token")

    @mock.patch("providers.open_meteo.check_carbon_intensity")
    def test_routes_to_open_meteo(self, mock_om):
        mock_om.return_value = (True, 200)
        is_green, intensity = check_grid.check_carbon_intensity(
            "ZA", 250, PROVIDER_OPEN_METEO
        )
        assert is_green is True
        mock_om.assert_called_once_with("ZA", 250)

    @mock.patch("providers.aemo.get_forecast")
    def test_forecast_routes_to_aemo(self, mock_forecast):
        mock_forecast.return_value = (None, None)
        check_grid.get_forecast("AU-NSW", 250, PROVIDER_AEMO)
        mock_forecast.assert_called_once_with("AU-NSW", 250)

    @mock.patch("providers.entsoe.get_forecast")
    def test_forecast_routes_to_entsoe(self, mock_forecast):
        mock_forecast.return_value = ("2026-03-10T12:00Z", 90)
        check_grid.get_forecast("DE", 250, PROVIDER_ENTSOE, entsoe_token="tok")
        mock_forecast.assert_called_once_with("DE", 250, "tok")

    @mock.patch("providers.open_meteo.get_forecast")
    def test_forecast_routes_to_open_meteo(self, mock_forecast):
        mock_forecast.return_value = ("2026-03-10T12:00Z", 200)
        check_grid.get_forecast("ZA", 250, PROVIDER_OPEN_METEO)
        mock_forecast.assert_called_once_with("ZA", 250)

    @mock.patch("providers.open_meteo.get_history_trend")
    def test_trend_routes_to_open_meteo(self, mock_trend):
        mock_trend.return_value = None
        check_grid.get_history_trend("ZA", PROVIDER_OPEN_METEO)
        mock_trend.assert_called_once_with("ZA")

    # --- New provider routing tests ---

    @mock.patch("providers.grid_india.check_carbon_intensity")
    def test_check_routes_to_grid_india(self, mock_check):
        mock_check.return_value = (True, 300)
        check_grid.check_carbon_intensity("IN-NO", 500, PROVIDER_GRID_INDIA)
        mock_check.assert_called_once_with("IN-NO", 500)

    @mock.patch("providers.ons_brazil.check_carbon_intensity")
    def test_check_routes_to_ons_brazil(self, mock_check):
        mock_check.return_value = (True, 100)
        check_grid.check_carbon_intensity("BR-S", 250, PROVIDER_ONS_BRAZIL)
        mock_check.assert_called_once_with("BR-S", 250)

    @mock.patch("providers.eskom.check_carbon_intensity")
    def test_check_routes_to_eskom(self, mock_check):
        mock_check.return_value = (False, 750)
        check_grid.check_carbon_intensity("ZA", 250, PROVIDER_ESKOM)
        mock_check.assert_called_once_with("ZA", 250)

    @mock.patch("providers.grid_india.get_forecast")
    def test_forecast_routes_to_grid_india(self, mock_forecast):
        mock_forecast.return_value = (None, None)
        check_grid.get_forecast("IN-SO", 250, PROVIDER_GRID_INDIA)
        mock_forecast.assert_called_once_with("IN-SO", 250)

    @mock.patch("providers.ons_brazil.get_forecast")
    def test_forecast_routes_to_ons_brazil(self, mock_forecast):
        mock_forecast.return_value = (None, None)
        check_grid.get_forecast("BR-NE", 250, PROVIDER_ONS_BRAZIL)
        mock_forecast.assert_called_once_with("BR-NE", 250)

    @mock.patch("providers.eskom.get_forecast")
    def test_forecast_routes_to_eskom(self, mock_forecast):
        mock_forecast.return_value = (None, None)
        check_grid.get_forecast("ZA", 250, PROVIDER_ESKOM)
        mock_forecast.assert_called_once_with("ZA", 250)

    @mock.patch("providers.grid_india.get_history_trend")
    def test_trend_routes_to_grid_india(self, mock_trend):
        mock_trend.return_value = None
        check_grid.get_history_trend("IN-WE", PROVIDER_GRID_INDIA)
        mock_trend.assert_called_once_with("IN-WE")

    @mock.patch("providers.ons_brazil.get_history_trend")
    def test_trend_routes_to_ons_brazil(self, mock_trend):
        mock_trend.return_value = None
        check_grid.get_history_trend("BR-S", PROVIDER_ONS_BRAZIL)
        mock_trend.assert_called_once_with("BR-S")

    @mock.patch("providers.eskom.get_history_trend")
    def test_trend_routes_to_eskom(self, mock_trend):
        mock_trend.return_value = None
        check_grid.get_history_trend("ZA", PROVIDER_ESKOM)
        mock_trend.assert_called_once_with("ZA")


# --- New provider detection tests ---

class TestNewProviderDetection:
    def test_india_zones_detect_grid_india(self):
        for zone in ["IN-NO", "IN-SO", "IN-EA", "IN-WE", "IN-NE"]:
            assert detect_provider(zone) == PROVIDER_GRID_INDIA

    def test_brazil_zones_detect_ons_brazil(self):
        for zone in ["BR-S", "BR-SE", "BR-CS", "BR-NE", "BR-N"]:
            assert detect_provider(zone) == PROVIDER_ONS_BRAZIL

    def test_south_africa_detects_eskom(self):
        assert detect_provider("ZA") == PROVIDER_ESKOM

    def test_india_zone_not_uk(self):
        assert detect_provider("IN-NO") != PROVIDER_UK

    def test_brazil_zone_not_eia(self):
        assert detect_provider("BR-S") != PROVIDER_EIA


# --- Grid India provider tests ---

class TestGridIndiaProvider:
    def test_unknown_zone(self):
        is_green, intensity = grid_india.check_carbon_intensity("XX", 250)
        assert is_green is None
        assert intensity is None

    def test_estimate_from_dict_data(self):
        data = {
            "coal": 5000,
            "solar": 2000,
            "wind": 1000,
            "hydro": 500,
            "nuclear": 500,
        }
        intensity = grid_india._estimate_from_national_mix(data)
        assert intensity is not None
        assert 0 < intensity < 820  # Should be between pure coal and zero

    def test_estimate_from_empty_data(self):
        assert grid_india._estimate_from_national_mix({}) is None

    def test_estimate_from_list_data(self):
        data = [{"coal": 3000, "solar": 1000}]
        intensity = grid_india._estimate_from_national_mix(data)
        assert intensity is not None

    @mock.patch("providers.grid_india._fetch_generation_data")
    def test_check_intensity_api_failure(self, mock_fetch):
        mock_fetch.return_value = None
        is_green, intensity = grid_india.check_carbon_intensity("IN-NO", 250)
        assert is_green is None

    @mock.patch("providers.grid_india._fetch_generation_data")
    def test_check_intensity_with_data(self, mock_fetch):
        mock_fetch.return_value = {"coal": 5000, "solar": 3000, "wind": 2000}
        is_green, intensity = grid_india.check_carbon_intensity("IN-SO", 500)
        assert is_green is not None
        assert intensity is not None

    def test_forecast_returns_none(self):
        assert grid_india.get_forecast("IN-NO", 250) == (None, None)

    def test_trend_returns_none(self):
        assert grid_india.get_history_trend("IN-NO") is None


# --- ONS Brazil provider tests ---

class TestOnsBrazilProvider:
    def test_unknown_zone(self):
        is_green, intensity = ons_brazil.check_carbon_intensity("XX", 250)
        assert is_green is None
        assert intensity is None

    def test_calculate_intensity_hydro_dominant(self):
        gen = {"hidraulica": 7000, "termica": 1000, "eolica": 1500, "solar": 500}
        intensity = ons_brazil._calculate_intensity(gen)
        assert intensity is not None
        assert intensity < 200  # Hydro-dominant grid should be clean

    def test_calculate_intensity_empty(self):
        assert ons_brazil._calculate_intensity({}) is None

    def test_parse_energy_balance_dict(self):
        data = {"hidraulica": 5000, "termica": 2000}
        result = ons_brazil._parse_energy_balance(data)
        assert result is not None
        assert "hidraulica" in result

    def test_parse_energy_balance_list(self):
        data = [
            {"fonte": "hidraulica", "geracao": 5000},
            {"fonte": "eolica", "geracao": 1000},
        ]
        result = ons_brazil._parse_energy_balance(data)
        assert result is not None

    def test_parse_energy_balance_none(self):
        assert ons_brazil._parse_energy_balance(None) is None

    @mock.patch("providers.ons_brazil._fetch_energy_balance")
    def test_check_intensity_api_failure(self, mock_fetch):
        mock_fetch.return_value = None
        is_green, intensity = ons_brazil.check_carbon_intensity("BR-S", 250)
        assert is_green is None

    def test_forecast_returns_none(self):
        assert ons_brazil.get_forecast("BR-S", 250) == (None, None)

    def test_trend_returns_none(self):
        assert ons_brazil.get_history_trend("BR-S") is None


# --- Eskom provider tests ---

class TestEskomProvider:
    def test_unknown_zone(self):
        is_green, intensity = eskom.check_carbon_intensity("XX", 250)
        assert is_green is None
        assert intensity is None

    def test_estimation_without_api_data(self):
        intensity = eskom._estimate_intensity(None)
        assert intensity is not None
        assert 600 < intensity < 900  # SA grid is ~85% coal

    def test_estimation_with_api_data(self):
        data = {"coal": 30000, "nuclear": 2000, "wind": 1000, "solar": 500}
        intensity = eskom._estimate_intensity(data)
        assert intensity is not None
        assert intensity > 500  # Coal-dominant

    @mock.patch("providers.eskom._fetch_generation_data")
    def test_check_always_returns_value(self, mock_fetch):
        """Eskom should always return a value (estimation fallback)."""
        mock_fetch.return_value = None
        is_green, intensity = eskom.check_carbon_intensity("ZA", 250)
        assert is_green is not None
        assert intensity is not None
        assert is_green is False  # SA grid is too dirty for 250 threshold

    @mock.patch("providers.eskom._fetch_generation_data")
    def test_check_with_high_threshold(self, mock_fetch):
        mock_fetch.return_value = None
        is_green, intensity = eskom.check_carbon_intensity("ZA", 1000)
        assert is_green is True  # Even SA is green at 1000 threshold

    def test_forecast_returns_none(self):
        assert eskom.get_forecast("ZA", 250) == (None, None)

    def test_trend_returns_none(self):
        assert eskom.get_history_trend("ZA") is None


# --- Auto presets tests ---

class TestAutoCleanestPreset:
    def test_auto_cleanest_expansion(self):
        result = check_grid.expand_auto_zones("auto:cleanest")
        assert result is not None
        assert len(result) == len(AUTO_CLEANEST_ZONES)
        zone_names = {z["zone"] for z in result}
        expected_names = {z["zone"] for z in AUTO_CLEANEST_ZONES}
        assert zone_names == expected_names

    def test_auto_cleanest_includes_free_providers(self):
        result = check_grid.expand_auto_zones("auto:cleanest")
        zone_names = {z["zone"] for z in result}
        # Should include zones from each free provider
        assert "CISO" in zone_names  # EIA
        assert "GB" in zone_names or "GB-16" in zone_names  # UK
        assert "AU-TAS" in zone_names  # AEMO
        assert "IN-SO" in zone_names  # Grid India
        assert "BR-S" in zone_names  # ONS Brazil
        # ZA intentionally excluded — ~85% coal (~750 gCO2eq/kWh)
        assert "ZA" not in zone_names

    def test_auto_cleanest_case_insensitive(self):
        result = check_grid.expand_auto_zones("AUTO:CLEANEST")
        assert result is not None


class TestAutoEscapeCoalPreset:
    def test_escape_coal_expansion(self):
        result = check_grid.expand_auto_zones("auto:escape-coal")
        assert result is not None
        assert len(result) == len(AUTO_ESCAPE_COAL_ZONES)

    def test_escape_coal_specific_zone(self):
        result = check_grid.expand_auto_zones("auto:escape-coal:IN")
        assert result is not None
        zone_names = {z["zone"] for z in result}
        # Should contain clean alternatives for India
        expected = set(ESCAPE_COAL_MAPPINGS["IN"])
        assert zone_names == expected

    def test_escape_coal_china(self):
        result = check_grid.expand_auto_zones("auto:escape-coal:CN")
        assert result is not None
        zone_names = {z["zone"] for z in result}
        assert "NZ-NZN" in zone_names or "AU-TAS" in zone_names

    def test_escape_coal_poland(self):
        result = check_grid.expand_auto_zones("auto:escape-coal:PL")
        assert result is not None
        zone_names = {z["zone"] for z in result}
        assert "NO-NO1" in zone_names

    def test_escape_coal_unknown_zone_uses_default(self):
        result = check_grid.expand_auto_zones("auto:escape-coal:XX")
        assert result is not None
        assert len(result) == len(AUTO_ESCAPE_COAL_ZONES)

    def test_escape_coal_mappings_exist(self):
        """All dirty-grid mappings should have valid clean alternatives."""
        for dirty, alternatives in ESCAPE_COAL_MAPPINGS.items():
            assert len(alternatives) > 0, f"No alternatives for {dirty}"


class TestParseZonesAutoPresets:
    def test_parse_auto_cleanest(self):
        result = check_grid.parse_zones_input("auto:cleanest")
        assert result is not None
        assert len(result) > 0

    def test_parse_auto_escape_coal(self):
        result = check_grid.parse_zones_input("auto:escape-coal")
        assert result is not None
        assert len(result) > 0

    def test_parse_auto_escape_coal_specific(self):
        result = check_grid.parse_zones_input("auto:escape-coal:ZA")
        assert result is not None
        zone_names = {z["zone"] for z in result}
        assert "IS" in zone_names  # Iceland is in ZA escape list


# --- GCP and Azure region tests ---

class TestGcpRegionMapping:
    def test_us_zones(self):
        assert get_gcp_region("CISO") == "us-west1"
        assert get_gcp_region("PJM") == "us-east4"
        assert get_gcp_region("ERCO") == "us-south1"

    def test_eu_zones(self):
        assert get_gcp_region("DE") == "europe-west3"
        assert get_gcp_region("FR") == "europe-west9"
        assert get_gcp_region("NO-NO1") == "europe-north1"

    def test_apac_zones(self):
        assert get_gcp_region("JP-TK") == "asia-northeast1"
        assert get_gcp_region("AU-NSW") == "australia-southeast1"
        assert get_gcp_region("IN-NO") == "asia-south1"

    def test_latam_zones(self):
        assert get_gcp_region("BR-S") == "southamerica-east1"

    def test_default_region(self):
        assert get_gcp_region("UNKNOWN-ZONE") == "us-central1"


class TestAzureRegionMapping:
    def test_us_zones(self):
        assert get_azure_region("CISO") == "westus2"
        assert get_azure_region("PJM") == "eastus"
        assert get_azure_region("ERCO") == "southcentralus"

    def test_eu_zones(self):
        assert get_azure_region("DE") == "germanywestcentral"
        assert get_azure_region("FR") == "francecentral"
        assert get_azure_region("NO-NO1") == "norwayeast"
        assert get_azure_region("SE-SE2") == "swedencentral"

    def test_apac_zones(self):
        assert get_azure_region("JP-TK") == "japaneast"
        assert get_azure_region("AU-NSW") == "australiaeast"
        assert get_azure_region("IN-NO") == "centralindia"

    def test_africa_zones(self):
        assert get_azure_region("ZA") == "southafricanorth"

    def test_default_region(self):
        assert get_azure_region("UNKNOWN-ZONE") == "eastus"


# --- Cloud region recommender output tests ---

class TestCloudRegionRecommender:
    def test_set_runner_outputs_includes_all_clouds(self):
        """set_runner_outputs should set gcp_region and azure_region."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            output_file = f.name
        os.environ["GITHUB_OUTPUT"] = output_file
        try:
            check_grid.set_runner_outputs(
                "CISO", None, "", "", ""
            )
            with open(output_file) as f:
                content = f.read()
            assert "cloud_region=us-west-1" in content
            assert "gcp_region=us-west1" in content
            assert "azure_region=westus2" in content
        finally:
            os.unlink(output_file)


# --- Carbon policy (org config) tests ---

class TestCarbonPolicy:
    def test_no_policy_file(self):
        os.environ["CARBON_POLICY_PATH"] = "/nonexistent/path.yml"
        policy = check_grid.load_carbon_policy()
        assert policy == {}

    def test_load_simple_policy(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False
        ) as f:
            f.write("max_carbon_intensity: 150\n")
            f.write("grid_zones: 'auto:green'\n")
            f.write("enable_forecast: true\n")
            f.write("# This is a comment\n")
            f.write("strategy: queue\n")
            policy_path = f.name

        os.environ["CARBON_POLICY_PATH"] = policy_path
        try:
            policy = check_grid.load_carbon_policy()
            assert policy["max_carbon_intensity"] == "150"
            assert policy["grid_zones"] == "auto:green"
            assert policy["enable_forecast"] == "true"
            assert policy["strategy"] == "queue"
        finally:
            os.unlink(policy_path)

    def test_policy_ignores_comments_and_blanks(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False
        ) as f:
            f.write("# Comment\n\n")
            f.write("max_carbon_intensity: 200\n")
            f.write("\n# Another comment\n")
            policy_path = f.name

        os.environ["CARBON_POLICY_PATH"] = policy_path
        try:
            policy = check_grid.load_carbon_policy()
            assert len(policy) == 1
            assert policy["max_carbon_intensity"] == "200"
        finally:
            os.unlink(policy_path)


# --- Queue strategy tests ---

class TestQueueStrategy:
    @mock.patch("check_grid.check_multiple_zones")
    @mock.patch("check_grid.get_forecast")
    def test_queue_find_optimal_window_found(self, mock_forecast, mock_check):
        mock_forecast.return_value = ("2026-03-10T14:00Z", 120)
        zones = [{"zone": "CISO", "runner_label": None}]
        zone, time, intensity = check_grid.queue_find_optimal_window(
            zones, 250, 24
        )
        assert zone == "CISO"
        assert time == "2026-03-10T14:00Z"
        assert intensity == 120

    @mock.patch("check_grid.get_forecast")
    def test_queue_find_optimal_window_none(self, mock_forecast):
        mock_forecast.return_value = ("none_in_forecast", None)
        zones = [{"zone": "PJM", "runner_label": None}]
        zone, time, intensity = check_grid.queue_find_optimal_window(
            zones, 250, 24
        )
        assert zone is None

    @mock.patch("check_grid.get_forecast")
    def test_queue_picks_cleanest_forecast(self, mock_forecast):
        def side_effect(zone, max_carbon, provider, *args, **kwargs):
            if zone == "CISO":
                return ("2026-03-10T14:00Z", 150)
            if zone == "BPAT":
                return ("2026-03-10T12:00Z", 80)
            return (None, None)

        mock_forecast.side_effect = side_effect
        zones = [
            {"zone": "CISO", "runner_label": None},
            {"zone": "BPAT", "runner_label": None},
        ]
        zone, time, intensity = check_grid.queue_find_optimal_window(
            zones, 250, 24
        )
        assert zone == "BPAT"  # Lower intensity
        assert intensity == 80


# --- Inline mode simplification test ---

class TestInlineMode:
    @mock.patch("check_grid.check_carbon_intensity")
    def test_inline_no_workflow_id(self, mock_check):
        """Inline mode should work without workflow_id or github_token."""
        mock_check.return_value = (True, 100)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            output_file = f.name
        os.environ["GITHUB_OUTPUT"] = output_file
        os.environ["GRID_ZONE"] = "GB"
        os.environ.pop("WORKFLOW_ID", None)
        os.environ.pop("GITHUB_TOKEN", None)
        try:
            # Should not raise — inline mode doesn't need token
            check_grid.main()
            with open(output_file) as f:
                content = f.read()
            assert "grid_clean=true" in content
        finally:
            os.unlink(output_file)


# ---------------------------------------------------------------------------
# Setup wizard tests
# ---------------------------------------------------------------------------

class TestSetupWizard:
    def test_provider_names_cover_all_providers(self):
        """All providers should have display names in the wizard."""
        from setup_wizard import _PROVIDER_NAMES
        from providers import (
            PROVIDER_UK, PROVIDER_EIA, PROVIDER_AEMO, PROVIDER_GRID_INDIA,
            PROVIDER_ONS_BRAZIL, PROVIDER_ESKOM, PROVIDER_ENTSOE,
            PROVIDER_OPEN_METEO, PROVIDER_ELECTRICITY_MAPS,
        )
        for p in [PROVIDER_UK, PROVIDER_EIA, PROVIDER_AEMO, PROVIDER_GRID_INDIA,
                  PROVIDER_ONS_BRAZIL, PROVIDER_ESKOM, PROVIDER_ENTSOE,
                  PROVIDER_OPEN_METEO, PROVIDER_ELECTRICITY_MAPS]:
            assert p in _PROVIDER_NAMES, f"Missing display name for {p}"

    def test_provider_modules_cover_all_providers(self):
        """All providers should have modules in the wizard."""
        from setup_wizard import _PROVIDER_MODULES
        from providers import (
            PROVIDER_UK, PROVIDER_EIA, PROVIDER_AEMO, PROVIDER_GRID_INDIA,
            PROVIDER_ONS_BRAZIL, PROVIDER_ESKOM, PROVIDER_ENTSOE,
            PROVIDER_OPEN_METEO, PROVIDER_ELECTRICITY_MAPS,
        )
        for p in [PROVIDER_UK, PROVIDER_EIA, PROVIDER_AEMO, PROVIDER_GRID_INDIA,
                  PROVIDER_ONS_BRAZIL, PROVIDER_ESKOM, PROVIDER_ENTSOE,
                  PROVIDER_OPEN_METEO, PROVIDER_ELECTRICITY_MAPS]:
            assert p in _PROVIDER_MODULES, f"Missing module for {p}"

    @mock.patch("setup_wizard.uk.check_carbon_intensity", return_value=(True, 100))
    def test_zone_test_uk(self, mock_check):
        from setup_wizard import test_zone
        result = test_zone("GB")
        assert result["status"] == "ok"
        assert result["intensity"] == 100

    def test_zone_test_entsoe_skipped_without_token(self):
        from setup_wizard import test_zone
        result = test_zone("DE", entsoe_token="")
        # DE without entsoe token should use Open-Meteo (if coordinates exist)
        # or be skipped for ENTSO-E
        assert result["status"] in ("ok", "skipped", "error")

    def test_zone_test_emaps_skipped_without_token(self):
        from setup_wizard import test_zone
        # Use a fake zone that only Electricity Maps can handle (no coordinates)
        result = test_zone("XX-NOCOORDS", emaps_api_key="")
        assert result["status"] == "skipped"
        assert "portal.electricitymaps.com" in result["error"]

    @mock.patch("setup_wizard.open_meteo.check_carbon_intensity", return_value=(True, 300))
    def test_zone_with_open_meteo_fallback(self, mock_check):
        from setup_wizard import test_zone
        # SG has Open-Meteo coordinates, should work without emaps token
        result = test_zone("SG", emaps_api_key="")
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# Cloud region mapping completeness
# ---------------------------------------------------------------------------

class TestCloudRegionMappingCompleteness:
    def test_all_auto_cleanest_zones_have_aws_mapping(self):
        """All zones in auto:cleanest should have AWS region mappings."""
        for entry in AUTO_CLEANEST_ZONES:
            zone = entry["zone"]
            region = get_cloud_region(zone)
            # Should not be the default for important zones
            assert region is not None, f"No AWS region for {zone}"

    def test_all_auto_cleanest_zones_have_gcp_mapping(self):
        """All zones in auto:cleanest should have GCP region mappings."""
        for entry in AUTO_CLEANEST_ZONES:
            zone = entry["zone"]
            region = get_gcp_region(zone)
            assert region is not None, f"No GCP region for {zone}"

    def test_all_auto_cleanest_zones_have_azure_mapping(self):
        """All zones in auto:cleanest should have Azure region mappings."""
        for entry in AUTO_CLEANEST_ZONES:
            zone = entry["zone"]
            region = get_azure_region(zone)
            assert region is not None, f"No Azure region for {zone}"

    def test_brazil_se_zone_in_all_clouds(self):
        """BR-SE should have mappings in all three clouds."""
        assert "BR-SE" in ZONE_TO_AWS_REGION
        assert "BR-SE" in ZONE_TO_GCP_REGION
        assert "BR-SE" in ZONE_TO_AZURE_REGION

    def test_nz_zones_in_gcp_and_azure(self):
        """NZ zones should have GCP and Azure mappings."""
        assert "NZ-NZN" in ZONE_TO_GCP_REGION
        assert "NZ-NZN" in ZONE_TO_AZURE_REGION


# ---------------------------------------------------------------------------
# Provider registry consistency
# ---------------------------------------------------------------------------

class TestProviderRegistryConsistency:
    def test_all_providers_in_check_grid_registry(self):
        """All provider constants should be in check_grid's module registry."""
        from check_grid import _PROVIDER_MODULES
        from providers import (
            PROVIDER_UK, PROVIDER_EIA, PROVIDER_AEMO, PROVIDER_GRID_INDIA,
            PROVIDER_ONS_BRAZIL, PROVIDER_ESKOM, PROVIDER_ENTSOE,
            PROVIDER_OPEN_METEO, PROVIDER_ELECTRICITY_MAPS,
        )
        for p in [PROVIDER_UK, PROVIDER_EIA, PROVIDER_AEMO, PROVIDER_GRID_INDIA,
                  PROVIDER_ONS_BRAZIL, PROVIDER_ESKOM, PROVIDER_ENTSOE,
                  PROVIDER_OPEN_METEO, PROVIDER_ELECTRICITY_MAPS]:
            assert p in _PROVIDER_MODULES, f"Missing {p} in _PROVIDER_MODULES"

    def test_all_provider_modules_have_required_functions(self):
        """Each provider module must have check_carbon_intensity, get_forecast, get_history_trend."""
        from check_grid import _PROVIDER_MODULES
        for provider_id, module in _PROVIDER_MODULES.items():
            assert hasattr(module, "check_carbon_intensity"), \
                f"{provider_id} missing check_carbon_intensity"
            assert hasattr(module, "get_forecast"), \
                f"{provider_id} missing get_forecast"
            assert hasattr(module, "get_history_trend"), \
                f"{provider_id} missing get_history_trend"

    def test_detect_provider_prefers_free_over_paid(self):
        """Free providers should be preferred over token-required providers."""
        # India zones should detect Grid India (free), not Electricity Maps
        assert detect_provider("IN-SO") == PROVIDER_GRID_INDIA
        # Brazil zones should detect ONS Brazil (free)
        assert detect_provider("BR-S") == PROVIDER_ONS_BRAZIL
        # South Africa should detect Eskom (free)
        assert detect_provider("ZA") == PROVIDER_ESKOM
        # Australia should detect AEMO (free)
        assert detect_provider("AU-NSW") == PROVIDER_AEMO

    def test_eu_zones_fallback_to_open_meteo_without_token(self):
        """EU zones with coordinates should detect Open-Meteo without ENTSO-E token."""
        assert detect_provider("DE") == PROVIDER_OPEN_METEO
        assert detect_provider("FR") == PROVIDER_OPEN_METEO
        assert detect_provider("NO-NO1") == PROVIDER_OPEN_METEO

    def test_eu_zones_prefer_entsoe_with_token(self):
        """EU zones should prefer ENTSO-E when token is available."""
        assert detect_provider("DE", entsoe_token="tok") == PROVIDER_ENTSOE
        assert detect_provider("FR", entsoe_token="tok") == PROVIDER_ENTSOE


# ---------------------------------------------------------------------------
# Fallback chain tests
# ---------------------------------------------------------------------------

class TestFallbackChain:
    @mock.patch("check_grid.open_meteo.check_carbon_intensity", return_value=(True, 200))
    @mock.patch("check_grid.eia.check_carbon_intensity", return_value=(None, None))
    def test_eia_failure_falls_back_to_open_meteo(self, mock_eia, mock_meteo):
        """When EIA fails for a zone with Open-Meteo coordinates, fallback works."""
        # CISO doesn't have Open-Meteo coords (it's EIA), so let's test with
        # a zone that would hit EIA but also has coords — not realistic for EIA.
        # Instead test the generic fallback path.
        from providers.open_meteo import ZONE_COORDINATES
        # Temporarily add coords for test
        ZONE_COORDINATES["CISO"] = (37.8, -122.4)
        try:
            is_green, intensity = check_grid.check_carbon_intensity(
                "CISO", 250, PROVIDER_EIA, eia_api_key=""
            )
            assert is_green is True
            assert intensity == 200
            mock_meteo.assert_called_once()
        finally:
            del ZONE_COORDINATES["CISO"]

    @mock.patch("check_grid.uk.check_carbon_intensity", return_value=(True, 150))
    def test_no_fallback_when_primary_succeeds(self, mock_uk):
        """Fallback should NOT trigger when primary provider succeeds."""
        with mock.patch("check_grid.open_meteo.check_carbon_intensity") as mock_meteo:
            is_green, intensity = check_grid.check_carbon_intensity(
                "GB", 250, PROVIDER_UK
            )
            assert is_green is True
            assert intensity == 150
            mock_meteo.assert_not_called()

    @mock.patch("check_grid.open_meteo.check_carbon_intensity")
    def test_no_double_fallback_for_open_meteo(self, mock_meteo):
        """Open-Meteo itself should not trigger fallback to Open-Meteo."""
        mock_meteo.return_value = (None, None)
        is_green, intensity = check_grid.check_carbon_intensity(
            "IS", 250, PROVIDER_OPEN_METEO
        )
        assert is_green is None
        # Should only be called once (primary), not twice (no self-fallback)
        assert mock_meteo.call_count == 1
