"""Tests for check_grid.py."""

import os
import tempfile
from unittest import mock

import pytest

import check_grid


@pytest.fixture(autouse=True)
def _clear_env():
    """Ensure test env vars don't leak between tests."""
    keys = [
        "GRID_ZONE", "GRID_ZONES", "EIA_API_KEY", "MAX_CARBON",
        "WORKFLOW_ID", "GITHUB_TOKEN", "TARGET_REPO", "TARGET_REF",
        "FAIL_ON_API_ERROR", "ENABLE_FORECAST", "GITHUB_OUTPUT",
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


def parse(s):
    return check_grid.parse_zones_input(s)


class TestDetectProvider:
    def test_uk_national(self):
        assert check_grid.detect_provider("GB") == check_grid.PROVIDER_UK

    def test_uk_region(self):
        assert check_grid.detect_provider("GB-13") == check_grid.PROVIDER_UK

    def test_uk_national_alias(self):
        assert check_grid.detect_provider("GB-national") == check_grid.PROVIDER_UK

    def test_eia_zone(self):
        assert check_grid.detect_provider("CISO") == check_grid.PROVIDER_EIA

    def test_eia_us_zone(self):
        assert check_grid.detect_provider("ERCO") == check_grid.PROVIDER_EIA

    def test_eia_unknown_zone(self):
        assert check_grid.detect_provider("XX-UNKNOWN") == check_grid.PROVIDER_EIA


class TestApiRequest:
    @mock.patch("check_grid.requests.get")
    def test_success_no_auth(self, mock_get):
        mock_get.return_value = mock.Mock(
            status_code=200,
            json=lambda: {"data": "value"},
        )
        result = check_grid.api_request("https://example.com")
        assert result == {"data": "value"}
        call_headers = mock_get.call_args[1].get("headers", {})
        assert "auth-token" not in call_headers

    @mock.patch("check_grid.requests.get")
    def test_success_with_auth(self, mock_get):
        mock_get.return_value = mock.Mock(
            status_code=200,
            json=lambda: {"ok": True},
        )
        result = check_grid.api_request("https://example.com", "my-token")
        assert result == {"ok": True}
        call_headers = mock_get.call_args[1].get("headers", {})
        assert call_headers.get("auth-token") == "my-token"

    @mock.patch("check_grid.requests.get")
    def test_retries_on_500(self, mock_get):
        fail = mock.Mock(status_code=500, text="Server Error")
        success = mock.Mock(status_code=200, json=lambda: {"ok": True})
        mock_get.side_effect = [fail, success]
        result = check_grid.api_request("https://example.com")
        assert result == {"ok": True}
        assert mock_get.call_count == 2

    @mock.patch("check_grid.requests.get")
    def test_returns_none_on_all_failures(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=500, text="Server Error")
        result = check_grid.api_request("https://example.com")
        assert result is None

    @mock.patch("check_grid.requests.get")
    def test_auth_error_no_retry(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=403, text="Forbidden")
        result = check_grid.api_request("https://example.com")
        assert result is None
        assert mock_get.call_count == 1

    @mock.patch("check_grid.requests.get")
    def test_invalid_json(self, mock_get):
        resp = mock.Mock(status_code=200, text="not json")
        resp.json.side_effect = ValueError("bad")
        mock_get.return_value = resp
        result = check_grid.api_request("https://example.com")
        assert result is None


# ---------------------------------------------------------------------------
# UK Carbon Intensity API tests
# ---------------------------------------------------------------------------

class TestUkCheckCarbonIntensity:
    @mock.patch("check_grid.api_request")
    def test_national_green(self, mock_api):
        mock_api.return_value = {
            "data": [{"from": "2026-03-10T00:00Z", "to": "2026-03-10T00:30Z",
                       "intensity": {"forecast": 100, "actual": 95, "index": "low"}}]
        }
        is_green, intensity = check_grid.uk_check_carbon_intensity("GB", 250)
        assert is_green is True
        assert intensity == 100

    @mock.patch("check_grid.api_request")
    def test_national_dirty(self, mock_api):
        mock_api.return_value = {
            "data": [{"intensity": {"forecast": 400, "actual": 410, "index": "high"}}]
        }
        is_green, intensity = check_grid.uk_check_carbon_intensity("GB", 250)
        assert is_green is False
        assert intensity == 400

    @mock.patch("check_grid.api_request")
    def test_regional_green(self, mock_api):
        mock_api.return_value = {
            "data": [{"data": [{"intensity": {"forecast": 50, "index": "very low"}}]}]
        }
        is_green, intensity = check_grid.uk_check_carbon_intensity("GB-16", 250)
        assert is_green is True
        assert intensity == 50

    @mock.patch("check_grid.api_request")
    def test_api_error(self, mock_api):
        mock_api.return_value = None
        is_green, intensity = check_grid.uk_check_carbon_intensity("GB", 250)
        assert is_green is None
        assert intensity is None

    @mock.patch("check_grid.api_request")
    def test_unknown_zone(self, mock_api):
        is_green, intensity = check_grid.uk_check_carbon_intensity("GB-99", 250)
        assert is_green is None
        assert intensity is None
        mock_api.assert_not_called()

    @mock.patch("check_grid.api_request")
    def test_malformed_response(self, mock_api):
        mock_api.return_value = {"data": [{}]}
        is_green, intensity = check_grid.uk_check_carbon_intensity("GB", 250)
        assert is_green is None
        assert intensity is None


class TestUkGetForecast:
    @mock.patch("check_grid.api_request")
    def test_finds_green_window(self, mock_api):
        mock_api.return_value = {
            "data": [
                {"from": "2026-03-10T00:00Z", "intensity": {"forecast": 300}},
                {"from": "2026-03-10T06:00Z", "intensity": {"forecast": 120}},
            ]
        }
        dt, intensity = check_grid.uk_get_forecast("GB", 200)
        assert dt == "2026-03-10T06:00Z"
        assert intensity == 120

    @mock.patch("check_grid.api_request")
    def test_no_green_window(self, mock_api):
        mock_api.return_value = {
            "data": [
                {"from": "2026-03-10T00:00Z", "intensity": {"forecast": 300}},
                {"from": "2026-03-10T06:00Z", "intensity": {"forecast": 350}},
            ]
        }
        dt, intensity = check_grid.uk_get_forecast("GB", 200)
        assert dt == "none_in_forecast"
        assert intensity is None

    @mock.patch("check_grid.api_request")
    def test_api_error(self, mock_api):
        mock_api.return_value = None
        dt, intensity = check_grid.uk_get_forecast("GB", 200)
        assert dt is None
        assert intensity is None


class TestUkGetHistoryTrend:
    @mock.patch("check_grid.api_request")
    def test_decreasing(self, mock_api):
        mock_api.return_value = {
            "data": [
                {"intensity": {"forecast": 400}}, {"intensity": {"forecast": 380}},
                {"intensity": {"forecast": 360}}, {"intensity": {"forecast": 300}},
                {"intensity": {"forecast": 280}}, {"intensity": {"forecast": 260}},
            ]
        }
        assert check_grid.uk_get_history_trend("GB") == "decreasing"

    @mock.patch("check_grid.api_request")
    def test_api_error(self, mock_api):
        mock_api.return_value = None
        assert check_grid.uk_get_history_trend("GB") is None


# ---------------------------------------------------------------------------
# EIA tests
# ---------------------------------------------------------------------------

class TestEiaFuelMixToIntensity:
    def test_all_gas(self):
        data = [{"fueltype": "NG", "value": 100}]
        assert check_grid._eia_fuel_mix_to_intensity(data) == 490

    def test_all_wind(self):
        data = [{"fueltype": "WND", "value": 100}]
        assert check_grid._eia_fuel_mix_to_intensity(data) == 0

    def test_mixed(self):
        data = [
            {"fueltype": "NG", "value": 50},   # 50 * 490 = 24500
            {"fueltype": "WND", "value": 50},   # 50 * 0 = 0
        ]
        # 24500 / 100 = 245
        assert check_grid._eia_fuel_mix_to_intensity(data) == 245

    def test_negative_values_ignored(self):
        data = [
            {"fueltype": "NG", "value": 100},
            {"fueltype": "SUN", "value": -10},  # Negative (consuming), ignored
        ]
        assert check_grid._eia_fuel_mix_to_intensity(data) == 490

    def test_none_values_ignored(self):
        data = [
            {"fueltype": "NG", "value": 100},
            {"fueltype": "SUN", "value": None},
        ]
        assert check_grid._eia_fuel_mix_to_intensity(data) == 490

    def test_empty_data(self):
        assert check_grid._eia_fuel_mix_to_intensity([]) is None

    def test_all_zero(self):
        data = [{"fueltype": "NG", "value": 0}]
        assert check_grid._eia_fuel_mix_to_intensity(data) is None


class TestEiaCheckCarbonIntensity:
    @mock.patch("check_grid.api_request")
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
        is_green, intensity = check_grid.eia_check_carbon_intensity("CISO", 250)
        assert is_green is True
        # (100*490) / (500+300+100) = 49000/900 = 54
        assert intensity == 54

    @mock.patch("check_grid.api_request")
    def test_dirty_grid(self, mock_api):
        mock_api.return_value = {
            "response": {
                "data": [
                    {"period": "2026-03-09T06", "respondent": "ERCO", "fueltype": "COL", "value": 500},
                    {"period": "2026-03-09T06", "respondent": "ERCO", "fueltype": "NG", "value": 500},
                ]
            }
        }
        is_green, intensity = check_grid.eia_check_carbon_intensity("ERCO", 250)
        assert is_green is False
        # (500*820 + 500*490) / 1000 = 655
        assert intensity == 655

    @mock.patch("check_grid.api_request")
    def test_api_error(self, mock_api):
        mock_api.return_value = None
        is_green, intensity = check_grid.eia_check_carbon_intensity("CISO", 250)
        assert is_green is None
        assert intensity is None

    @mock.patch("check_grid.api_request")
    def test_empty_data(self, mock_api):
        mock_api.return_value = {"response": {"data": []}}
        is_green, intensity = check_grid.eia_check_carbon_intensity("CISO", 250)
        assert is_green is None
        assert intensity is None

    @mock.patch("check_grid.api_request")
    def test_uses_demo_key_by_default(self, mock_api):
        mock_api.return_value = {"response": {"data": []}}
        check_grid.eia_check_carbon_intensity("CISO", 250)
        call_url = mock_api.call_args[0][0]
        assert "DEMO_KEY" in call_url

    @mock.patch("check_grid.api_request")
    def test_uses_custom_key(self, mock_api):
        mock_api.return_value = {"response": {"data": []}}
        check_grid.eia_check_carbon_intensity("CISO", 250, eia_api_key="my-key")
        call_url = mock_api.call_args[0][0]
        assert "my-key" in call_url
        assert "DEMO_KEY" not in call_url


class TestEiaGetHistoryTrend:
    @mock.patch("check_grid.api_request")
    def test_decreasing(self, mock_api):
        # 6 hours of data with varying fuel mixes to create decreasing intensity
        # Newer hours have more wind (clean), older have more gas (dirty)
        rows = []
        # API returns newest first; we need 6 periods
        # Period 06 (newest): mostly wind -> low intensity
        # Period 01 (oldest): mostly gas -> high intensity
        gas_amounts = [100, 150, 200, 300, 350, 400]  # newest to oldest
        wind_amounts = [400, 350, 300, 200, 150, 100]
        for i in range(6):
            period = f"2026-03-09T{6-i:02d}"
            rows.append({"period": period, "fueltype": "NG", "value": gas_amounts[i]})
            rows.append({"period": period, "fueltype": "WND", "value": wind_amounts[i]})

        mock_api.return_value = {"response": {"data": rows}}
        result = check_grid.eia_get_history_trend("CISO")
        assert result == "decreasing"

    @mock.patch("check_grid.api_request")
    def test_api_error(self, mock_api):
        mock_api.return_value = None
        assert check_grid.eia_get_history_trend("CISO") is None


# ---------------------------------------------------------------------------
# Provider-agnostic tests
# ---------------------------------------------------------------------------

class TestComputeTrend:
    def test_decreasing(self):
        points = [400, 380, 360, 300, 280, 260]
        assert check_grid._compute_trend(points) == "decreasing"

    def test_increasing(self):
        points = [100, 120, 130, 200, 250, 300]
        assert check_grid._compute_trend(points) == "increasing"

    def test_stable(self):
        points = [200, 200, 200, 200, 200, 200]
        assert check_grid._compute_trend(points) == "stable"

    def test_insufficient_data(self):
        assert check_grid._compute_trend([100, 200]) is None


class TestCheckMultipleZones:
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.detect_provider", return_value=check_grid.PROVIDER_EIA)
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
        zone, intensity, label = check_grid.check_multiple_zones(zones, "", 250)
        assert zone == "NYIS"
        assert intensity == 50
        assert label == "label-b"

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.detect_provider", return_value=check_grid.PROVIDER_EIA)
    def test_all_dirty(self, _mock_detect, mock_check):
        mock_check.side_effect = [(False, 400), (False, 500)]
        zones = [{"zone": "ERCO"}, {"zone": "PJM"}]
        zone, intensity, label = check_grid.check_multiple_zones(zones, "", 250)
        assert zone is None

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.detect_provider", return_value=check_grid.PROVIDER_EIA)
    def test_all_errors(self, _mock_detect, mock_check):
        mock_check.side_effect = [(None, None), (None, None)]
        zones = [{"zone": "CISO"}, {"zone": "ERCO"}]
        zone, intensity, label = check_grid.check_multiple_zones(zones, "", 250)
        assert zone is None


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

        check_grid.handle_dirty_grid("GB", "", 250, 400, enable_forecast=False)

        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["grid_clean"] == "false"
        assert output_calls["carbon_intensity"] == "400"
        assert output_calls["intensity_trend"] == "decreasing"
        assert output_calls["forecast_green_at"] == "2026-03-10T06:00Z"
        mock_forecast.assert_called_once()

    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.get_history_trend")
    @mock.patch("check_grid.set_output")
    def test_eia_no_forecast(self, mock_output, mock_trend, mock_forecast):
        """EIA zones don't have forecasts."""
        mock_trend.return_value = "increasing"
        mock_forecast.return_value = (None, None)

        check_grid.handle_dirty_grid("CISO", "", 250, 400, enable_forecast=True)

        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["grid_clean"] == "false"
        assert output_calls["intensity_trend"] == "increasing"
        assert "forecast_green_at" not in output_calls

    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.get_history_trend")
    @mock.patch("check_grid.set_output")
    def test_unknown_intensity(self, mock_output, mock_trend, mock_forecast):
        mock_trend.return_value = None
        mock_forecast.return_value = (None, None)

        check_grid.handle_dirty_grid("GB", "", 250, None, enable_forecast=False)

        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["carbon_intensity"] == "unknown"

    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.get_history_trend")
    @mock.patch("check_grid.set_output")
    def test_no_green_in_forecast(self, mock_output, mock_trend, mock_forecast):
        mock_trend.return_value = "stable"
        mock_forecast.return_value = ("none_in_forecast", None)

        check_grid.handle_dirty_grid("GB", "", 250, 400, enable_forecast=False)

        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["forecast_green_at"] == "none_in_forecast"
        assert "forecast_intensity" not in output_calls
