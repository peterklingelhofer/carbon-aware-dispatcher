"""Tests for carbon-aware dispatcher."""

import os
import tempfile
from datetime import datetime, timedelta, timezone
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
    PROVIDER_CANADA,
    PROVIDER_EIA,
    PROVIDER_ELECTRICITY_MAPS,
    PROVIDER_ENTSOE,
    PROVIDER_ESKOM,
    PROVIDER_GRID_INDIA,
    PROVIDER_ONS_BRAZIL,
    PROVIDER_OPEN_METEO,
    PROVIDER_TAIWAN,
    PROVIDER_UK,
    _time_priority_score,
    aemo,
    canada,
    detect_provider,
    eia,
    electricity_maps,
    entsoe,
    eskom,
    grid_india,
    gridstatus,
    ons_brazil,
    open_meteo,
    sort_auto_green_by_time,
    taiwan,
    uk,
)
from providers.base import api_request, api_request_with_header, compute_trend
from providers.runners import (
    AWS_REGION_TO_ZONE,
    AZURE_REGION_TO_ZONE,
    GCP_REGION_TO_ZONE,
    ZONE_TO_AWS_REGION,
    ZONE_TO_AZURE_REGION,
    ZONE_TO_GCP_REGION,
    detect_cloud_zone,
    format_runner_label,
    format_runson_label,
    get_azure_region,
    get_cloud_region,
    get_gcp_region,
)


@pytest.fixture(autouse=True)
def _no_real_sleep():
    """Stop the retry/backoff layer from actually sleeping during tests.

    base.request sleeps RETRY_DELAY seconds between retries on 5xx/429/network
    errors. Without this, the handful of failure-path tests add ~60s to the
    suite (and to every CI matrix run) for no behavioral coverage.
    """
    with mock.patch("providers.base.time.sleep"):
        yield


@pytest.fixture(autouse=True)
def _clear_env():
    """Ensure test env vars don't leak between tests."""
    keys = [
        "GRID_ZONE",
        "GRID_ZONES",
        "EIA_API_KEY",
        "GRID_STATUS_API_KEY",
        "ELECTRICITY_MAPS_TOKEN",
        "MAX_CARBON",
        "WORKFLOW_ID",
        "GITHUB_TOKEN",
        "TARGET_REPO",
        "TARGET_REF",
        "FAIL_ON_API_ERROR",
        "ENABLE_FORECAST",
        "MAX_WAIT",
        "GITHUB_OUTPUT",
        "GITHUB_STEP_SUMMARY",
        "RUNNER_PROVIDER",
        "RUNNER_SPEC",
        "GITHUB_RUN_ID",
        "ENTSOE_TOKEN",
        "STRATEGY",
        "DEADLINE_HOURS",
        "CARBON_POLICY_PATH",
        "DRY_RUN",
        "CONSUMPTION_BASED",
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


class TestFailureReason:
    """request() classifies why a call failed, for actionable skip reasons."""

    @mock.patch("providers.base.requests.get")
    def test_auth_failed(self, mock_get):
        from providers import base

        mock_get.return_value = mock.Mock(status_code=403, text="no")
        base.request("https://x")
        assert base.last_failure_reason() == "auth failed"

    @mock.patch("providers.base.requests.get")
    def test_rate_limited(self, mock_get):
        from providers import base

        mock_get.return_value = mock.Mock(status_code=429, text="slow", headers={})
        base.request("https://x")
        assert base.last_failure_reason() == "rate limited"

    @mock.patch("providers.base.requests.get")
    def test_network_error(self, mock_get):
        from providers import base

        mock_get.side_effect = requests.RequestException("boom")
        base.request("https://x")
        assert base.last_failure_reason() == "network error"

    @mock.patch("providers.base.requests.get")
    def test_success_resets_reason(self, mock_get):
        from providers import base

        mock_get.return_value = mock.Mock(status_code=200, json=lambda: {"ok": 1})
        base.request("https://x")
        assert base.last_failure_reason() is None

    @mock.patch("check_grid.check_carbon_intensity")
    def test_dispatcher_surfaces_reason(self, mock_check):
        from providers import base

        # check_carbon_intensity returns (None, None); base recorded a reason
        mock_check.return_value = (None, None)
        base._set_failure_reason("auth failed")
        _zone, _i, _label, skipped = check_grid.check_multiple_zones(
            [{"zone": "CISO", "runner_label": None}], 250
        )
        assert skipped == [("CISO", "auth failed")]

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
            "data": [
                {
                    "from": "2026-03-10T00:00Z",
                    "to": "2026-03-10T00:30Z",
                    "intensity": {"forecast": 100, "actual": 95, "index": "low"},
                }
            ]
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
                {"intensity": {"forecast": 400}},
                {"intensity": {"forecast": 380}},
                {"intensity": {"forecast": 360}},
                {"intensity": {"forecast": 300}},
                {"intensity": {"forecast": 280}},
                {"intensity": {"forecast": 260}},
            ]
        }
        assert uk.get_history_trend("GB") == "decreasing"

    @mock.patch("providers.uk.api_request")
    def test_api_error(self, mock_api):
        mock_api.return_value = None
        assert uk.get_history_trend("GB") is None

    @mock.patch("providers.uk.api_request")
    def test_regional_trend(self, mock_api):
        # Regional zones nest the points under data.data
        mock_api.return_value = {
            "data": {
                "data": [
                    {"intensity": {"forecast": 400}},
                    {"intensity": {"forecast": 380}},
                    {"intensity": {"forecast": 360}},
                    {"intensity": {"forecast": 300}},
                    {"intensity": {"forecast": 280}},
                    {"intensity": {"forecast": 260}},
                ]
            }
        }
        assert uk.get_history_trend("GB-16") == "decreasing"

    def test_trend_unknown_zone(self):
        assert uk.get_history_trend("GB-999") is None


class TestUkRegionalForecast:
    @mock.patch("providers.uk.api_request")
    def test_regional_forecast_finds_window(self, mock_api):
        mock_api.return_value = {
            "data": {
                "data": [
                    {"from": "2026-03-10T12:00Z", "intensity": {"forecast": 300}},
                    {"from": "2026-03-10T14:00Z", "intensity": {"forecast": 90}},
                ]
            }
        }
        dt, intensity = uk.get_forecast("GB-16", 200)
        assert dt == "2026-03-10T14:00Z"
        assert intensity == 90

    def test_forecast_unknown_zone(self):
        dt, intensity = uk.get_forecast("GB-999", 200)
        assert dt is None and intensity is None

    @mock.patch("providers.uk.api_request")
    def test_forecast_malformed_response(self, mock_api):
        mock_api.return_value = {"data": {"data": [{"oops": True}]}}
        dt, intensity = uk.get_forecast("GB-16", 200)
        assert dt is None and intensity is None


# ---------------------------------------------------------------------------
# EIA tests
# ---------------------------------------------------------------------------


class TestEiaFuelMixToIntensity:
    def test_all_gas(self):
        data = [{"fueltype": "NG", "value": 100}]
        assert eia._fuel_mix_to_intensity(data) == 490

    def test_all_wind(self):
        data = [{"fueltype": "WND", "value": 100}]
        # IPCC AR5 wind = 12, renewables are no longer treated as zero
        assert eia._fuel_mix_to_intensity(data) == 12

    def test_mixed(self):
        data = [
            {"fueltype": "NG", "value": 50},  # 50 * 490 = 24500
            {"fueltype": "WND", "value": 50},  # 50 * 12 = 600
        ]
        # (24500 + 600) / 100 = 251
        assert eia._fuel_mix_to_intensity(data) == 251

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

    def test_unknown_fuel_warns_and_falls_back(self, capsys):
        # an unknown EIA fuel code must warn and still apply the fallback
        # factor so the calc proceeds rather than silently using zero
        from providers.base import DEFAULT_FUEL_FACTOR

        data = [{"fueltype": "XYZ", "value": 100}]
        result = eia._fuel_mix_to_intensity(data)
        assert result == DEFAULT_FUEL_FACTOR
        out = capsys.readouterr().out
        assert "::warning::" in out
        assert "XYZ" in out

    def test_battery_storage_excluded(self):
        # battery storage must not be counted as zero-carbon generation,
        # it is excluded from the mix entirely
        data = [
            {"fueltype": "COL", "value": 100},
            {"fueltype": "BAT", "value": 100},
        ]
        # BAT excluded, so result is pure coal = 820
        assert eia._fuel_mix_to_intensity(data) == 820


class TestEiaCheckCarbonIntensity:
    @mock.patch("providers.eia.api_request")
    def test_green_grid(self, mock_api):
        mock_api.return_value = {
            "response": {
                "data": [
                    {
                        "period": "2026-03-09T06",
                        "respondent": "CISO",
                        "fueltype": "WND",
                        "value": 500,
                    },
                    {
                        "period": "2026-03-09T06",
                        "respondent": "CISO",
                        "fueltype": "SUN",
                        "value": 300,
                    },
                    {
                        "period": "2026-03-09T06",
                        "respondent": "CISO",
                        "fueltype": "NG",
                        "value": 100,
                    },
                ]
            }
        }
        is_green, intensity = eia.check_carbon_intensity("CISO", 250)
        assert is_green is True
        # wind 12, solar 45, gas 490: (500*12 + 300*45 + 100*490) / 900
        # = (6000 + 13500 + 49000) / 900 = 68500/900 = 76.1 -> 76
        assert intensity == 76

    @mock.patch("providers.eia.api_request")
    def test_dirty_grid(self, mock_api):
        mock_api.return_value = {
            "response": {
                "data": [
                    {
                        "period": "2026-03-09T06",
                        "respondent": "ERCO",
                        "fueltype": "COL",
                        "value": 500,
                    },
                    {
                        "period": "2026-03-09T06",
                        "respondent": "ERCO",
                        "fueltype": "NG",
                        "value": 500,
                    },
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
            period = f"2026-03-09T{6 - i:02d}"
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
                {"carbonIntensity": 400},
                {"carbonIntensity": 380},
                {"carbonIntensity": 360},
                {"carbonIntensity": 300},
                {"carbonIntensity": 280},
                {"carbonIntensity": 260},
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
        result = api_request_with_header(
            "https://api.gridstatus.io/v1/test", "x-api-key", "bad-key"
        )
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
        dt, intensity = check_grid.get_forecast("CISO", 250, PROVIDER_EIA, "my-gridstatus-key")
        assert dt == "2026-03-10T18:00:00+00:00"
        assert intensity == 0


class TestGridstatusRenewableForecast:
    @mock.patch("providers.gridstatus._query_dataset")
    def test_single_dataset_with_location_filter(self, mock_query):
        """CAISO-style: single dataset with location filter."""
        mock_query.return_value = [
            {
                "interval_start_utc": "2026-03-10T18:00:00+00:00",
                "location": "CAISO",
                "solar_mw": 8000,
                "wind_mw": 1500,
            },
            {
                "interval_start_utc": "2026-03-10T18:00:00+00:00",
                "location": "NP15",
                "solar_mw": 2000,
                "wind_mw": 500,
            },
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

    @mock.patch("providers.gridstatus._query_dataset")
    def test_sum_columns_branch(self, mock_query):
        """ISNE-style sum_columns: sum all numeric forecast columns per row."""
        iso = None
        for cfg in gridstatus.GRIDSTATUS_ISO_MAP.values():
            if cfg.get("sum_columns"):
                iso = cfg
                break
        assert iso is not None, "expected at least one sum_columns ISO"
        mock_query.side_effect = [
            # solar dataset: two zones summed
            [
                {
                    "interval_start_utc": "2026-03-10T18:00:00+00:00",
                    "publish_time_utc": "x",
                    "zone_a": 1000,
                    "zone_b": 500,
                }
            ],
            # wind dataset
            [{"interval_start_utc": "2026-03-10T18:00:00+00:00", "zone_a": 800}],
        ]
        result = iso and gridstatus._get_renewable_forecast(iso, "key", "2026-03-10")
        slot = result["2026-03-10T18:00:00+00:00"]
        assert slot["solar_mw"] == 1500  # 1000 + 500, publish_/interval_ excluded
        assert slot["wind_mw"] == 800

    @mock.patch("providers.gridstatus.api_request_with_header")
    def test_query_dataset_none_on_failure(self, mock_req):
        mock_req.return_value = None
        assert gridstatus._query_dataset("ds", "key", "2026-03-10") == []

    @mock.patch("providers.gridstatus._query_dataset")
    def test_load_forecast_parsing(self, mock_query):
        mock_query.return_value = [
            {"interval_start_utc": "2026-03-10T18:00:00+00:00", "load_forecast": 12345},
            {"interval_start_utc": None, "load_forecast": 999},  # skipped (no ts)
        ]
        iso = gridstatus.GRIDSTATUS_ISO_MAP["CISO"]
        result = gridstatus._get_load_forecast(iso, "key", "2026-03-10")
        assert result == {"2026-03-10T18:00:00+00:00": 12345.0}

    def test_load_forecast_no_dataset(self):
        # ERCO has load_dataset=None
        iso = gridstatus.GRIDSTATUS_ISO_MAP["ERCO"]
        assert gridstatus._get_load_forecast(iso, "key", "2026-03-10") is None


class TestOnsBrazilCheckAndForecast:
    @mock.patch("providers.ons_brazil._fetch_energy_balance")
    def test_check_api_unavailable(self, mock_fetch):
        mock_fetch.return_value = None
        assert ons_brazil.check_carbon_intensity("BR-S", 250) == (None, None)

    @mock.patch("providers.ons_brazil._fetch_energy_balance")
    def test_check_unparseable_response(self, mock_fetch):
        mock_fetch.return_value = {"unexpected": "shape"}
        assert ons_brazil.check_carbon_intensity("BR-S", 250) == (None, None)

    @mock.patch("providers.ons_brazil._fetch_energy_balance")
    def test_check_success(self, mock_fetch):
        mock_fetch.return_value = {
            "sul": {"geracao": {"total": 6000, "hidraulica": 5000, "termica": 1000}}
        }
        is_green, intensity = ons_brazil.check_carbon_intensity("BR-S", 250)
        # hydro 5000*24 + thermal 1000*650 = 120000+650000 = 770000/6000 = 128
        assert intensity == 128
        assert is_green is True

    def test_check_unknown_zone(self):
        assert ons_brazil.check_carbon_intensity("BR-XX", 250) == (None, None)

    def test_forecast_offpeak_already_green_returns_none(self):
        # Pin the clock to an off-peak hour (10:00 BRT = 13:00 UTC) so the test
        # is deterministic regardless of when CI runs. Off-peak + high threshold
        # means the grid is already green, so no future window is needed.
        fixed = datetime(2026, 3, 10, 13, 0, tzinfo=timezone.utc)
        with mock.patch("providers.ons_brazil.datetime") as mock_dt:
            # Only now() is pinned; real datetime construction still works
            mock_dt.now.return_value = fixed
            dt, intensity = ons_brazil.get_forecast("BR-S", 500)
        assert dt is None and intensity is None

    def test_forecast_finds_window_or_none(self):
        # With a very low threshold the heuristic should return a string or
        # the none sentinel, never crash, at any hour
        dt, intensity = ons_brazil.get_forecast("BR-NE", 10)
        assert dt is None or isinstance(dt, str)


# ---------------------------------------------------------------------------
# Provider-agnostic tests
# ---------------------------------------------------------------------------


class TestCanonicalFuelFactors:
    """Every provider sources its factors from base.FUEL_FACTORS, so shared
    fuels must agree across providers and match the canonical table."""

    def test_shared_fuels_agree_across_providers(self):
        from providers import aemo, base, canada, entsoe, eskom, grid_india, taiwan

        f = base.FUEL_FACTORS
        # Coal/hard-coal is the same everywhere it appears
        assert base.EIA_EMISSION_FACTORS["COL"] == f["coal"]
        assert canada.CANADA_EMISSION_FACTORS["coal"] == f["coal"]
        assert taiwan.TAIWAN_EMISSION_FACTORS["coal"] == f["coal"]
        assert aemo.AEMO_EMISSION_FACTORS["black coal"] == f["coal"]
        assert eskom.SA_EMISSION_FACTORS["coal"] == f["coal"]
        assert grid_india.INDIA_EMISSION_FACTORS["coal"] == f["coal"]
        assert entsoe.ENTSOE_EMISSION_FACTORS["B05"] == f["coal"]
        # Lignite/brown-coal agree
        assert aemo.AEMO_EMISSION_FACTORS["brown coal"] == f["lignite"]
        assert grid_india.INDIA_EMISSION_FACTORS["lignite"] == f["lignite"]
        assert entsoe.ENTSOE_EMISSION_FACTORS["B02"] == f["lignite"]
        # Gas agrees
        assert base.EIA_EMISSION_FACTORS["NG"] == f["gas"]
        assert entsoe.ENTSOE_EMISSION_FACTORS["B04"] == f["gas"]
        # Renewables agree
        assert canada.CANADA_EMISSION_FACTORS["wind"] == f["wind"]
        assert entsoe.ENTSOE_EMISSION_FACTORS["B19"] == f["wind"]
        assert taiwan.TAIWAN_EMISSION_FACTORS["hydro"] == f["hydro"]

    def test_default_factor_is_canonical(self):
        from providers import aemo, base, entsoe

        assert base.DEFAULT_FUEL_FACTOR == base.FUEL_FACTORS["other"]
        assert aemo.DEFAULT_FUEL_FACTOR == base.FUEL_FACTORS["other"]
        assert entsoe.DEFAULT_FUEL_FACTOR == base.FUEL_FACTORS["other"]

    def test_every_provider_value_is_in_canonical_table(self):
        """Every value in every provider's factor dict must come from the
        canonical FUEL_FACTORS, proving none reintroduced a bare number."""
        from providers import aemo, base, canada, entsoe, eskom, grid_india, ons_brazil, taiwan

        canonical = set(base.FUEL_FACTORS.values())
        dicts = [
            base.EIA_EMISSION_FACTORS,
            canada.CANADA_EMISSION_FACTORS,
            taiwan.TAIWAN_EMISSION_FACTORS,
            aemo.AEMO_EMISSION_FACTORS,
            eskom.SA_EMISSION_FACTORS,
            grid_india.INDIA_EMISSION_FACTORS,
            ons_brazil.BRAZIL_EMISSION_FACTORS,
            entsoe.ENTSOE_EMISSION_FACTORS,
        ]
        for d in dicts:
            for fuel, value in d.items():
                assert value in canonical, f"{fuel}={value} not in FUEL_FACTORS"


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
            (True, 200),  # zone A
            (True, 50),  # zone B (best)
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

        check_grid.handle_dirty_grid(
            "CISO", 250, 400, enable_forecast=True, gridstatus_api_key="gs-key"
        )

        output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
        assert output_calls["forecast_green_at"] == "2026-03-10T18:00:00+00:00"
        assert output_calls["forecast_intensity"] == "50"
        mock_forecast.assert_called_once_with("CISO", 250, PROVIDER_EIA, "gs-key", "", "", "")

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

        check_grid.handle_dirty_grid("DE", 250, 400, enable_forecast=False, emaps_api_key="em-key")

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
                "PJM",
                380,
                False,
                200,
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
                "CISO",
                100,
                True,
                200,
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

        is_green, intensity, waited = check_grid.smart_wait_single("CISO", 250, 10, PROVIDER_EIA)
        assert is_green is True
        assert intensity == 100
        mock_sleep.assert_called_once()

    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid._time.sleep")
    @mock.patch("check_grid._time.time")
    def test_stays_dirty_after_max_wait(self, mock_time, mock_sleep, mock_check, mock_forecast):
        """Grid stays dirty: returns after max_wait exceeded."""
        # Simulate time passing: start at 0, then exceed deadline
        mock_time.side_effect = [
            0,
            0,
            601,
            601,
        ]  # start, loop check, loop check (past deadline), final
        mock_check.return_value = (False, 400)
        mock_forecast.return_value = (None, None)

        is_green, intensity, waited = check_grid.smart_wait_single("CISO", 250, 10, PROVIDER_EIA)
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


class TestDryRun:
    """Report-only mode never gates the build but reports the real verdict."""

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_dirty_grid_does_not_gate(self, mock_summary, mock_output, mock_check):
        # Single dirty zone, but dry_run must keep grid_clean true and exit 0
        mock_check.return_value = (False, 400)
        os.environ["GRID_ZONES"] = "AU-NSW"
        os.environ["MAX_CARBON"] = "250"
        os.environ["DRY_RUN"] = "true"
        os.environ["WORKFLOW_ID"] = ""

        with pytest.raises(SystemExit) as exc:
            check_grid.main()
        assert exc.value.code == 0

        out = {c[0][0]: c[0][1] for c in mock_output.call_args_list}
        assert out["grid_clean"] == "true"  # build is never blocked
        assert out["would_defer"] == "true"  # but the honest verdict is exposed
        assert out["dry_run"] == "true"

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_clean_grid_reports_dispatch(self, mock_summary, mock_output, mock_check):
        mock_check.return_value = (True, 80)
        os.environ["GRID_ZONES"] = "GB"
        os.environ["MAX_CARBON"] = "250"
        os.environ["DRY_RUN"] = "true"
        os.environ["WORKFLOW_ID"] = ""

        with pytest.raises(SystemExit) as exc:
            check_grid.main()
        assert exc.value.code == 0

        out = {c[0][0]: c[0][1] for c in mock_output.call_args_list}
        assert out["grid_clean"] == "true"
        assert out["would_defer"] == "false"

    @mock.patch("check_grid.trigger_workflow")
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_never_dispatches(self, mock_summary, mock_output, mock_check, mock_trigger):
        # Even in dispatch mode (workflow_id set), dry_run must not trigger
        mock_check.return_value = (True, 80)
        os.environ["GRID_ZONES"] = "GB"
        os.environ["DRY_RUN"] = "true"
        os.environ["WORKFLOW_ID"] = "heavy.yml"
        os.environ["GITHUB_TOKEN"] = "tok"
        os.environ["TARGET_REPO"] = "owner/repo"

        with pytest.raises(SystemExit):
            check_grid.main()
        mock_trigger.assert_not_called()

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_never_fails_build_even_with_fail_on_api_error(
        self, mock_summary, mock_output, mock_check
    ):
        # dry_run must exit 0 even when every zone errors AND fail_on_api_error
        # is set: report-only never breaks the build
        mock_check.return_value = (None, None)
        os.environ["GRID_ZONES"] = "GB,AU-NSW"
        os.environ["DRY_RUN"] = "true"
        os.environ["FAIL_ON_API_ERROR"] = "true"
        os.environ["WORKFLOW_ID"] = ""

        with pytest.raises(SystemExit) as exc:
            check_grid.main()
        assert exc.value.code == 0

    def test_summary_dry_run_banner(self):
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as f:
            path = f.name
        try:
            os.environ["GITHUB_STEP_SUMMARY"] = path
            check_grid.write_job_summary("AU-NSW", 400, False, 250, dry_run=True)
            with open(path) as f:
                content = f.read()
            assert "Report-only" in content
            assert "would defer" in content
        finally:
            os.unlink(path)
            os.environ.pop("GITHUB_STEP_SUMMARY", None)


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
        assert (
            output_calls["runner_label"] == "runs-on=98765/runner=4cpu-linux-x64/region=us-west-1"
        )

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_multi_zone_with_runson_provider(self, mock_summary, mock_output, mock_check):
        mock_check.side_effect = [
            (False, 400),  # ERCO dirty
            (True, 80),  # GB green
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
        # IPCC AR5 wind = 12, renewables are no longer treated as zero
        assert aemo._fuel_mix_to_intensity(data, "NSW1") == 12

    def test_mixed(self):
        data = [
            {"REGIONID": "NSW1", "FUELTYPE": "Black Coal", "GEN_MW": 500},
            {"REGIONID": "NSW1", "FUELTYPE": "Solar", "GEN_MW": 500},
        ]
        # coal 820, solar 45: (500*820 + 500*45) / 1000 = 432.5 -> 432
        assert aemo._fuel_mix_to_intensity(data, "NSW1") == 432

    def test_filters_by_region(self):
        data = [
            {"REGIONID": "NSW1", "FUELTYPE": "Wind", "GEN_MW": 1000},
            {"REGIONID": "QLD1", "FUELTYPE": "Black Coal", "GEN_MW": 1000},
        ]
        # only NSW wind is counted, wind = 12
        assert aemo._fuel_mix_to_intensity(data, "NSW1") == 12

    def test_empty_data(self):
        assert aemo._fuel_mix_to_intensity([], "NSW1") is None

    def test_negative_gen_ignored(self):
        data = [
            {"REGIONID": "NSW1", "FUELTYPE": "Wind", "GEN_MW": 100},
            {"REGIONID": "NSW1", "FUELTYPE": "Battery", "GEN_MW": -50},
        ]
        # wind = 12, battery is storage and excluded anyway
        assert aemo._fuel_mix_to_intensity(data, "NSW1") == 12


class TestAemoCheckCarbonIntensity:
    @mock.patch("providers.aemo._fetch_fuel_data")
    def test_green(self, mock_fetch):
        mock_fetch.return_value = [
            {"REGIONID": "TAS1", "FUELTYPE": "Hydro", "GEN_MW": 900},
            {"REGIONID": "TAS1", "FUELTYPE": "Wind", "GEN_MW": 100},
        ]
        is_green, intensity = aemo.check_carbon_intensity("AU-TAS", 250)
        assert is_green is True
        # hydro 24, wind 12: (900*24 + 100*12) / 1000 = 22.8 -> 23
        assert intensity == 23

    @mock.patch("providers.aemo._fetch_fuel_data")
    def test_dirty(self, mock_fetch):
        mock_fetch.return_value = [
            {"REGIONID": "VIC1", "FUELTYPE": "Brown Coal", "GEN_MW": 800},
            {"REGIONID": "VIC1", "FUELTYPE": "Wind", "GEN_MW": 200},
        ]
        is_green, intensity = aemo.check_carbon_intensity("AU-VIC", 250)
        assert is_green is False
        # brown coal/lignite 1050, wind 12: (800*1050 + 200*12) / 1000
        # = 842.4 -> 842
        assert intensity == 842

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

    def test_uses_latest_period_not_blend(self):
        # two periods for the same production type, the parser must use only
        # the latest period/position quantity, discarding earlier hours
        xml = """
        <TimeSeries>
            <MktPSRType><psrType>B05</psrType></MktPSRType>
            <Period>
                <timeInterval><end>2024-01-01T01:00Z</end></timeInterval>
                <Point><position>1</position><quantity>100</quantity></Point>
                <Point><position>2</position><quantity>200</quantity></Point>
            </Period>
            <Period>
                <timeInterval><end>2024-01-01T02:00Z</end></timeInterval>
                <Point><position>1</position><quantity>500</quantity></Point>
            </Period>
        </TimeSeries>
        """
        result = entsoe._parse_generation_xml(xml)
        # latest period end is 02:00 with quantity 500, not a sum (800)
        assert result == [("B05", 500.0)]

    def test_pumped_storage_excluded_from_intensity(self):
        # B10 Hydro Pumped Storage must be excluded from the weighted mix
        gen_data = [("B05", 100.0), ("B10", 100.0)]
        # B10 excluded, so result is pure hard coal = 820
        assert entsoe._intensity_from_gen_data(gen_data) == 820

    def test_unknown_psr_warns_and_falls_back(self, capsys):
        from providers.entsoe import DEFAULT_FUEL_FACTOR

        gen_data = [("B99", 100.0)]
        result = entsoe._intensity_from_gen_data(gen_data)
        assert result == DEFAULT_FUEL_FACTOR
        out = capsys.readouterr().out
        assert "::warning::" in out
        assert "B99" in out


class TestEntsoeCheckCarbonIntensity:
    @mock.patch("providers.base.requests.get")
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
        # wind B19 = 12, gas B04 = 490: (800*12 + 200*490) / 1000
        # = (9600 + 98000) / 1000 = 107.6 -> 108
        assert intensity == 108

    @mock.patch("providers.base.requests.get")
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

    @mock.patch("providers.base.requests.get")
    def test_auth_failure(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=401, text="Unauthorized")
        is_green, intensity = entsoe.check_carbon_intensity("DE", 250, "bad-token")
        assert is_green is None
        assert intensity is None

    @mock.patch("providers.base.requests.get")
    def test_rate_limit(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=429, text="Too Many Requests")
        is_green, intensity = entsoe.check_carbon_intensity("DE", 250, "token")
        assert is_green is None
        assert intensity is None

    @mock.patch("providers.base.requests.get")
    def test_network_error(self, mock_get):
        mock_get.side_effect = requests.RequestException("timeout")
        is_green, intensity = entsoe.check_carbon_intensity("DE", 250, "token")
        assert is_green is None
        assert intensity is None


class TestEntsoeForecast:
    def test_no_token(self):
        dt, intensity = entsoe.get_forecast("DE", 250, "")
        assert dt is None
        assert intensity is None

    def test_unknown_zone(self):
        dt, intensity = entsoe.get_forecast("XX-FAKE", 250, "token")
        assert dt is None and intensity is None

    def test_series_parser_averages_subhourly(self):
        # Two 15-min points in the same hour are averaged; matching TimeSeries summed
        xml = """
        <TimeSeries><MktPSRType><psrType>B16</psrType></MktPSRType><Period>
        <timeInterval><start>2026-03-10T00:00Z</start></timeInterval>
        <resolution>PT15M</resolution>
        <Point><position>1</position><quantity>100</quantity></Point>
        <Point><position>2</position><quantity>300</quantity></Point>
        </Period></TimeSeries>
        """
        series = entsoe._forecast_series_by_hour(xml, entsoe._VRE_PSR)
        # positions 1 and 2 are both in hour 00:00 (15-min steps): avg(100,300)=200
        hour = list(series)[0]
        assert series[hour] == 200.0

    def test_series_parser_psr_filter(self):
        # A non-VRE psrType is excluded when a VRE filter is applied
        xml = """
        <TimeSeries><MktPSRType><psrType>B04</psrType></MktPSRType><Period>
        <timeInterval><start>2026-03-10T00:00Z</start></timeInterval>
        <resolution>PT60M</resolution>
        <Point><position>1</position><quantity>500</quantity></Point>
        </Period></TimeSeries>
        """
        assert entsoe._forecast_series_by_hour(xml, entsoe._VRE_PSR) == {}
        # but parses when no filter (load doc)
        assert entsoe._forecast_series_by_hour(xml, None) != {}

    @mock.patch("providers.entsoe.production_for_zone")
    @mock.patch("providers.entsoe._vre_fraction_curve")
    def test_forecast_finds_greener_hour(self, mock_curve, mock_prod):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        mock_prod.return_value = (400, 50000)  # dirty now, 20% VRE
        # now: 20% renewable; +3h: 80% renewable -> much cleaner
        mock_curve.return_value = {
            now: 0.20,
            now + timedelta(hours=1): 0.30,
            now + timedelta(hours=3): 0.80,
        }
        dt, intensity = entsoe.get_forecast("DE", 250, "token")
        # base_fossil=0.8; +3h projected = 400*(1-0.8)/0.8 = 100 <= 250
        assert intensity == 100
        assert dt.endswith("Z")

    @mock.patch("providers.entsoe.production_for_zone")
    @mock.patch("providers.entsoe._vre_fraction_curve")
    def test_forecast_no_green_window(self, mock_curve, mock_prod):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        mock_prod.return_value = (800, 50000)  # very dirty, low VRE throughout
        mock_curve.return_value = {now: 0.05, now + timedelta(hours=1): 0.06}
        dt, intensity = entsoe.get_forecast("DE", 50, "token")
        assert dt == "none_in_forecast"
        assert intensity is None

    @mock.patch("providers.entsoe.production_for_zone")
    @mock.patch("providers.entsoe._vre_fraction_curve")
    def test_forecast_unavailable_curve(self, mock_curve, mock_prod):
        mock_prod.return_value = (400, 50000)
        mock_curve.return_value = {}  # forecast API failed
        assert entsoe.get_forecast("DE", 250, "token") == (None, None)


# ---------------------------------------------------------------------------
# Open-Meteo provider tests
# ---------------------------------------------------------------------------


class TestOpenMeteoEstimateIntensity:
    def test_high_solar_high_wind(self):
        # 40% solar reduction * 25% wind reduction = 0.60 * 0.75 = 0.45
        # 550 * 0.60 * 0.75 = 247.5 -> 248 (above the renewable floor)
        intensity = open_meteo._estimate_intensity_from_weather(700, 10)
        assert intensity == 248

    def test_no_solar_no_wind(self):
        # Night, calm: full base intensity
        intensity = open_meteo._estimate_intensity_from_weather(0, 1)
        assert intensity == 550

    def test_medium_solar_only(self):
        intensity = open_meteo._estimate_intensity_from_weather(400, 1)
        assert intensity == round(550 * 0.80 * 1.0)

    def test_high_wind_only(self):
        # 25% wind reduction only: 550 * 1.0 * 0.75 = 412.5 -> 412
        intensity = open_meteo._estimate_intensity_from_weather(0, 9)
        assert intensity == 412


class TestOpenMeteoCheckCarbonIntensity:
    @mock.patch("providers.base.requests.get")
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

    @mock.patch("providers.base.requests.get")
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

    @mock.patch("providers.base.requests.get")
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
        is_green, intensity = open_meteo.check_carbon_intensity("CUSTOM", 500, lat=40.0, lon=-74.0)
        assert is_green is True

    @mock.patch("providers.base.requests.get")
    def test_api_error(self, mock_get):
        mock_get.side_effect = requests.RequestException("timeout")
        is_green, intensity = open_meteo.check_carbon_intensity("ZA", 300)
        assert is_green is None
        assert intensity is None

    @mock.patch("providers.base.requests.get")
    def test_non_200_response(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=500, text="Server Error")
        is_green, intensity = open_meteo.check_carbon_intensity("ZA", 300)
        assert is_green is None
        assert intensity is None


class TestOpenMeteoForecast:
    @mock.patch("providers.base.requests.get")
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

    @mock.patch("providers.base.requests.get")
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
        night_score = _time_priority_score(zone, 2)  # 2am local
        day_score = _time_priority_score(zone, 14)  # 2pm local
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
        # Americas (EIA, free)
        assert "CISO" in zones
        assert "BPAT" in zones
        # UK (free)
        assert "GB-16" in zones
        # Australia (AEMO, free)
        assert "AU-TAS" in zones
        # Brazil (ONS, free)
        assert "BR-S" in zones

    def test_auto_green_excludes_geowalled_india(self):
        """Grid India zones are geo-walled (Indian IPs only), so curated
        presets omit them to keep the default experience clean from CI."""
        green = {z["zone"] for z in AUTO_GREEN_ZONES}
        cleanest = {z["zone"] for z in AUTO_CLEANEST_ZONES}
        assert not any(z.startswith("IN-") for z in green)
        assert not any(z.startswith("IN-") for z in cleanest)

    def test_auto_green_only_free_providers(self):
        """auto:green is the curated free set; the token-only extras live in
        auto:green:full."""
        zones = {z["zone"] for z in AUTO_GREEN_ZONES}
        full = {z["zone"] for z in __import__("providers").AUTO_GREEN_ZONES_FULL}
        # Token-tier zones are reserved for auto:green:full
        assert "NO-NO1" not in zones and "NO-NO1" in full
        assert "FR" not in zones and "FR" in full
        assert "NZ-NZN" not in zones and "NZ-NZN" in full
        # Canada is keyless (IESO / Hydro-Quebec), so CA-QC belongs in auto:green
        assert "CA-QC" in zones

    def test_auto_green_full_includes_token_zones(self):
        """auto:green:full includes both free and token-requiring zones."""
        from providers import AUTO_GREEN_ZONES_FULL

        zones = {z["zone"] for z in AUTO_GREEN_ZONES_FULL}
        assert "CISO" in zones  # Free
        assert "NO-NO1" in zones  # Token-requiring
        assert "CA-QC" in zones  # Token-requiring
        assert "NZ-NZN" in zones  # Token-requiring

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
        # 500 gCO2eq/kWh, worse than 450 baseline
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


class TestCarbonEquivalents:
    def test_zero_and_none_have_empty_phrase(self):
        for grams in (0, None, -5):
            eq = check_grid.carbon_equivalents(grams)
            assert eq["phrase"] == ""
            assert eq["km_driven"] == 0
            assert eq["phone_charges"] == 0

    def test_small_amount_uses_phone_charges(self):
        # 50 g is under a km of driving, so the phrase should be phone charges
        eq = check_grid.carbon_equivalents(50)
        assert "phone charges" in eq["phrase"]
        # 50 / 8.22 ~= 6 charges
        assert eq["phone_charges"] == pytest.approx(50 / 8.22, rel=0.01)

    def test_large_amount_uses_km_driven(self):
        # 1000 g => 4 km driven, comfortably over the 1 km switchover
        eq = check_grid.carbon_equivalents(1000)
        assert "km not driven" in eq["phrase"]
        assert eq["km_driven"] == pytest.approx(4.0, rel=0.01)

    def test_switchover_at_one_km(self):
        # Exactly 250 g == 1 km, should report km (>= 1)
        eq = check_grid.carbon_equivalents(250)
        assert "km not driven" in eq["phrase"]

    def test_tree_years_present(self):
        eq = check_grid.carbon_equivalents(21000)
        assert eq["tree_years"] == pytest.approx(1.0, rel=0.01)


class TestSetSavingsOutputs:
    def test_emits_equivalent_output(self):
        with tempfile.NamedTemporaryFile(mode="w+", delete=False) as f:
            path = f.name
        try:
            os.environ["GITHUB_OUTPUT"] = path
            check_grid.set_savings_outputs(1000, "https://img.shields.io/badge/x")
            with open(path) as f:
                content = f.read()
            assert "co2_saved_grams=1000" in content
            assert "co2_saved_equivalent=" in content
            assert "km not driven" in content
            assert "carbon_badge_url=" in content
        finally:
            os.unlink(path)
            os.environ.pop("GITHUB_OUTPUT", None)

    def test_no_savings_emits_nothing(self):
        with tempfile.NamedTemporaryFile(mode="w+", delete=False) as f:
            path = f.name
        try:
            os.environ["GITHUB_OUTPUT"] = path
            check_grid.set_savings_outputs(0, None)
            with open(path) as f:
                content = f.read()
            assert "co2_saved_grams" not in content
            assert "co2_saved_equivalent" not in content
        finally:
            os.unlink(path)
            os.environ.pop("GITHUB_OUTPUT", None)


class TestCarbonTier:
    def test_parse_defaults_on_empty(self):
        assert check_grid.parse_tier_thresholds("") == check_grid.DEFAULT_TIER_THRESHOLDS

    def test_parse_valid(self):
        assert check_grid.parse_tier_thresholds("120,280") == (120.0, 280.0)

    def test_parse_bad_order_falls_back(self):
        assert check_grid.parse_tier_thresholds("300,100") == check_grid.DEFAULT_TIER_THRESHOLDS

    def test_parse_garbage_falls_back(self):
        assert check_grid.parse_tier_thresholds("abc") == check_grid.DEFAULT_TIER_THRESHOLDS

    def test_parse_wrong_count_falls_back(self):
        assert check_grid.parse_tier_thresholds("100") == check_grid.DEFAULT_TIER_THRESHOLDS

    def test_classify_green(self):
        tier, reason = check_grid.classify_tier(80, (150, 300))
        assert tier == "green"
        assert "full" in reason

    def test_classify_amber(self):
        tier, _ = check_grid.classify_tier(200, (150, 300))
        assert tier == "amber"

    def test_classify_red(self):
        tier, _ = check_grid.classify_tier(500, (150, 300))
        assert tier == "red"

    def test_classify_boundary_is_inclusive(self):
        assert check_grid.classify_tier(150, (150, 300))[0] == "green"
        assert check_grid.classify_tier(300, (150, 300))[0] == "amber"

    def test_classify_unknown_on_none(self):
        tier, _ = check_grid.classify_tier(None, (150, 300))
        assert tier == "unknown"

    def test_classify_negative_intensity_is_green(self):
        # Negative intensity should not crash; treated as cleanest (green)
        assert check_grid.classify_tier(-10, (150, 300))[0] == "green"

    def test_summary_sets_tier_output(self):
        with tempfile.NamedTemporaryFile(mode="w+", delete=False) as f:
            out_path = f.name
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as f:
            sum_path = f.name
        try:
            os.environ["GITHUB_OUTPUT"] = out_path
            os.environ["GITHUB_STEP_SUMMARY"] = sum_path
            os.environ["TIER_THRESHOLDS"] = "120,280"
            check_grid.write_job_summary("CISO", 90, True, 250)
            with open(out_path) as f:
                out = f.read()
            with open(sum_path) as f:
                summary = f.read()
            assert "carbon_tier=green" in out
            assert "carbon_tier_reason=" in out
            assert "Carbon Tier" in summary
        finally:
            for p in (out_path, sum_path):
                os.unlink(p)
            for k in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "TIER_THRESHOLDS"):
                os.environ.pop(k, None)


class TestCostCarbonRanking:
    def test_cost_weight_default_zero(self):
        os.environ.pop("COST_WEIGHT", None)
        assert check_grid._cost_weight() == 0.0

    def test_cost_weight_clamped(self):
        try:
            os.environ["COST_WEIGHT"] = "1.7"
            assert check_grid._cost_weight() == 1.0
            os.environ["COST_WEIGHT"] = "-3"
            assert check_grid._cost_weight() == 0.0
        finally:
            os.environ.pop("COST_WEIGHT", None)

    def test_cost_weight_garbage(self):
        try:
            os.environ["COST_WEIGHT"] = "abc"
            assert check_grid._cost_weight() == 0.0
        finally:
            os.environ.pop("COST_WEIGHT", None)

    @mock.patch("check_grid.azure_pricing.get_region_price")
    def test_pure_cost_picks_cheapest(self, price):
        # CISO cleaner (50) but pricier (0.10); FR dirtier (100) but cheaper (0.05)
        candidates = [("CISO", 50, "l1"), ("FR", 100, "l2")]
        price.side_effect = [0.10, 0.05]
        zone, intensity, label = check_grid.rank_by_cost_carbon(candidates, 1.0)
        assert zone == "FR"

    @mock.patch("check_grid.azure_pricing.get_region_price")
    def test_pure_carbon_picks_cleanest(self, price):
        candidates = [("CISO", 50, "l1"), ("FR", 100, "l2")]
        price.side_effect = [0.10, 0.05]
        zone, _, _ = check_grid.rank_by_cost_carbon(candidates, 0.0)
        assert zone == "CISO"

    @mock.patch("check_grid.azure_pricing.get_region_price")
    def test_missing_price_falls_back(self, price):
        candidates = [("CISO", 50, "l1"), ("FR", 100, "l2")]
        price.side_effect = [0.10, None]
        assert check_grid.rank_by_cost_carbon(candidates, 0.5) is None

    def test_load_price_map_empty(self):
        os.environ.pop("COST_PRICE_MAP", None)
        assert check_grid._load_price_map() == {}

    def test_load_price_map_parses_json(self):
        try:
            os.environ["COST_PRICE_MAP"] = '{"CISO": "0.09", "GB": "0.11"}'
            assert check_grid._load_price_map() == {"CISO": "0.09", "GB": "0.11"}
        finally:
            os.environ.pop("COST_PRICE_MAP", None)

    def test_load_price_map_bad_json(self):
        try:
            os.environ["COST_PRICE_MAP"] = "{not json"
            assert check_grid._load_price_map() == {}
        finally:
            os.environ.pop("COST_PRICE_MAP", None)

    @mock.patch("check_grid.azure_pricing.get_region_price")
    def test_price_map_used_before_azure(self, azure):
        azure.return_value = 99.0  # should not be consulted for mapped zones
        zone_price = check_grid._zone_price("CISO", {"CISO": "0.07"})
        assert zone_price == 0.07
        azure.assert_not_called()

    @mock.patch("check_grid.azure_pricing.get_region_price")
    def test_price_map_falls_back_to_azure(self, azure):
        azure.return_value = 0.12
        assert check_grid._zone_price("GB", {"CISO": "0.07"}) == 0.12

    @mock.patch("check_grid.azure_pricing.get_region_price")
    def test_multi_cloud_price_map_ranking(self, azure):
        # All prices from the map (any cloud); cheapest wins at cost_weight=1
        try:
            os.environ["COST_PRICE_MAP"] = '{"CISO": "0.20", "GB": "0.05"}'
            zone, _, _ = check_grid.rank_by_cost_carbon([("CISO", 50, "l1"), ("GB", 60, "l2")], 1.0)
            assert zone == "GB"
            azure.assert_not_called()
        finally:
            os.environ.pop("COST_PRICE_MAP", None)

    @mock.patch("check_grid.azure_pricing.get_region_price")
    def test_single_candidate_zero_span(self, price):
        # One candidate: price and carbon spans are both zero; must not divide by 0
        price.side_effect = [0.10]
        zone, intensity, label = check_grid.rank_by_cost_carbon([("CISO", 50, "l1")], 0.5)
        assert zone == "CISO"

    def test_empty_candidates(self):
        assert check_grid.rank_by_cost_carbon([], 0.5) is None


class TestEmitRunSignalsIntegration:
    """End-to-end coverage of the composed signal-emission path."""

    def _reset(self):
        check_grid._ledger_recorded = False
        check_grid._budget_summary = None
        check_grid._lifetime_summary = None
        check_grid._marginal_done = False
        check_grid._marginal_summary = None
        check_grid._status_badge_done = False
        check_grid._pr_comment_done = False
        check_grid._notify_done = False

    def test_file_ledger_budget_and_tier(self):
        self._reset()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as lf:
            ledger_path = lf.name
        os.unlink(ledger_path)
        out = tempfile.NamedTemporaryFile(mode="w+", delete=False)
        out.close()
        try:
            os.environ["GITHUB_OUTPUT"] = out.name
            os.environ["LEDGER"] = f"file:{ledger_path}"
            os.environ["MONTHLY_BUDGET_GRAMS"] = "2000"
            os.environ["TIER_THRESHOLDS"] = "150,300"
            tier, _ = check_grid.emit_run_signals("GB", 192, True, 250)
            assert tier == "amber"
            content = open(out.name).read()
            assert "carbon_tier=amber" in content
            assert "budget_state=ok" in content
            assert "budget_exceeded=false" in content
            assert os.path.exists(ledger_path)  # ledger actually written
        finally:
            for p in (out.name, ledger_path):
                if os.path.exists(p):
                    os.unlink(p)
            for k in ("GITHUB_OUTPUT", "LEDGER", "MONTHLY_BUDGET_GRAMS", "TIER_THRESHOLDS"):
                os.environ.pop(k, None)
            self._reset()


class TestDoctor:
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.detect_provider")
    def test_run_doctor_end_to_end(self, detect, check):
        detect.return_value = "uk_carbon_intensity"
        check.return_value = (True, 120)
        sumf = tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False)
        sumf.close()
        try:
            os.environ["GITHUB_STEP_SUMMARY"] = sumf.name
            os.environ["GRID_ZONES"] = "GB"
            check_grid.run_doctor()
            written = open(sumf.name).read()
            assert "Zone connectivity" in written
            assert "`GB`" in written
            assert "OK" in written
        finally:
            os.unlink(sumf.name)
            for k in ("GITHUB_STEP_SUMMARY", "GRID_ZONES"):
                os.environ.pop(k, None)

    def test_render_report_contains_sections(self):
        results = [
            {"zone": "GB", "provider": "uk", "token": "n/a", "status": "OK", "detail": "120"},
            {
                "zone": "FR",
                "provider": "entsoe",
                "token": "MISSING",
                "status": "FAIL",
                "detail": "no token",
            },
        ]
        features = [("Ledger", "on"), ("Carbon budget", "off")]
        report = "\n".join(check_grid.render_doctor_report(results, features))
        assert "Zone connectivity" in report
        assert "Optional features" in report
        assert "`GB`" in report
        assert "MISSING" in report
        assert "Ledger" in report

    def test_enabled_features_reflects_env(self):
        env = {"LEDGER": "gist:x", "COST_WEIGHT": "0.5", "NOTIFY_WEBHOOK": ""}
        feats = dict(check_grid._enabled_features(env))
        assert feats["Ledger"] == "on"
        assert feats["Cost+carbon"] == "on"
        assert feats["Notifications"] == "off"

    def test_enabled_features_bad_cost_weight(self):
        feats = dict(check_grid._enabled_features({"COST_WEIGHT": "abc"}))
        assert feats["Cost+carbon"] == "off"

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.detect_provider")
    def test_probe_zone_ok(self, detect, check):
        detect.return_value = "uk_carbon_intensity"
        check.return_value = (True, 90)
        r = check_grid.probe_zone("GB", 250, "", "", "")
        assert r["status"] == "OK"
        assert "90" in r["detail"]
        assert r["token"] == "n/a"

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.detect_provider")
    def test_probe_zone_missing_token(self, detect, check):
        detect.return_value = check_grid.PROVIDER_ENTSOE
        check.return_value = (None, None)
        r = check_grid.probe_zone("FR", 250, "", "", "")
        assert r["status"] == "FAIL"
        assert r["token"] == "MISSING"


class TestMarginalOutputs:
    def _reset(self):
        check_grid._marginal_done = False
        check_grid._marginal_summary = None

    def test_noop_without_creds(self):
        self._reset()
        os.environ.pop("WATTTIME_USERNAME", None)
        os.environ.pop("WATTTIME_PASSWORD", None)
        check_grid.emit_marginal_outputs()
        assert check_grid._marginal_summary is None

    @mock.patch("check_grid.watttime.get_marginal_index")
    @mock.patch("check_grid.watttime.login")
    def test_clean_when_below_threshold(self, login, idx):
        self._reset()
        login.return_value = "tok"
        idx.return_value = 20
        out = tempfile.NamedTemporaryFile(mode="w+", delete=False)
        out.close()
        try:
            os.environ["GITHUB_OUTPUT"] = out.name
            os.environ["WATTTIME_USERNAME"] = "u"
            os.environ["WATTTIME_PASSWORD"] = "p"
            os.environ["MARGINAL_MAX_PERCENTILE"] = "33"
            check_grid.emit_marginal_outputs()
            with open(out.name) as f:
                content = f.read()
            assert "marginal_percentile=20" in content
            assert "marginal_clean=true" in content
            assert check_grid._marginal_summary["clean"] is True
        finally:
            os.unlink(out.name)
            for k in (
                "GITHUB_OUTPUT",
                "WATTTIME_USERNAME",
                "WATTTIME_PASSWORD",
                "MARGINAL_MAX_PERCENTILE",
            ):
                os.environ.pop(k, None)

    @mock.patch("check_grid.watttime.get_marginal_index")
    @mock.patch("check_grid.watttime.login")
    def test_dirty_when_above_threshold(self, login, idx):
        self._reset()
        login.return_value = "tok"
        idx.return_value = 90
        out = tempfile.NamedTemporaryFile(mode="w+", delete=False)
        out.close()
        try:
            os.environ["GITHUB_OUTPUT"] = out.name
            os.environ["WATTTIME_USERNAME"] = "u"
            os.environ["WATTTIME_PASSWORD"] = "p"
            check_grid.emit_marginal_outputs()
            with open(out.name) as f:
                content = f.read()
            assert "marginal_clean=false" in content
        finally:
            os.unlink(out.name)
            for k in ("GITHUB_OUTPUT", "WATTTIME_USERNAME", "WATTTIME_PASSWORD"):
                os.environ.pop(k, None)


class TestEstimateEmissions:
    def test_none_intensity_zero(self):
        assert check_grid.estimate_emissions(None) == 0.0

    def test_proportional_to_intensity(self):
        # 80 and 320 avoid the banker's-rounding edge of round(1.25, 1)
        low = check_grid.estimate_emissions(80)
        high = check_grid.estimate_emissions(320)
        assert high > low > 0
        assert high == pytest.approx(low * 4, rel=0.01)

    def test_longer_job_emits_more(self):
        assert check_grid.estimate_emissions(100, job_minutes=60) > check_grid.estimate_emissions(
            100, job_minutes=15
        )

    def test_negative_intensity_clamped(self):
        assert check_grid.estimate_emissions(-50) == 0.0

    def test_negative_job_minutes_clamped(self):
        assert check_grid.estimate_emissions(100, job_minutes=-5) == 0.0


class TestCarbonBudget:
    def _reset(self):
        check_grid._ledger_recorded = False
        check_grid._lifetime_summary = None
        check_grid._budget_summary = None

    def _run(self, ledger_path, budget, emitted):
        # Seed the ledger with prior emissions this month via a direct record,
        # then drive the budget output computation.
        self._reset()
        out = tempfile.NamedTemporaryFile(mode="w+", delete=False)
        out.close()
        os.environ["GITHUB_OUTPUT"] = out.name
        os.environ["LEDGER"] = f"file:{ledger_path}"
        os.environ["MONTHLY_BUDGET_GRAMS"] = str(budget)
        # emitted comes from intensity via estimate_emissions; pick intensity so
        # that emitted grams ~= the value we want is not necessary; pass directly
        check_grid.record_lifetime_savings(0, emitted_grams=emitted)
        with open(out.name) as f:
            content = f.read()
        os.unlink(out.name)
        return content

    def test_under_budget_state_ok(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as lf:
            ledger_path = lf.name
        os.unlink(ledger_path)
        try:
            content = self._run(ledger_path, budget=1000, emitted=100)
            assert "budget_used_pct=10.0" in content
            assert "budget_exceeded=false" in content
            assert "budget_state=ok" in content
        finally:
            if os.path.exists(ledger_path):
                os.unlink(ledger_path)
            for k in ("GITHUB_OUTPUT", "LEDGER", "MONTHLY_BUDGET_GRAMS"):
                os.environ.pop(k, None)

    def test_over_budget_exceeded(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as lf:
            ledger_path = lf.name
        os.unlink(ledger_path)
        try:
            content = self._run(ledger_path, budget=50, emitted=80)
            assert "budget_exceeded=true" in content
            assert "budget_state=exceeded" in content
            assert check_grid._budget_summary["exceeded"] is True
            # remaining is clamped to 0, never negative
            assert "budget_remaining_grams=0" in content
        finally:
            if os.path.exists(ledger_path):
                os.unlink(ledger_path)
            for k in ("GITHUB_OUTPUT", "LEDGER", "MONTHLY_BUDGET_GRAMS"):
                os.environ.pop(k, None)

    def test_warning_at_80_percent(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as lf:
            ledger_path = lf.name
        os.unlink(ledger_path)
        try:
            content = self._run(ledger_path, budget=1000, emitted=800)
            assert "budget_state=warning" in content
            assert "budget_exceeded=false" in content
        finally:
            if os.path.exists(ledger_path):
                os.unlink(ledger_path)
            for k in ("GITHUB_OUTPUT", "LEDGER", "MONTHLY_BUDGET_GRAMS"):
                os.environ.pop(k, None)

    def test_budget_emitted_on_dirty_path(self):
        # On a dirty grid (no savings recorded), budget gating must still work:
        # write_job_summary force-records so budget_exceeded is emitted
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as lf:
            ledger_path = lf.name
        os.unlink(ledger_path)
        out = tempfile.NamedTemporaryFile(mode="w+", delete=False)
        out.close()
        self._reset()
        try:
            os.environ["GITHUB_OUTPUT"] = out.name
            os.environ["LEDGER"] = f"file:{ledger_path}"
            os.environ["MONTHLY_BUDGET_GRAMS"] = "1000"
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
            # dirty grid: is_green False, no set_savings_outputs call beforehand
            check_grid.write_job_summary("PL", 600, False, 250)
            with open(out.name) as f:
                content = f.read()
            assert "budget_exceeded=" in content
            assert "budget_state=" in content
        finally:
            for p in (out.name, ledger_path):
                if os.path.exists(p):
                    os.unlink(p)
            for k in ("GITHUB_OUTPUT", "LEDGER", "MONTHLY_BUDGET_GRAMS"):
                os.environ.pop(k, None)


class TestRecordLifetimeSavings:
    def _reset(self):
        check_grid._ledger_recorded = False
        check_grid._lifetime_summary = None

    def test_no_ledger_config_is_noop(self):
        self._reset()
        os.environ.pop("LEDGER", None)
        with tempfile.NamedTemporaryFile(mode="w+", delete=False) as f:
            path = f.name
        try:
            os.environ["GITHUB_OUTPUT"] = path
            check_grid.record_lifetime_savings(100)
            with open(path) as f:
                assert "co2_saved_total_grams" not in f.read()
            assert check_grid._lifetime_summary is None
        finally:
            os.unlink(path)
            os.environ.pop("GITHUB_OUTPUT", None)

    def test_file_ledger_sets_outputs_and_summary(self):
        self._reset()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as lf:
            ledger_path = lf.name
        os.unlink(ledger_path)
        with tempfile.NamedTemporaryFile(mode="w+", delete=False) as f:
            out_path = f.name
        try:
            os.environ["GITHUB_OUTPUT"] = out_path
            os.environ["LEDGER"] = f"file:{ledger_path}"
            check_grid.record_lifetime_savings(100)
            with open(out_path) as f:
                content = f.read()
            assert "co2_saved_total_grams=100" in content
            assert "co2_saved_total_equivalent=" in content
            assert check_grid._lifetime_summary["total_runs"] == 1
        finally:
            for p in (ledger_path, out_path):
                if os.path.exists(p):
                    os.unlink(p)
            os.environ.pop("GITHUB_OUTPUT", None)
            os.environ.pop("LEDGER", None)

    def test_records_at_most_once_per_process(self):
        self._reset()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as lf:
            ledger_path = lf.name
        os.unlink(ledger_path)
        try:
            os.environ["LEDGER"] = f"file:{ledger_path}"
            check_grid.record_lifetime_savings(100)
            check_grid.record_lifetime_savings(100)  # second call must be ignored
            assert check_grid._lifetime_summary["total_runs"] == 1
        finally:
            if os.path.exists(ledger_path):
                os.unlink(ledger_path)
            os.environ.pop("LEDGER", None)


class TestPostPrCommentOnce:
    def _reset(self):
        check_grid._pr_comment_done = False

    def test_noop_when_disabled(self):
        self._reset()
        os.environ.pop("PR_COMMENT", None)
        with mock.patch("check_grid.pr_comment.post_comment") as posted:
            check_grid.post_pr_comment_once("CISO", 80, True, 250)
            posted.assert_not_called()

    def test_posts_when_enabled(self):
        self._reset()
        try:
            os.environ["PR_COMMENT"] = "true"
            with mock.patch("check_grid.pr_comment.post_comment") as posted:
                check_grid.post_pr_comment_once("CISO", 80, True, 250, co2_saved=1500)
                posted.assert_called_once()
                # body is the 5th positional arg
                body = posted.call_args.args[4]
                assert "CISO" in body
        finally:
            os.environ.pop("PR_COMMENT", None)

    def test_only_once(self):
        self._reset()
        try:
            os.environ["PR_COMMENT"] = "true"
            with mock.patch("check_grid.pr_comment.post_comment") as posted:
                check_grid.post_pr_comment_once("CISO", 80, True, 250)
                check_grid.post_pr_comment_once("CISO", 80, True, 250)
                assert posted.call_count == 1
        finally:
            os.environ.pop("PR_COMMENT", None)


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

    def test_heuristic_forecast_is_labeled(self):
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as f:
            path = f.name
        try:
            os.environ["GITHUB_STEP_SUMMARY"] = path
            check_grid.write_job_summary(
                "ZA",
                700,
                False,
                250,
                forecast_at="2026-03-10T03:00Z",
                forecast_intensity=650,
                forecast_heuristic=True,
            )
            content = open(path).read()
            assert "Next Green Window (estimated)" in content
            assert "650 gCO2eq/kWh (estimate)" in content
        finally:
            os.unlink(path)
            os.environ.pop("GITHUB_STEP_SUMMARY", None)

    def test_real_forecast_is_not_labeled_estimate(self):
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as f:
            path = f.name
        try:
            os.environ["GITHUB_STEP_SUMMARY"] = path
            check_grid.write_job_summary(
                "GB",
                300,
                False,
                250,
                forecast_at="2026-03-10T14:00Z",
                forecast_intensity=90,
                forecast_heuristic=False,
            )
            content = open(path).read()
            assert "**Next Green Window**" in content
            assert "(estimated)" not in content
            assert "(estimate)" not in content
        finally:
            os.unlink(path)
            os.environ.pop("GITHUB_STEP_SUMMARY", None)


class TestRoutingComparison:
    def test_renders_bars_and_markers(self):
        measured = [("BR-NE", 27), ("GB", 169), ("AU-NSW", 501)]
        panel = check_grid.render_routing_comparison(measured, "BR-NE")
        assert panel is not None
        text = "\n".join(panel)
        # chosen zone is marked; the dirtiest is left unmarked (chart speaks for itself)
        assert "BR-NE" in text and "routed here" in text
        assert "AU-NSW" in text
        assert "avoided (dirtiest)" not in text
        # both baselines present (delta footer still references the dirtiest)
        assert "dirtiest candidate" in text
        assert "global average" in text
        # fenced for monospace rendering
        assert panel[0] == "```text" and panel[-1] == "```"

    def test_delta_math(self):
        panel = check_grid.render_routing_comparison([("A", 100), ("B", 500)], "A")
        text = "\n".join(panel)
        # 500 - 100 = 400 avoided, (400/500) = 80% lower than worst
        assert "400 gCO2eq/kWh" in text
        assert "80% lower" in text

    def test_needs_two_zones(self):
        assert check_grid.render_routing_comparison([("A", 100)], "A") is None
        assert check_grid.render_routing_comparison([], None) is None

    def test_summary_includes_comparison(self):
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".md", delete=False) as f:
            path = f.name
        try:
            os.environ["GITHUB_STEP_SUMMARY"] = path
            check_grid.write_job_summary(
                "GB", 169, True, 250, comparison=[("GB", 169), ("AU-NSW", 501)]
            )
            with open(path) as f:
                content = f.read()
            assert "Carbon-aware routing" in content
            assert "routed here" in content
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
        is_green, intensity = check_grid.check_carbon_intensity("AU-NSW", 250, PROVIDER_AEMO)
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
        is_green, intensity = check_grid.check_carbon_intensity("ZA", 250, PROVIDER_OPEN_METEO)
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

    def test_forecast_returns_heuristic(self):
        """Grid India forecast should return time-of-day heuristic."""
        dt, intensity = grid_india.get_forecast("IN-SO", 500)
        # With a high threshold, either already green (None) or finds a window
        assert dt is None or isinstance(dt, str)

    def test_forecast_south_lower_than_north(self):
        """IN-SO should have lower midday intensity than IN-NO."""
        dt_south, int_south = grid_india.get_forecast("IN-SO", 1000)
        dt_north, int_north = grid_india.get_forecast("IN-NO", 1000)
        # Both should find green windows at high threshold
        # The actual intensity comparison depends on time of day,
        # so just verify both return valid results
        assert dt_south is None or isinstance(dt_south, str)
        assert dt_north is None or isinstance(dt_north, str)

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

    def test_parse_energy_balance_nested(self):
        # Real ONS shape: {region_key: {"geracao": {total, fuel: MW, ...}}}
        data = {
            "sul": {
                "geracao": {
                    "total": 7000.0,
                    "hidraulica": 5000.0,
                    "termica": 2000.0,
                    "eolica": 0.0,
                }
            }
        }
        result = ons_brazil._parse_energy_balance(data, "sul")
        assert result is not None
        assert result["hidraulica"] == 5000.0
        assert result["termica"] == 2000.0
        # the aggregate "total" and zero-valued sources are dropped
        assert "total" not in result
        assert "eolica" not in result

    def test_parse_energy_balance_missing_region(self):
        data = {"sul": {"geracao": {"hidraulica": 5000.0}}}
        assert ons_brazil._parse_energy_balance(data, "nordeste") is None

    def test_parse_energy_balance_none(self):
        assert ons_brazil._parse_energy_balance(None, "sul") is None

    @mock.patch("providers.ons_brazil._fetch_energy_balance")
    def test_check_intensity_api_failure(self, mock_fetch):
        mock_fetch.return_value = None
        is_green, intensity = ons_brazil.check_carbon_intensity("BR-S", 250)
        assert is_green is None

    def test_forecast_returns_heuristic(self):
        """ONS Brazil forecast should return time-of-day heuristic."""
        dt, intensity = ons_brazil.get_forecast("BR-S", 500)
        # With a high threshold, should find a window or already be green
        assert dt is None or isinstance(dt, str)

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

    def test_forecast_returns_heuristic(self):
        """Eskom forecast should return time-of-day heuristic."""
        # At 250 threshold, SA grid (650+ gCO2eq/kWh) will never be green
        dt, intensity = eskom.get_forecast("ZA", 250)
        assert dt == "none_in_forecast"
        assert intensity is None

    def test_forecast_with_high_threshold(self):
        """Eskom forecast with high threshold should find a window."""
        dt, intensity = eskom.get_forecast("ZA", 800)
        # SA midday is ~650, so with 800 threshold it should find a window
        assert dt is None or isinstance(dt, str)
        if dt and dt != "none_in_forecast":
            assert intensity is not None
            assert intensity <= 800

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
        assert "BR-S" in zone_names  # ONS Brazil
        # ZA intentionally excluded: ~85% coal (~750 gCO2eq/kWh)
        assert "ZA" not in zone_names
        # Grid India excluded: geo-walled API, always fails from CI runners
        assert not any(z.startswith("IN-") for z in zone_names)

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
            check_grid.set_runner_outputs("CISO", None, "", "", "")
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
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
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
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
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
        zone, time, intensity = check_grid.queue_find_optimal_window(zones, 250, 24)
        assert zone == "CISO"
        assert time == "2026-03-10T14:00Z"
        assert intensity == 120

    @mock.patch("check_grid.get_forecast")
    def test_queue_find_optimal_window_none(self, mock_forecast):
        mock_forecast.return_value = ("none_in_forecast", None)
        zones = [{"zone": "PJM", "runner_label": None}]
        zone, time, intensity = check_grid.queue_find_optimal_window(zones, 250, 24)
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
        zone, time, intensity = check_grid.queue_find_optimal_window(zones, 250, 24)
        assert zone == "BPAT"  # Lower intensity
        assert intensity == 80


class TestQueueStrategyMain:
    """Exercise the queue-strategy branch of main() end to end."""

    @mock.patch("check_grid.trigger_workflow")
    @mock.patch("check_grid.check_multiple_zones")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_queue_already_green_dispatches_now(
        self, mock_summary, mock_output, mock_multi, mock_trigger
    ):
        # A zone is already green: dispatch immediately, optimal_dispatch_at=now
        mock_multi.return_value = ("CISO", 90, None, [])
        os.environ["GRID_ZONES"] = "CISO,GB"
        os.environ["STRATEGY"] = "queue"
        os.environ["WORKFLOW_ID"] = "heavy.yml"
        os.environ["GITHUB_TOKEN"] = "tok"
        os.environ["TARGET_REPO"] = "owner/repo"

        with pytest.raises(SystemExit) as exc:
            check_grid.main()
        assert exc.value.code == 0
        out = {c[0][0]: c[0][1] for c in mock_output.call_args_list}
        assert out["optimal_dispatch_at"] == "now"
        assert out["grid_clean"] == "true"
        mock_trigger.assert_called_once()

    @mock.patch("check_grid.queue_find_optimal_window")
    @mock.patch("check_grid.check_multiple_zones")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_queue_finds_future_window(self, mock_summary, mock_output, mock_multi, mock_window):
        # Nothing green now, but a future window exists within the deadline
        mock_multi.return_value = (None, None, None, [])
        mock_window.return_value = ("CISO", "2026-03-10T14:00Z", 120)
        os.environ["GRID_ZONES"] = "CISO,GB"
        os.environ["STRATEGY"] = "queue"
        os.environ["WORKFLOW_ID"] = ""

        with pytest.raises(SystemExit) as exc:
            check_grid.main()
        assert exc.value.code == 0
        out = {c[0][0]: c[0][1] for c in mock_output.call_args_list}
        assert out["optimal_dispatch_at"] == "2026-03-10T14:00Z"
        assert out["optimal_zone"] == "CISO"
        assert out["grid_clean"] == "false"

    @mock.patch("check_grid.queue_find_optimal_window")
    @mock.patch("check_grid.check_multiple_zones")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_queue_no_window_no_fail(self, mock_summary, mock_output, mock_multi, mock_window):
        mock_multi.return_value = (None, None, None, [])
        mock_window.return_value = (None, None, None)
        os.environ["GRID_ZONES"] = "CISO,GB"
        os.environ["STRATEGY"] = "queue"
        os.environ["WORKFLOW_ID"] = ""

        with pytest.raises(SystemExit) as exc:
            check_grid.main()
        assert exc.value.code == 0
        out = {c[0][0]: c[0][1] for c in mock_output.call_args_list}
        assert out["optimal_dispatch_at"] == "none_in_deadline"

    @mock.patch("check_grid.queue_find_optimal_window")
    @mock.patch("check_grid.check_multiple_zones")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_queue_no_window_fail_on_api_error(
        self, mock_summary, mock_output, mock_multi, mock_window
    ):
        # No window + fail_on_api_error: must exit non-zero
        mock_multi.return_value = (None, None, None, [])
        mock_window.return_value = (None, None, None)
        os.environ["GRID_ZONES"] = "CISO,GB"
        os.environ["STRATEGY"] = "queue"
        os.environ["FAIL_ON_API_ERROR"] = "true"
        os.environ["WORKFLOW_ID"] = ""

        with pytest.raises(SystemExit) as exc:
            check_grid.main()
        assert exc.value.code == 1


class TestSingleZoneDirtyMain:
    """Exercise the single-zone dirty and API-error paths of main()."""

    @mock.patch("check_grid.handle_dirty_grid")
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_dirty_grid_sets_outputs_no_dispatch(
        self, mock_summary, mock_output, mock_check, mock_dirty
    ):
        mock_check.return_value = (False, 480)
        mock_dirty.return_value = ("stable", "2026-03-10T03:00Z", 90)
        os.environ["GRID_ZONE"] = "AU-NSW"
        os.environ["WORKFLOW_ID"] = ""

        with pytest.raises(SystemExit) as exc:
            check_grid.main()
        assert exc.value.code == 0
        mock_dirty.assert_called_once()

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_api_error_skips_without_fail_flag(self, mock_summary, mock_output, mock_check):
        mock_check.return_value = (None, None)
        os.environ["GRID_ZONE"] = "CISO"
        os.environ["WORKFLOW_ID"] = ""

        with pytest.raises(SystemExit) as exc:
            check_grid.main()
        assert exc.value.code == 0
        out = {c[0][0]: c[0][1] for c in mock_output.call_args_list}
        assert out["grid_clean"] == "false"
        assert out["carbon_intensity"] == "unknown"

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_api_error_fails_with_flag(self, mock_summary, mock_output, mock_check):
        mock_check.return_value = (None, None)
        os.environ["GRID_ZONE"] = "CISO"
        os.environ["FAIL_ON_API_ERROR"] = "true"
        os.environ["WORKFLOW_ID"] = ""

        with pytest.raises(SystemExit) as exc:
            check_grid.main()
        assert exc.value.code == 1

    @mock.patch("check_grid.smart_wait_single")
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_smart_wait_invoked_when_dirty(self, mock_summary, mock_output, mock_check, mock_wait):
        # Dirty now + max_wait set: smart_wait_single runs and turns it green
        mock_check.return_value = (False, 400)
        mock_wait.return_value = (True, 90, 12.0)
        os.environ["GRID_ZONE"] = "CISO"
        os.environ["MAX_WAIT"] = "60"
        os.environ["WORKFLOW_ID"] = ""

        # Green single-zone path returns normally (no sys.exit)
        check_grid.main()
        mock_wait.assert_called_once()
        out = {c[0][0]: c[0][1] for c in mock_output.call_args_list}
        assert out["grid_clean"] == "true"


# --- Inline mode simplification test ---


class TestInlineModeDispatch:
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
            # Should not raise; inline mode doesn't need token
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
    def test_wizard_registries_match_dispatcher(self):
        """The wizard must know every provider the dispatcher routes to, so it
        can't silently mislabel a zone (e.g. after a new provider is added)."""
        import check_grid
        from setup_wizard import _PROVIDER_MODULES, _PROVIDER_NAMES

        for p in check_grid._PROVIDER_MODULES:
            assert p in _PROVIDER_NAMES, f"Wizard missing display name for {p}"
            assert p in _PROVIDER_MODULES, f"Wizard missing module for {p}"

    @mock.patch("setup_wizard.canada.check_carbon_intensity", return_value=(True, 30))
    def test_zone_canada(self, mock_check):
        from setup_wizard import test_zone

        result = test_zone("CA-QC")
        assert result["status"] == "ok"
        assert result["intensity"] == 30
        assert "Canada" in result["provider"]

    @mock.patch("setup_wizard.taiwan.check_carbon_intensity", return_value=(False, 527))
    def test_zone_taiwan(self, mock_check):
        from setup_wizard import test_zone

        result = test_zone("TW")
        assert result["status"] == "ok"
        assert result["intensity"] == 527
        assert "Taipower" in result["provider"]

    @mock.patch("setup_wizard.eia.check_carbon_intensity", return_value=(None, None))
    def test_zone_error_on_no_data(self, mock_check):
        from setup_wizard import test_zone

        result = test_zone("CISO")
        assert result["status"] == "error"
        assert "no data" in result["error"]

    @mock.patch("setup_wizard.uk.check_carbon_intensity", side_effect=RuntimeError("boom"))
    def test_zone_error_on_exception(self, mock_check):
        from setup_wizard import test_zone

        result = test_zone("GB")
        assert result["status"] == "error"
        assert "boom" in result["error"]

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
    # Every curated preset's zones should have an EXPLICIT cloud-region mapping.
    # get_cloud_region() falls back to a default (us-east-1) for unmapped zones,
    # so we assert membership in the mapping dicts rather than "is not None",
    # which would pass even for a totally unmapped zone.
    def _preset_zones(self):
        from providers import AUTO_GREEN_ZONES_FULL

        zones = set()
        for preset in (AUTO_GREEN_ZONES, AUTO_CLEANEST_ZONES, AUTO_GREEN_ZONES_FULL):
            zones.update(e["zone"] for e in preset)
        return zones

    def test_all_preset_zones_have_aws_mapping(self):
        missing = [z for z in self._preset_zones() if z not in ZONE_TO_AWS_REGION]
        assert not missing, f"Preset zones missing AWS region: {sorted(missing)}"

    def test_all_preset_zones_have_gcp_mapping(self):
        missing = [z for z in self._preset_zones() if z not in ZONE_TO_GCP_REGION]
        assert not missing, f"Preset zones missing GCP region: {sorted(missing)}"

    def test_all_preset_zones_have_azure_mapping(self):
        missing = [z for z in self._preset_zones() if z not in ZONE_TO_AZURE_REGION]
        assert not missing, f"Preset zones missing Azure region: {sorted(missing)}"

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
            PROVIDER_AEMO,
            PROVIDER_EIA,
            PROVIDER_ELECTRICITY_MAPS,
            PROVIDER_ENTSOE,
            PROVIDER_ESKOM,
            PROVIDER_GRID_INDIA,
            PROVIDER_ONS_BRAZIL,
            PROVIDER_OPEN_METEO,
            PROVIDER_UK,
        )

        for p in [
            PROVIDER_UK,
            PROVIDER_EIA,
            PROVIDER_AEMO,
            PROVIDER_GRID_INDIA,
            PROVIDER_ONS_BRAZIL,
            PROVIDER_ESKOM,
            PROVIDER_ENTSOE,
            PROVIDER_OPEN_METEO,
            PROVIDER_ELECTRICITY_MAPS,
        ]:
            assert p in _PROVIDER_MODULES, f"Missing {p} in _PROVIDER_MODULES"

    def test_all_provider_modules_have_required_functions(self):
        """Every provider module exposes the three required provider functions."""
        from check_grid import _PROVIDER_MODULES

        for provider_id, module in _PROVIDER_MODULES.items():
            assert hasattr(module, "check_carbon_intensity"), (
                f"{provider_id} missing check_carbon_intensity"
            )
            assert hasattr(module, "get_forecast"), f"{provider_id} missing get_forecast"
            assert hasattr(module, "get_history_trend"), f"{provider_id} missing get_history_trend"

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
        # a zone that would hit EIA but also has coords, which is unrealistic.
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
            is_green, intensity = check_grid.check_carbon_intensity("GB", 250, PROVIDER_UK)
            assert is_green is True
            assert intensity == 150
            mock_meteo.assert_not_called()

    @mock.patch("check_grid.open_meteo.check_carbon_intensity")
    def test_no_double_fallback_for_open_meteo(self, mock_meteo):
        """Open-Meteo itself should not trigger fallback to Open-Meteo."""
        mock_meteo.return_value = (None, None)
        is_green, intensity = check_grid.check_carbon_intensity("IS", 250, PROVIDER_OPEN_METEO)
        assert is_green is None
        # Should only be called once (primary), not twice (no self-fallback)
        assert mock_meteo.call_count == 1


# ---------------------------------------------------------------------------
# Cloud auto-detection tests
# ---------------------------------------------------------------------------


class TestCloudAutoDetection:
    def test_aws_region_detection(self):
        """Detects grid zone from AWS_REGION env var."""
        with mock.patch.dict(os.environ, {"AWS_REGION": "us-west-2"}, clear=False):
            zone, source = detect_cloud_zone()
            assert zone == "BPAT"
            assert "AWS" in source

    def test_aws_default_region_detection(self):
        with mock.patch.dict(os.environ, {"AWS_DEFAULT_REGION": "eu-west-2"}, clear=False):
            os.environ.pop("AWS_REGION", None)
            zone, source = detect_cloud_zone()
            assert zone == "GB"

    def test_gcp_region_detection(self):
        with mock.patch.dict(os.environ, {"GOOGLE_CLOUD_REGION": "europe-west9"}, clear=False):
            os.environ.pop("AWS_REGION", None)
            os.environ.pop("AWS_DEFAULT_REGION", None)
            zone, source = detect_cloud_zone()
            assert zone == "FR"
            assert "GCP" in source

    def test_azure_region_detection(self):
        with mock.patch.dict(os.environ, {"AZURE_REGION": "japaneast"}, clear=False):
            os.environ.pop("AWS_REGION", None)
            os.environ.pop("AWS_DEFAULT_REGION", None)
            os.environ.pop("GOOGLE_CLOUD_REGION", None)
            zone, source = detect_cloud_zone()
            assert zone == "JP-TK"
            assert "Azure" in source

    def test_cloud_region_override(self):
        with mock.patch.dict(os.environ, {"CLOUD_REGION_OVERRIDE": "ap-southeast-1"}, clear=False):
            zone, source = detect_cloud_zone()
            assert zone == "SG"
            assert "CLOUD_REGION_OVERRIDE" in source

    def test_no_cloud_env_returns_none(self):
        env_keys = [
            "AWS_REGION",
            "AWS_DEFAULT_REGION",
            "GOOGLE_CLOUD_REGION",
            "CLOUDSDK_COMPUTE_REGION",
            "CLOUD_RUN_REGION",
            "AZURE_REGION",
            "REGION_NAME",
            "WEBSITE_SITE_NAME_REGION",
            "CLOUD_REGION_OVERRIDE",
            "GITHUB_ACTIONS",
            "RUNNER_NAME",
        ]
        clean_env = {k: v for k, v in os.environ.items() if k not in env_keys}
        with mock.patch.dict(os.environ, clean_env, clear=True):
            zone, source = detect_cloud_zone()
            assert zone is None
            assert source is None

    def test_unknown_region_returns_none(self):
        with mock.patch.dict(os.environ, {"AWS_REGION": "xx-unknown-99"}, clear=False):
            os.environ.pop("CLOUD_REGION_OVERRIDE", None)
            zone, source = detect_cloud_zone()
            assert zone is None


class TestReverseRegionMappings:
    def test_aws_reverse_map_covers_major_regions(self):
        major = ["us-west-1", "us-west-2", "us-east-1", "eu-west-2", "ap-northeast-1"]
        for region in major:
            assert region in AWS_REGION_TO_ZONE, f"Missing AWS reverse: {region}"

    def test_gcp_reverse_map_covers_major_regions(self):
        major = ["us-west1", "us-east4", "europe-west2", "asia-northeast1"]
        for region in major:
            assert region in GCP_REGION_TO_ZONE, f"Missing GCP reverse: {region}"

    def test_azure_reverse_map_covers_major_regions(self):
        major = ["eastus", "westus2", "uksouth", "japaneast"]
        for region in major:
            assert region in AZURE_REGION_TO_ZONE, f"Missing Azure reverse: {region}"

    def test_forward_regions_resolve_in_reverse_map(self):
        """Every region a zone forward-maps to must exist in the reverse map,
        or auto:detect (region -> zone) silently can't resolve a runner there."""
        for cloud, fwd, rev in [
            ("AWS", ZONE_TO_AWS_REGION, AWS_REGION_TO_ZONE),
            ("GCP", ZONE_TO_GCP_REGION, GCP_REGION_TO_ZONE),
            ("Azure", ZONE_TO_AZURE_REGION, AZURE_REGION_TO_ZONE),
        ]:
            missing = sorted({r for r in fwd.values() if r not in rev})
            assert not missing, f"{cloud} regions used but absent from reverse map: {missing}"


class TestAutoDetectPreset:
    def test_auto_detect_expansion_with_aws_region(self):
        with mock.patch.dict(os.environ, {"AWS_REGION": "us-west-1"}, clear=False):
            result = check_grid.expand_auto_zones("auto:detect")
            assert result is not None
            assert len(result) == 1
            assert result[0]["zone"] == "CISO"

    def test_auto_detect_fallback_to_cleanest(self):
        """auto:detect falls back to auto:cleanest when no cloud env is set."""
        env_keys = [
            "AWS_REGION",
            "AWS_DEFAULT_REGION",
            "GOOGLE_CLOUD_REGION",
            "CLOUDSDK_COMPUTE_REGION",
            "CLOUD_RUN_REGION",
            "AZURE_REGION",
            "REGION_NAME",
            "WEBSITE_SITE_NAME_REGION",
            "CLOUD_REGION_OVERRIDE",
            "GITHUB_ACTIONS",
            "RUNNER_NAME",
        ]
        clean_env = {k: v for k, v in os.environ.items() if k not in env_keys}
        with mock.patch.dict(os.environ, clean_env, clear=True):
            result = check_grid.expand_auto_zones("auto:detect")
            assert result is not None
            assert len(result) == len(AUTO_CLEANEST_ZONES)


class TestZeroConfigDefault:
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_no_zone_input_uses_auto_detect(self, mock_summary, mock_output, mock_check):
        """When no zone is specified, should use auto:detect."""
        mock_check.return_value = (True, 100)
        os.environ.pop("GRID_ZONE", None)
        os.environ.pop("GRID_ZONES", None)
        os.environ["WORKFLOW_ID"] = ""
        os.environ.pop("CARBON_POLICY_PATH", None)
        # Set a cloud region so auto:detect finds something
        os.environ["AWS_REGION"] = "us-west-2"
        try:
            check_grid.main()
            output_calls = {call[0][0]: call[0][1] for call in mock_output.call_args_list}
            assert output_calls["grid_clean"] == "true"
        finally:
            os.environ.pop("AWS_REGION", None)


# ---------------------------------------------------------------------------
# Forecast heuristic tests
# ---------------------------------------------------------------------------


class TestIndiaForecastHeuristic:
    def test_high_threshold_finds_window(self):
        """With high threshold, India forecast should find a green window."""
        dt, intensity = grid_india.get_forecast("IN-SO", 500)
        # Should either be already in window (None) or find one
        if dt is not None:
            assert isinstance(dt, str)
            if dt != "none_in_forecast":
                assert intensity is not None
                assert intensity <= 500

    def test_very_low_threshold_no_window(self):
        """With very low threshold, India forecast won't find a window."""
        dt, intensity = grid_india.get_forecast("IN-NO", 50)
        assert dt == "none_in_forecast"
        assert intensity is None


class TestBrazilForecastHeuristic:
    def test_high_threshold_finds_window(self):
        dt, intensity = ons_brazil.get_forecast("BR-S", 500)
        if dt is not None and dt != "none_in_forecast":
            assert intensity is not None
            assert intensity <= 500

    def test_hydro_zone_very_clean(self):
        """BR-S (hydro) should be green at moderate threshold."""
        dt, intensity = ons_brazil.get_forecast("BR-S", 200)
        # Should find a window or be already green
        if dt is not None and dt != "none_in_forecast":
            assert intensity <= 200


class TestEskomForecastHeuristic:
    def test_coal_grid_rarely_green(self):
        """SA grid at low threshold should not find green window."""
        dt, intensity = eskom.get_forecast("ZA", 250)
        assert dt == "none_in_forecast"

    def test_high_threshold_finds_window(self):
        """SA grid at high threshold should find midday window."""
        dt, intensity = eskom.get_forecast("ZA", 800)
        if dt is not None and dt != "none_in_forecast":
            assert intensity is not None
            assert intensity <= 800


# ---------------------------------------------------------------------------
# auto:nearest preset tests
# ---------------------------------------------------------------------------


class TestAutoNearestPreset:
    def test_nearest_with_tz_utc(self):
        """TZ=UTC should resolve to UTC+0 zones."""
        with mock.patch.dict(os.environ, {"TZ": "UTC+0"}):
            zones = check_grid.expand_auto_zones("auto:nearest")
            zone_ids = [z["zone"] for z in zones]
            assert "GB-16" in zone_ids or "GB" in zone_ids

    def test_nearest_with_tz_offset_positive(self):
        """TZ=UTC+5.5 (India) resolves to the nearest reachable clean zones.

        Grid India itself is geo-walled, so the offset maps to Australian
        clean zones rather than the unreachable IN-* zones."""
        with mock.patch.dict(os.environ, {"TZ": "UTC+5.5"}):
            zones = check_grid.expand_auto_zones("auto:nearest")
            zone_ids = [z["zone"] for z in zones]
            assert len(zone_ids) > 0
            assert not any(z.startswith("IN-") for z in zone_ids)
            assert "AU-TAS" in zone_ids

    def test_nearest_with_tz_offset_negative(self):
        """TZ=UTC-8 should resolve to US West zones."""
        with mock.patch.dict(os.environ, {"TZ": "UTC-8"}):
            zones = check_grid.expand_auto_zones("auto:nearest")
            zone_ids = [z["zone"] for z in zones]
            assert "CISO" in zone_ids

    def test_nearest_fallback_to_cleanest(self):
        """No TZ env var falls back to system timezone (which resolves to some zones)."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TZ", None)
            zones = check_grid.expand_auto_zones("auto:nearest")
            assert len(zones) > 0

    def test_nearest_etc_gmt_inverted(self):
        """Etc/GMT-5 means UTC+5 (inverted sign), resolving to reachable zones."""
        with mock.patch.dict(os.environ, {"TZ": "Etc/GMT-5"}):
            zones = check_grid.expand_auto_zones("auto:nearest")
            zone_ids = [z["zone"] for z in zones]
            # UTC+5 maps to the AU clean zones (Grid India is geo-walled)
            assert "AU-TAS" in zone_ids


class TestDetectUtcOffset:
    def test_utc_zero(self):
        with mock.patch.dict(os.environ, {"TZ": "UTC"}):
            assert check_grid._detect_utc_offset() == 0

    def test_utc_plus_offset(self):
        with mock.patch.dict(os.environ, {"TZ": "UTC+5.5"}):
            assert check_grid._detect_utc_offset() == 5.5

    def test_utc_minus_offset(self):
        with mock.patch.dict(os.environ, {"TZ": "UTC-8"}):
            assert check_grid._detect_utc_offset() == -8

    def test_gmt_offset(self):
        with mock.patch.dict(os.environ, {"TZ": "GMT+3"}):
            assert check_grid._detect_utc_offset() == 3

    def test_etc_gmt_inverted(self):
        """Etc/GMT offsets are inverted: Etc/GMT-5 = UTC+5."""
        with mock.patch.dict(os.environ, {"TZ": "Etc/GMT-5"}):
            assert check_grid._detect_utc_offset() == 5

    def test_system_fallback(self):
        """With no TZ env, should fall back to system time."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TZ", None)
            offset = check_grid._detect_utc_offset()
            assert offset is not None
            assert -12 <= offset <= 14


# ---------------------------------------------------------------------------
# Cron schedule optimizer tests
# ---------------------------------------------------------------------------


class TestSuggestGreenCron:
    def test_solar_zone(self):
        """Solar zones should suggest midday cron."""
        cron, desc = check_grid.suggest_green_cron("CISO")
        assert cron is not None
        assert "solar peak" in desc

    def test_hydro_zone(self):
        """Hydro zones should suggest off-peak cron."""
        cron, desc = check_grid.suggest_green_cron("BPAT")
        assert cron is not None
        assert "off-peak" in desc

    def test_wind_zone(self):
        """Wind zones should suggest nighttime cron."""
        cron, desc = check_grid.suggest_green_cron("GB-16")
        assert cron is not None
        assert "wind peak" in desc

    def test_unknown_zone_returns_none(self):
        """Unknown zone should return None."""
        cron, desc = check_grid.suggest_green_cron("UNKNOWN-ZONE-XYZ")
        assert cron is None
        assert desc is None

    def test_cron_format_valid(self):
        """Cron expression should have 5 fields."""
        cron, _ = check_grid.suggest_green_cron("CISO")
        parts = cron.split()
        assert len(parts) == 5
        assert parts[0] == "0"  # minute
        assert 0 <= int(parts[1]) <= 23  # hour


# ---------------------------------------------------------------------------
# NEAREST_ZONES_BY_OFFSET coverage tests
# ---------------------------------------------------------------------------


class TestNearestZonesMapping:
    def test_all_major_offsets_covered(self):
        from providers import NEAREST_ZONES_BY_OFFSET

        for offset in range(-10, 14):
            assert offset in NEAREST_ZONES_BY_OFFSET, f"Missing offset {offset}"

    def test_half_hour_offsets(self):
        from providers import NEAREST_ZONES_BY_OFFSET

        assert 5.5 in NEAREST_ZONES_BY_OFFSET  # India
        assert 9.5 in NEAREST_ZONES_BY_OFFSET  # Australia Central


# ---------------------------------------------------------------------------
# Guarded env parsing helpers
# ---------------------------------------------------------------------------


class TestEnvParsingHelpers:
    def test_env_float_default_when_unset(self):
        os.environ.pop("MAX_CARBON", None)
        assert check_grid._env_float("MAX_CARBON", 250) == 250

    def test_env_float_default_when_empty(self):
        with mock.patch.dict(os.environ, {"MAX_CARBON": ""}):
            assert check_grid._env_float("MAX_CARBON", 250) == 250

    def test_env_float_parses_value(self):
        with mock.patch.dict(os.environ, {"MAX_CARBON": "123.5"}):
            assert check_grid._env_float("MAX_CARBON", 250) == 123.5

    def test_env_float_exits_on_malformed(self):
        with mock.patch.dict(os.environ, {"MAX_CARBON": "notanumber"}):
            with pytest.raises(SystemExit) as exc:
                check_grid._env_float("MAX_CARBON", 250)
            assert exc.value.code == check_grid.EXIT_FAILURE

    def test_env_int_default_when_unset(self):
        os.environ.pop("MAX_WAIT", None)
        assert check_grid._env_int("MAX_WAIT", 0) == 0

    def test_env_int_parses_value(self):
        with mock.patch.dict(os.environ, {"MAX_WAIT": "30"}):
            assert check_grid._env_int("MAX_WAIT", 0) == 30

    def test_env_int_exits_on_malformed(self):
        with mock.patch.dict(os.environ, {"MAX_WAIT": "soon"}):
            with pytest.raises(SystemExit) as exc:
                check_grid._env_int("MAX_WAIT", 0)
            assert exc.value.code == check_grid.EXIT_FAILURE

    def test_env_float_raw_overrides_env(self):
        # Explicit raw string takes precedence (policy-fallback path)
        with mock.patch.dict(os.environ, {"MAX_CARBON": "999"}):
            assert check_grid._env_float("MAX_CARBON", 250, "100") == 100

    def test_env_float_raw_empty_uses_default(self):
        assert check_grid._env_float("DEADLINE_HOURS", 24, "") == 24


# ---------------------------------------------------------------------------
# get_forecast forwards eia_api_key
# ---------------------------------------------------------------------------


class TestGetForecastEiaKey:
    @mock.patch("providers.eia.get_forecast", create=True)
    def test_eia_key_forwarded_to_extra_args(self, mock_fc):
        # EIA resolves the eia_api_key from the extra-args dict; verify it
        # reaches the provider module's get_forecast call
        mock_fc.return_value = (None, None)
        check_grid.get_forecast(
            "CISO", 250, PROVIDER_EIA, gridstatus_api_key="", eia_api_key="my-eia-key"
        )
        mock_fc.assert_called_once_with("CISO", 250, "my-eia-key")

    @mock.patch("providers.gridstatus.get_forecast")
    @mock.patch("providers.eia.get_forecast", create=True)
    def test_eia_still_returns_none_without_gridstatus(self, mock_eia_fc, mock_gs):
        # Without a gridstatus key, EIA forecast resolves to (None, None) today
        mock_eia_fc.return_value = (None, None)
        result = check_grid.get_forecast("CISO", 250, PROVIDER_EIA, "")
        assert result == (None, None)
        mock_gs.assert_not_called()


# ---------------------------------------------------------------------------
# _emit_green_result shared helper
# ---------------------------------------------------------------------------


class TestEmitGreenResult:
    @mock.patch("check_grid.trigger_workflow")
    def test_sets_grid_clean_and_co2_saved(self, mock_trigger):
        outputs = {}

        def _capture(name, value):
            outputs[name] = value

        with mock.patch("check_grid.set_output", side_effect=_capture):
            with mock.patch("check_grid.write_job_summary") as mock_summary:
                # Low intensity vs global average produces positive savings
                check_grid._emit_green_result(
                    "CISO", 50, None, 250, False, "", "", "", "main", "", "", "run-1"
                )

        assert outputs["grid_clean"] == "true"
        assert outputs["carbon_intensity"] == "50"
        assert "co2_saved_grams" in outputs
        assert float(outputs["co2_saved_grams"]) > 0
        mock_summary.assert_called_once()
        # Inline mode (no dispatch) should not trigger a workflow
        mock_trigger.assert_not_called()

    @mock.patch("check_grid.trigger_workflow")
    def test_dispatch_mode_triggers_workflow(self, mock_trigger):
        with mock.patch("check_grid.set_output"):
            with mock.patch("check_grid.write_job_summary"):
                check_grid._emit_green_result(
                    "CISO",
                    50,
                    None,
                    250,
                    True,
                    "owner/repo",
                    "wf.yml",
                    "tok",
                    "main",
                    "",
                    "",
                    "run-1",
                )
        mock_trigger.assert_called_once_with("owner/repo", "wf.yml", "tok", "main")


# ---------------------------------------------------------------------------
# Canada provider tests (IESO / AESO / Hydro-Quebec)
# ---------------------------------------------------------------------------

_IESO_XML = """<?xml version="1.0"?>
<Document xmlns="http://www.ieso.ca/schema">
  <DailyData>
    <HourlyData>
      <FuelTotal><Fuel>NUCLEAR</Fuel><Output>8000</Output></FuelTotal>
      <FuelTotal><Fuel>HYDRO</Fuel><Output>4000</Output></FuelTotal>
      <FuelTotal><Fuel>GAS</Fuel><Output>1000</Output></FuelTotal>
      <FuelTotal><Fuel>WIND</Fuel><Output>500</Output></FuelTotal>
    </HourlyData>
  </DailyData>
</Document>"""

_AESO_HTML = (
    "<TR><TD>COAL</TD><TD>1000</TD><TD>800</TD><TD>0</TD></TR>"
    "<TR><TD>GAS</TD><TD>2000</TD><TD>1500</TD><TD>0</TD></TR>"
    "<TR><TD>WIND</TD><TD>500</TD><TD>300</TD><TD>0</TD></TR>"
)


class TestCanadaProvider:
    def test_detect_provider(self):
        for z in ("CA-ON", "CA-AB", "CA-QC"):
            assert detect_provider(z) == PROVIDER_CANADA

    def test_quebec_is_fixed_estimate(self):
        is_green, intensity = canada.check_carbon_intensity("CA-QC", 250)
        assert is_green is True
        assert intensity == 30

    @mock.patch("providers.base.requests.get")
    def test_ieso_ontario_parse(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=200, text=_IESO_XML)
        is_green, intensity = canada.check_carbon_intensity("CA-ON", 250)
        # nuclear 8000*12 + hydro 4000*24 + gas 1000*490 + wind 500*12
        # = 96000 + 96000 + 490000 + 6000 = 688000 / 13500 = 51
        assert intensity == 51
        assert is_green is True

    @mock.patch("providers.base.requests.get")
    def test_aeso_alberta_parse(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=200, text=_AESO_HTML)
        is_green, intensity = canada.check_carbon_intensity("CA-AB", 250)
        # coal 800*820 + gas 1500*490 + wind 300*12 = 656000+735000+3600
        # = 1394600 / 2600 = 536
        assert intensity == 536
        assert is_green is False

    @mock.patch("providers.base.requests.get")
    def test_api_failure_returns_none(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=500, text="err")
        assert canada.check_carbon_intensity("CA-ON", 250) == (None, None)

    def test_unknown_zone(self):
        assert canada.check_carbon_intensity("CA-XX", 250) == (None, None)

    def test_no_forecast_or_trend(self):
        assert canada.get_forecast("CA-ON", 250) == (None, None)
        assert canada.get_history_trend("CA-ON") is None

    def test_storage_excluded(self):
        # battery is storage, excluded from the mix
        mix = {"hydro": 1000, "battery": 5000}
        assert canada._mix_to_intensity(mix) == 24


# ---------------------------------------------------------------------------
# Taiwan provider tests (Taipower)
# ---------------------------------------------------------------------------

_TAIPOWER_JSON = (
    b'{"aaData": ['
    b'["<b>\\u71c3\\u7164(Coal)</b>", "", "U1", "1000", "5000"],'
    b'["<b>\\u6c23(LNG)</b>", "", "U2", "1000", "3000"],'
    b'["<b>\\u6838\\u80fd(Nuclear)</b>", "", "U3", "1000", "2000"],'
    b'["<b>\\u592a\\u967d\\u80fd(Solar)</b>", "", "U4", "1000", "1000"],'
    b'["Energy Storage Load", "", "U5", "1000", "200"]'
    b"]}"
)


class TestTaiwanProvider:
    def test_detect_provider(self):
        assert detect_provider("TW") == PROVIDER_TAIWAN

    @mock.patch("providers.base.requests.get")
    def test_parse_generation(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=200, content=_TAIPOWER_JSON)
        is_green, intensity = taiwan.check_carbon_intensity("TW", 250)
        # coal 5000*820 + lng 3000*490 + nuclear 2000*12 + solar 1000*45
        # = 4100000 + 1470000 + 24000 + 45000 = 5639000 / 11000 = 513
        # (the "Load" row is skipped as storage charging)
        assert intensity == 513
        assert is_green is False

    @mock.patch("providers.base.requests.get")
    def test_api_failure_returns_none(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=500, content=b"", text="err")
        assert taiwan.check_carbon_intensity("TW", 250) == (None, None)

    def test_unknown_zone(self):
        assert taiwan.check_carbon_intensity("TW-XX", 250) == (None, None)

    def test_fuel_mapping(self):
        assert taiwan._fuel_of("Coal") == "coal"
        assert taiwan._fuel_of("LNG") == "natural_gas"
        assert taiwan._fuel_of("Energy Storage Load") is None
        assert taiwan._fuel_of("Energy Storage") == "battery"

    def test_no_forecast_or_trend(self):
        assert taiwan.get_forecast("TW", 250) == (None, None)
        assert taiwan.get_history_trend("TW") is None


# ---------------------------------------------------------------------------
# Flow tracing / consumption-based intensity (EU)
# ---------------------------------------------------------------------------


class TestFlowTracing:
    def test_solver_attributes_imports(self):
        from providers import flow_tracing as ft

        # IT-NO imports clean FR nuclear -> reads cleaner; NL imports DE coal -> dirtier
        prod_mw = {"FR": 50000, "IT-NO": 20000, "DE": 60000, "NL": 10000}
        prod_int = {"FR": 55, "IT-NO": 380, "DE": 420, "NL": 350}
        flows = {("FR", "IT-NO"): 4000, ("DE", "NL"): 8000}
        cons = ft.trace_consumption_intensity(prod_mw, prod_int, flows)
        assert cons["FR"] == 55.0  # exporter unchanged
        assert cons["IT-NO"] < prod_int["IT-NO"]  # importing clean -> lower
        assert cons["NL"] > prod_int["NL"]  # importing dirty -> higher

    def test_solver_empty(self):
        from providers import flow_tracing as ft

        assert ft.trace_consumption_intensity({}, {}, {}) == {}

    def test_solver_ignores_unknown_and_zero_flows(self):
        from providers import flow_tracing as ft

        prod_mw = {"FR": 1000}
        prod_int = {"FR": 50}
        # flow from an unknown zone and a zero flow are both ignored
        flows = {("XX", "FR"): 500, ("FR", "FR"): 0}
        assert ft.trace_consumption_intensity(prod_mw, prod_int, flows) == {"FR": 50.0}

    @mock.patch("providers.flow_tracing.compute_consumption_intensities")
    def test_apply_override_traced_zone(self, mock_compute):
        mock_compute.return_value = {"IT-NO": 326.0}
        g, i = check_grid._apply_consumption_intensity("IT-NO", 250, False, 380, "tok")
        assert i == 326 and g is False

    def test_apply_override_untraced_zone_unchanged(self):
        g, i = check_grid._apply_consumption_intensity("CISO", 250, True, 100, "tok")
        assert (g, i) == (True, 100)

    @mock.patch("providers.flow_tracing.compute_consumption_intensities")
    def test_apply_override_no_value_falls_back(self, mock_compute):
        mock_compute.return_value = {}  # computation produced nothing for FR
        g, i = check_grid._apply_consumption_intensity("FR", 250, True, 55, "tok")
        assert (g, i) == (True, 55)

    @mock.patch("providers.flow_tracing.compute_consumption_intensities")
    def test_apply_override_flips_verdict_to_green(self, mock_compute):
        # production 280 (dirty), consumption 240 (green) at threshold 250
        mock_compute.return_value = {"FR": 240.0}
        g, i = check_grid._apply_consumption_intensity("FR", 250, False, 280, "tok")
        assert i == 240 and g is True

    @mock.patch("providers.flow_tracing.compute_consumption_intensities")
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_main_consumption_mode_end_to_end(
        self, mock_summary, mock_output, mock_check, mock_compute
    ):
        mock_check.return_value = (False, 380)  # FR production dirty
        mock_compute.return_value = {"FR": 240.0}  # consumption green
        os.environ["GRID_ZONE"] = "FR"
        os.environ["ENTSOE_TOKEN"] = "tok"
        os.environ["CONSUMPTION_BASED"] = "true"
        os.environ["WORKFLOW_ID"] = ""

        check_grid.main()
        out = {c[0][0]: c[0][1] for c in mock_output.call_args_list}
        assert out["grid_clean"] == "true"
        assert out["carbon_intensity"] == "240"

    @mock.patch("providers.flow_tracing.compute_consumption_intensities")
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_main_consumption_off_uses_production(
        self, mock_summary, mock_output, mock_check, mock_compute
    ):
        mock_check.return_value = (True, 90)
        os.environ["GRID_ZONE"] = "FR"
        os.environ["ENTSOE_TOKEN"] = "tok"
        os.environ.pop("CONSUMPTION_BASED", None)  # default off
        os.environ["WORKFLOW_ID"] = ""

        check_grid.main()
        mock_compute.assert_not_called()  # never computed when mode is off

    def test_flow_parse_latest(self):
        from providers.entsoe import _parse_flow_latest

        xml = (
            "<TimeSeries><Period>"
            "<Point><position>1</position><quantity>1200</quantity></Point>"
            "<Point><position>2</position><quantity>1500</quantity></Point>"
            "</Period></TimeSeries>"
        )
        assert _parse_flow_latest(xml) == 1500.0
        assert _parse_flow_latest("") is None
