"""Tests for carbon-aware dispatcher."""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
import requests

import check_grid
import ledger
import setup_wizard
from providers import (
    AUTO_CLEANEST_ZONES,
    AUTO_ESCAPE_COAL_ZONES,
    AUTO_GREEN_ZONES,
    AUTO_GREEN_ZONES_FULL,
    ESCAPE_COAL_MAPPINGS,
    NEAREST_ZONES_BY_OFFSET,
    PROVIDER_AEMO,
    PROVIDER_CANADA,
    PROVIDER_EIA,
    PROVIDER_ELECTRICITY_MAPS,
    PROVIDER_ENERGY_CHARTS,
    PROVIDER_ENTSOE,
    PROVIDER_ESKOM,
    PROVIDER_GRID_INDIA,
    PROVIDER_ONS_BRAZIL,
    PROVIDER_OPEN_METEO,
    PROVIDER_RTE,
    PROVIDER_TAIWAN,
    PROVIDER_UK,
    _haversine_km,
    _time_priority_score,
    _zone_latlon,
    aemo,
    base,
    canada,
    detect_provider,
    eia,
    electricity_maps,
    entsoe,
    eskom,
    flow_tracing,
    grid_india,
    gridstatus,
    nearest_clean_zones,
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


@pytest.fixture
def github_output(tmp_path, monkeypatch):
    """Point GITHUB_OUTPUT at an empty file and return its path."""
    path = tmp_path / "output.txt"
    path.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(path))
    return path


@pytest.fixture
def step_summary(tmp_path, monkeypatch):
    """Point GITHUB_STEP_SUMMARY at an empty file and return its path."""
    path = tmp_path / "summary.md"
    path.touch()
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
    return path


@pytest.fixture
def ledger_path(tmp_path, monkeypatch):
    """Configure a file ledger at a path that doesn't exist yet."""
    path = tmp_path / "ledger.json"
    monkeypatch.setenv("LEDGER", f"file:{path}")
    return path


def _reset_once_flags():
    check_grid._ledger_recorded = False
    check_grid._budget_summary = None
    check_grid._lifetime_summary = None
    check_grid._sla_summary = None
    check_grid._marginal_done = False
    check_grid._marginal_summary = None
    check_grid._status_badge_done = False
    check_grid._pr_comment_done = False
    check_grid._notify_done = False


@pytest.fixture
def reset_once_flags():
    """Clear the run-once guards and cached summaries before and after a test."""
    _reset_once_flags()
    yield
    _reset_once_flags()


def parse(s):
    return check_grid.parse_zones_input(s)


def outputs_of(mock_output):
    """Collect the set_output(name, value) calls made on a mock into a dict."""
    return {c.args[0]: c.args[1] for c in mock_output.call_args_list}


def main_exit_code():
    """Run main() and return the code it exits with."""
    with pytest.raises(SystemExit) as exc:
        check_grid.main()
    return exc.value.code


def respond(mock_call, response):
    """Make a mocked HTTP call return a response, or raise it when it's an exception."""
    if isinstance(response, Exception):
        mock_call.side_effect = response
    else:
        mock_call.return_value = response


def assert_verdict(result, is_green, intensity):
    """A (is_green, intensity) verdict: is_green is a bool or None, compared by identity."""
    assert result[0] is is_green
    assert result[1] == intensity


_DECREASING = [400, 380, 360, 300, 280, 260]
_SLOT = "2026-03-10T18:00:00+00:00"


class TestParseZonesInput:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            pytest.param("CISO", [("CISO", None)], id="single_zone"),
            pytest.param(
                "CISO, ERCO, PJM",
                [("CISO", None), ("ERCO", None), ("PJM", None)],
                id="multiple_zones",
            ),
            pytest.param(
                "CISO:runner-cal, GB:runner-uk",
                [("CISO", "runner-cal"), ("GB", "runner-uk")],
                id="zones_with_labels",
            ),
            pytest.param(
                "GB:runner-uk, CISO, ERCO:runner-tex",
                [("GB", "runner-uk"), ("CISO", None), ("ERCO", "runner-tex")],
                id="mixed_labels",
            ),
            pytest.param("", [], id="empty_string"),
            pytest.param("CISO,,ERCO,", [("CISO", None), ("ERCO", None)], id="trailing_commas"),
        ],
    )
    def test_explicit_zones(self, raw, expected):
        assert parse(raw) == [{"zone": z, "runner_label": label} for z, label in expected]

    @pytest.mark.parametrize(
        ("raw", "present"),
        [
            # auto:green only includes free-provider zones: US (EIA), UK, AEMO, ONS
            pytest.param("auto:green", {"CISO", "GB-16", "AU-TAS", "BR-S"}, id="auto_green_free"),
            # auto:green:full adds the token-requiring zones
            pytest.param("auto:green:full", {"CISO", "NO-NO1", "CA-QC"}, id="auto_green_full"),
            pytest.param("Auto:Green", {"CISO"}, id="case_insensitive"),
            pytest.param("  auto:green  ", {"CISO"}, id="surrounding_whitespace"),
        ],
    )
    def test_auto_green_presets(self, raw, present):
        result = parse(raw)
        assert len(result) >= 5
        assert present <= {z["zone"] for z in result}


# Free regional providers win over generic ones. DE and FR have keyless real
# sources (Energy-Charts, RTE) preferred over the Open-Meteo estimate when no
# ENTSO-E token is set. NO-NO1 has no national keyless source, so it estimates
_DETECT_CASES = [
    ("GB", "", PROVIDER_UK),
    ("GB-13", "", PROVIDER_UK),
    ("GB-national", "", PROVIDER_UK),
    ("CISO", "", PROVIDER_EIA),
    ("ERCO", "", PROVIDER_EIA),
    ("XX-UNKNOWN", "", PROVIDER_ELECTRICITY_MAPS),
    ("AU-NSW", "", PROVIDER_AEMO),
    ("AU-TAS", "", PROVIDER_AEMO),
    ("AU-VIC", "", PROVIDER_AEMO),
    ("IN-NO", "", PROVIDER_GRID_INDIA),
    ("IN-SO", "", PROVIDER_GRID_INDIA),
    ("IN-EA", "", PROVIDER_GRID_INDIA),
    ("IN-WE", "", PROVIDER_GRID_INDIA),
    ("IN-NE", "", PROVIDER_GRID_INDIA),
    ("BR-S", "", PROVIDER_ONS_BRAZIL),
    ("BR-SE", "", PROVIDER_ONS_BRAZIL),
    ("BR-CS", "", PROVIDER_ONS_BRAZIL),
    ("BR-NE", "", PROVIDER_ONS_BRAZIL),
    ("BR-N", "", PROVIDER_ONS_BRAZIL),
    ("ZA", "", PROVIDER_ESKOM),
    ("CA-ON", "", PROVIDER_CANADA),
    ("CA-AB", "", PROVIDER_CANADA),
    ("CA-QC", "", PROVIDER_CANADA),
    ("TW", "", PROVIDER_TAIWAN),
    ("DE", "", PROVIDER_ENERGY_CHARTS),
    ("FR", "", PROVIDER_RTE),
    ("NO-NO1", "", PROVIDER_OPEN_METEO),
    ("DE", "my-token", PROVIDER_ENTSOE),
    ("FR", "tok", PROVIDER_ENTSOE),
    ("CISO", "tok", PROVIDER_EIA),
]


class TestDetectProvider:
    @pytest.mark.parametrize(
        ("zone", "token", "provider"),
        _DETECT_CASES,
        ids=[f"{z}{'+entsoe_token' if t else ''}->{p}" for z, t, p in _DETECT_CASES],
    )
    def test_zone_routes_to_provider(self, zone, token, provider):
        assert detect_provider(zone, entsoe_token=token) == provider

    @pytest.mark.parametrize(
        ("zone", "wrong"),
        [("IN-NO", PROVIDER_UK), ("BR-S", PROVIDER_EIA)],
        ids=["india_is_not_uk", "brazil_is_not_eia"],
    )
    def test_zone_not_misrouted(self, zone, wrong):
        assert detect_provider(zone) != wrong


class TestApiRequest:
    @pytest.mark.parametrize("token", [None, "my-token"], ids=["no_auth", "auth_token_header"])
    @mock.patch("providers.base._SESSION.get")
    def test_success(self, mock_get, token):
        mock_get.return_value = mock.Mock(status_code=200, json=lambda: {"ok": True})
        assert api_request("https://example.com", token) == {"ok": True}
        assert mock_get.call_args.kwargs.get("headers", {}).get("auth-token") == token

    @mock.patch("providers.base._SESSION.get")
    def test_retries_on_500(self, mock_get):
        fail = mock.Mock(status_code=500, text="Server Error")
        success = mock.Mock(status_code=200, json=lambda: {"ok": True})
        mock_get.side_effect = [fail, success]
        assert api_request("https://example.com") == {"ok": True}
        assert mock_get.call_count == 2

    @mock.patch("providers.base._SESSION.get")
    def test_returns_none_on_all_failures(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=500, text="Server Error")
        assert api_request("https://example.com") is None

    @mock.patch("providers.base._SESSION.get")
    def test_429_honors_retry_after(self, mock_get):
        limited = mock.Mock(status_code=429, text="slow", headers={"Retry-After": "7"})
        success = mock.Mock(status_code=200, json=lambda: {"ok": True})
        mock_get.side_effect = [limited, success]
        with mock.patch("providers.base.time.sleep") as sleep:
            assert api_request("https://example.com") == {"ok": True}
        sleep.assert_called_once_with(7)

    @mock.patch("providers.base._SESSION.get")
    def test_auth_error_no_retry(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=403, text="Forbidden")
        assert api_request("https://example.com") is None
        assert mock_get.call_count == 1

    @mock.patch("providers.base._SESSION.get")
    def test_invalid_json(self, mock_get):
        resp = mock.Mock(status_code=200, text="not json")
        resp.json.side_effect = ValueError("bad")
        mock_get.return_value = resp
        assert api_request("https://example.com") is None


class TestFailureReason:
    """request() classifies why a call failed, for actionable skip reasons."""

    @pytest.mark.parametrize(
        ("response", "reason"),
        [
            pytest.param(
                mock.Mock(status_code=403, text="no"), "auth failed", id="403_auth_failed"
            ),
            pytest.param(
                mock.Mock(status_code=429, text="slow", headers={}),
                "rate limited",
                id="429_rate_limited",
            ),
            pytest.param(requests.RequestException("boom"), "network error", id="network_error"),
        ],
    )
    @mock.patch("providers.base._SESSION.get")
    def test_classifies_failure(self, mock_get, response, reason):
        respond(mock_get, response)
        base.request("https://x")
        assert base.last_failure_reason() == reason

    @mock.patch("providers.base._SESSION.get")
    def test_success_resets_reason(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=200, json=lambda: {"ok": 1})
        base.request("https://x")
        assert base.last_failure_reason() is None

    @mock.patch("check_grid.check_carbon_intensity")
    def test_dispatcher_surfaces_reason(self, mock_check):
        # check_carbon_intensity returns (None, None) and records the reason in
        # the same thread it ran on, exactly as the real request() does. The
        # dispatcher then reads it back thread-locally
        def _fail(*_a, **_k):
            base._set_failure_reason("auth failed")
            return (None, None)

        mock_check.side_effect = _fail
        _zone, _i, _label, skipped = check_grid.check_multiple_zones(
            [{"zone": "CISO", "runner_label": None}], 250
        )
        assert skipped == [("CISO", "auth failed")]


@pytest.mark.usefixtures("cache_env")
class TestRequestCache:
    @pytest.fixture
    def cache_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CARBON_CACHE_DIR", str(tmp_path))
        monkeypatch.setenv("CARBON_CACHE_TTL", "300")

    @staticmethod
    def _backdate(url):
        """Age the cached GET entry past the TTL so the next call can't serve it fresh."""
        path = base._cache_path("GET", url, "json")
        with open(path) as fh:
            entry = json.load(fh)
        entry["ts"] -= 10_000
        with open(path, "w") as fh:
            json.dump(entry, fh)

    @mock.patch("providers.base._SESSION.get")
    def test_caches_get_json_within_ttl(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=200, json=lambda: {"v": 1})
        first = base.request("https://grid.example/intensity")
        second = base.request("https://grid.example/intensity")
        assert first == second == {"v": 1}
        assert mock_get.call_count == 1  # second served from cache

    @mock.patch("providers.base._SESSION.get")
    def test_disabled_by_default(self, mock_get, monkeypatch):
        monkeypatch.delenv("CARBON_CACHE_TTL")
        mock_get.return_value = mock.Mock(status_code=200, json=lambda: {"v": 1})
        base.request("https://grid.example/intensity")
        base.request("https://grid.example/intensity")
        assert mock_get.call_count == 2  # no caching when TTL unset

    @mock.patch("providers.base._SESSION.get")
    def test_expired_entry_refetched(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=200, json=lambda: {"v": 1})
        base.request("https://grid.example/intensity")
        self._backdate("https://grid.example/intensity")
        base.request("https://grid.example/intensity")
        assert mock_get.call_count == 2

    @mock.patch("providers.base._SESSION.post")
    def test_post_not_cached(self, mock_post):
        mock_post.return_value = mock.Mock(status_code=200, json=lambda: {"v": 1})
        base.request("https://grid.example/q", method="POST", json_body={"a": 1})
        base.request("https://grid.example/q", method="POST", json_body={"a": 1})
        assert mock_post.call_count == 2  # writes are never cached

    @mock.patch("providers.base._SESSION.get")
    def test_etag_revalidation_serves_cache_on_304(self, mock_get):
        ok = mock.Mock(status_code=200, json=lambda: {"v": 1}, headers={"ETag": "abc"})
        not_modified = mock.Mock(status_code=304, headers={})
        mock_get.side_effect = [ok, not_modified]
        url = "https://grid.example/intensity"

        assert base.request(url) == {"v": 1}  # 200, cached with ETag
        self._backdate(url)  # stale, so the next call revalidates instead of using TTL

        assert base.request(url) == {"v": 1}  # 304 -> served from cache
        assert mock_get.call_count == 2
        sent = mock_get.call_args.kwargs["headers"]
        assert sent.get("If-None-Match") == "abc"  # conditional request was made

    @mock.patch("providers.base._SESSION.get")
    def test_304_refreshes_freshness(self, mock_get):
        ok = mock.Mock(status_code=200, json=lambda: {"v": 9}, headers={"ETag": "z"})
        not_modified = mock.Mock(status_code=304, headers={})
        mock_get.side_effect = [ok, not_modified]
        url = "https://grid.example/x"

        base.request(url)
        self._backdate(url)

        base.request(url)  # 304 -> bumps ts back to now
        # A third call is within TTL again, so it serves from cache (no 3rd GET)
        assert base.request(url) == {"v": 9}
        assert mock_get.call_count == 2


# ---------------------------------------------------------------------------
# UK Carbon Intensity API tests
# ---------------------------------------------------------------------------


def _uk_points(values, key="from"):
    return [{key: t, "intensity": {"forecast": v}} for t, v in values]


class TestUkCheckCarbonIntensity:
    @pytest.mark.parametrize(
        ("zone", "response", "expected"),
        [
            pytest.param(
                "GB",
                {
                    "data": [
                        {
                            "from": "2026-03-10T00:00Z",
                            "to": "2026-03-10T00:30Z",
                            "intensity": {"forecast": 100, "actual": 95, "index": "low"},
                        }
                    ]
                },
                (True, 100),
                id="national_green",
            ),
            pytest.param(
                "GB",
                {"data": [{"intensity": {"forecast": 400, "actual": 410, "index": "high"}}]},
                (False, 400),
                id="national_dirty",
            ),
            pytest.param(
                "GB-16",
                {"data": [{"data": [{"intensity": {"forecast": 50, "index": "very low"}}]}]},
                (True, 50),
                id="regional_green",
            ),
            pytest.param("GB", None, (None, None), id="api_error"),
            pytest.param("GB", {"data": [{}]}, (None, None), id="malformed_response"),
        ],
    )
    @mock.patch("providers.uk.api_request")
    def test_verdict(self, mock_api, zone, response, expected):
        mock_api.return_value = response
        assert_verdict(uk.check_carbon_intensity(zone, 250), *expected)

    @mock.patch("providers.uk.api_request")
    def test_unknown_zone(self, mock_api):
        assert_verdict(uk.check_carbon_intensity("GB-99", 250), None, None)
        mock_api.assert_not_called()


class TestUkGetForecast:
    @pytest.mark.parametrize(
        ("zone", "response", "expected"),
        [
            pytest.param(
                "GB",
                {"data": _uk_points([("2026-03-10T00:00Z", 300), ("2026-03-10T06:00Z", 120)])},
                ("2026-03-10T06:00Z", 120),
                id="finds_green_window",
            ),
            pytest.param(
                "GB",
                {"data": _uk_points([("2026-03-10T00:00Z", 300), ("2026-03-10T06:00Z", 350)])},
                ("none_in_forecast", None),
                id="no_green_window",
            ),
            pytest.param("GB", None, (None, None), id="api_error"),
            pytest.param(
                "GB-16",
                {
                    "data": {
                        "data": _uk_points([("2026-03-10T12:00Z", 300), ("2026-03-10T14:00Z", 90)])
                    }
                },
                ("2026-03-10T14:00Z", 90),
                id="regional_finds_window",
            ),
            pytest.param(
                "GB-16",
                {"data": {"data": [{"oops": True}]}},
                (None, None),
                id="regional_malformed_response",
            ),
        ],
    )
    @mock.patch("providers.uk.api_request")
    def test_forecast(self, mock_api, zone, response, expected):
        mock_api.return_value = response
        assert uk.get_forecast(zone, 200) == expected

    def test_unknown_zone(self):
        assert uk.get_forecast("GB-999", 200) == (None, None)


class TestUkGetHistoryTrend:
    @pytest.mark.parametrize(
        ("zone", "response", "expected"),
        [
            pytest.param(
                "GB",
                {"data": [{"intensity": {"forecast": v}} for v in _DECREASING]},
                "decreasing",
                id="decreasing",
            ),
            pytest.param("GB", None, None, id="api_error"),
            # Regional zones nest the points under data.data
            pytest.param(
                "GB-16",
                {"data": {"data": [{"intensity": {"forecast": v}} for v in _DECREASING]}},
                "decreasing",
                id="regional_decreasing",
            ),
        ],
    )
    @mock.patch("providers.uk.api_request")
    def test_trend(self, mock_api, zone, response, expected):
        mock_api.return_value = response
        assert uk.get_history_trend(zone) == expected

    def test_unknown_zone(self):
        assert uk.get_history_trend("GB-999") is None


# ---------------------------------------------------------------------------
# EIA tests
# ---------------------------------------------------------------------------


def _eia_response(respondent, **mw):
    rows = [
        {"period": "2026-03-09T06", "respondent": respondent, "fueltype": fuel, "value": value}
        for fuel, value in mw.items()
    ]
    return {"response": {"data": rows}}


class TestEiaFuelMixToIntensity:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            pytest.param([{"fueltype": "NG", "value": 100}], 490, id="all_gas"),
            # IPCC AR5 wind onshore = 11, renewables are not treated as zero
            pytest.param([{"fueltype": "WND", "value": 100}], 11, id="all_wind"),
            # (50 * 490 + 50 * 11) / 100 = 250.5 -> 250
            pytest.param(
                [{"fueltype": "NG", "value": 50}, {"fueltype": "WND", "value": 50}],
                250,
                id="mixed",
            ),
            # Negative (consuming) values are ignored
            pytest.param(
                [{"fueltype": "NG", "value": 100}, {"fueltype": "SUN", "value": -10}],
                490,
                id="negative_values_ignored",
            ),
            pytest.param(
                [{"fueltype": "NG", "value": 100}, {"fueltype": "SUN", "value": None}],
                490,
                id="none_values_ignored",
            ),
            pytest.param([], None, id="empty_data"),
            pytest.param([{"fueltype": "NG", "value": 0}], None, id="all_zero"),
            # Battery storage is excluded from the mix rather than counted as zero-carbon
            pytest.param(
                [{"fueltype": "COL", "value": 100}, {"fueltype": "BAT", "value": 100}],
                820,
                id="battery_storage_excluded",
            ),
        ],
    )
    def test_intensity(self, data, expected):
        assert eia._fuel_mix_to_intensity(data) == expected

    def test_unknown_fuel_warns_and_falls_back(self, capsys):
        # an unknown EIA fuel code must warn and still apply the fallback
        # factor so the calc proceeds rather than silently using zero
        data = [{"fueltype": "XYZ", "value": 100}]
        assert eia._fuel_mix_to_intensity(data) == base.DEFAULT_FUEL_FACTOR
        out = capsys.readouterr().out
        assert "::warning::" in out
        assert "XYZ" in out


class TestEiaFuelMixSeries:
    @mock.patch("providers.eia.api_request")
    def test_series_totals_oldest_first(self, mock_api):
        # Two periods (API returns newest first), but series should be oldest-first
        # with (generation, generation-weighted co2) per period
        mock_api.return_value = {
            "response": {
                "data": [
                    {"period": "2026-03-09T07", "fueltype": "NG", "value": 100},
                    {"period": "2026-03-09T07", "fueltype": "WND", "value": 100},
                    {"period": "2026-03-09T06", "fueltype": "NG", "value": 50},
                    {"period": "2026-03-09T06", "fueltype": "WND", "value": 100},
                ]
            }
        }
        series = eia.fuel_mix_series("CISO")
        # Oldest (06): gen 150, co2 50*490 + 100*11 = 25600
        # Newest (07): gen 200, co2 100*490 + 100*11 = 50100
        assert series == [(150.0, 25600.0), (200.0, 50100.0)]

    @mock.patch("providers.eia.api_request", return_value=None)
    def test_empty_on_no_data(self, _mock_api):
        assert eia.fuel_mix_series("CISO") == []


class TestEiaCheckCarbonIntensity:
    @pytest.mark.parametrize(
        ("zone", "response", "expected"),
        [
            # wind 11, solar 48, gas 490: (500*11 + 300*48 + 100*490) / 900
            # = (5500 + 14400 + 49000) / 900 = 68900/900 = 76.6 -> 77
            pytest.param(
                "CISO", _eia_response("CISO", WND=500, SUN=300, NG=100), (True, 77), id="green_grid"
            ),
            # (500*820 + 500*490) / 1000 = 655
            pytest.param(
                "ERCO", _eia_response("ERCO", COL=500, NG=500), (False, 655), id="dirty_grid"
            ),
            pytest.param("CISO", None, (None, None), id="api_error"),
            pytest.param("CISO", {"response": {"data": []}}, (None, None), id="empty_data"),
        ],
    )
    @mock.patch("providers.eia.api_request")
    def test_verdict(self, mock_api, zone, response, expected):
        mock_api.return_value = response
        assert_verdict(eia.check_carbon_intensity(zone, 250), *expected)

    @pytest.mark.parametrize(
        ("key", "in_url", "not_in_url"),
        [("", "DEMO_KEY", None), ("my-key", "my-key", "DEMO_KEY")],
        ids=["demo_key_by_default", "custom_key"],
    )
    @mock.patch("providers.eia.api_request")
    def test_api_key_in_url(self, mock_api, key, in_url, not_in_url):
        mock_api.return_value = {"response": {"data": []}}
        eia.check_carbon_intensity("CISO", 250, eia_api_key=key)
        call_url = mock_api.call_args[0][0]
        assert in_url in call_url
        assert not_in_url is None or not_in_url not in call_url


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
        assert eia.get_history_trend("CISO") == "decreasing"

    @mock.patch("providers.eia.api_request")
    def test_api_error(self, mock_api):
        mock_api.return_value = None
        assert eia.get_history_trend("CISO") is None


# ---------------------------------------------------------------------------
# Electricity Maps tests
# ---------------------------------------------------------------------------


class TestElectricityMapsCheckCarbonIntensity:
    @pytest.mark.parametrize(
        ("response", "expected"),
        [
            pytest.param({"carbonIntensity": 85.3}, (True, 85), id="green"),
            pytest.param({"carbonIntensity": 450.7}, (False, 451), id="dirty"),
            pytest.param(None, (None, None), id="api_error"),
            pytest.param({"zone": "DE"}, (None, None), id="no_intensity_in_response"),
        ],
    )
    @mock.patch("providers.electricity_maps.api_request_with_header")
    def test_verdict(self, mock_api, response, expected):
        mock_api.return_value = response
        assert_verdict(electricity_maps.check_carbon_intensity("DE", 200, "key"), *expected)

    def test_no_api_key(self):
        assert_verdict(electricity_maps.check_carbon_intensity("DE", 200, ""), None, None)


class TestElectricityMapsGetForecast:
    @pytest.mark.parametrize(
        ("response", "expected"),
        [
            pytest.param(
                {
                    "forecast": [
                        {"carbonIntensity": 300, "datetime": "2026-03-10T12:00Z"},
                        {"carbonIntensity": 80, "datetime": "2026-03-10T14:00Z"},
                    ]
                },
                ("2026-03-10T14:00Z", 80),
                id="finds_green_window",
            ),
            pytest.param(
                {
                    "forecast": [
                        {"carbonIntensity": 300, "datetime": "2026-03-10T12:00Z"},
                        {"carbonIntensity": 350, "datetime": "2026-03-10T14:00Z"},
                    ]
                },
                ("none_in_forecast", None),
                id="no_green_window",
            ),
            pytest.param(None, (None, None), id="api_error"),
        ],
    )
    @mock.patch("providers.electricity_maps.api_request_with_header")
    def test_forecast(self, mock_api, response, expected):
        mock_api.return_value = response
        assert electricity_maps.get_forecast("DE", 200, "key") == expected

    def test_no_api_key(self):
        assert electricity_maps.get_forecast("DE", 200, "") == (None, None)


class TestElectricityMapsGetHistoryTrend:
    @pytest.mark.parametrize(
        ("response", "expected"),
        [
            pytest.param(
                {"history": [{"carbonIntensity": v} for v in _DECREASING]},
                "decreasing",
                id="decreasing",
            ),
            pytest.param(None, None, id="api_error"),
        ],
    )
    @mock.patch("providers.electricity_maps.api_request_with_header")
    def test_trend(self, mock_api, response, expected):
        mock_api.return_value = response
        assert electricity_maps.get_history_trend("DE", "key") == expected

    def test_no_api_key(self):
        assert electricity_maps.get_history_trend("DE", "") is None


# ---------------------------------------------------------------------------
# GridStatus.io forecast tests
# ---------------------------------------------------------------------------


class TestGridstatusApiRequest:
    @mock.patch("providers.base._SESSION.get")
    def test_success(self, mock_get):
        mock_get.return_value = mock.Mock(
            status_code=200,
            json=lambda: {"data": [{"interval_start_utc": "2026-03-10T12:00:00+00:00"}]},
        )
        result = api_request_with_header("https://api.gridstatus.io/v1/test", "x-api-key", "my-key")
        assert result is not None
        call_headers = mock_get.call_args[1].get("headers", {})
        assert call_headers.get("x-api-key") == "my-key"

    @mock.patch("providers.base._SESSION.get")
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
            _SLOT: {"solar_mw": 8000, "wind_mw": 2000},
        }
        mock_load.return_value = {"2026-03-10T12:00:00+00:00": 10000, _SLOT: 10000}
        assert gridstatus.get_forecast("CISO", 250, "key") == (_SLOT, 0)

    @mock.patch("providers.gridstatus._get_load_forecast")
    @mock.patch("providers.gridstatus._get_renewable_forecast")
    def test_no_green_window(self, mock_renew, mock_load):
        mock_renew.return_value = {"2026-03-10T12:00:00+00:00": {"solar_mw": 100, "wind_mw": 50}}
        mock_load.return_value = {"2026-03-10T12:00:00+00:00": 10000}
        assert gridstatus.get_forecast("CISO", 100, "key") == ("none_in_forecast", None)

    @mock.patch("providers.gridstatus._get_renewable_forecast")
    def test_no_renewable_data(self, mock_renew):
        mock_renew.return_value = {}
        assert gridstatus.get_forecast("CISO", 250, "key") == (None, None)

    def test_unsupported_zone(self):
        assert gridstatus.get_forecast("BPAT", 250, "key") == (None, None)

    @mock.patch("providers.gridstatus._get_load_forecast")
    @mock.patch("providers.gridstatus._get_renewable_forecast")
    def test_no_key_returns_none(self, mock_renew, _mock_load):
        """get_forecast returns None for US zones without GridStatus key."""
        assert check_grid.get_forecast("CISO", 250, PROVIDER_EIA, "") == (None, None)
        mock_renew.assert_not_called()

    @mock.patch("providers.gridstatus._get_load_forecast")
    @mock.patch("providers.gridstatus._get_renewable_forecast")
    def test_get_forecast_with_key(self, mock_renew, mock_load):
        """get_forecast calls gridstatus when key is provided."""
        mock_renew.return_value = {_SLOT: {"solar_mw": 9000, "wind_mw": 1000}}
        mock_load.return_value = {_SLOT: 10000}
        result = check_grid.get_forecast("CISO", 250, PROVIDER_EIA, "my-gridstatus-key")
        assert result == (_SLOT, 0)


class TestGridstatusRenewableForecast:
    @mock.patch("providers.gridstatus._query_dataset")
    def test_single_dataset_with_location_filter(self, mock_query):
        """CAISO-style: single dataset with location filter."""
        mock_query.return_value = [
            {"interval_start_utc": _SLOT, "location": "CAISO", "solar_mw": 8000, "wind_mw": 1500},
            {"interval_start_utc": _SLOT, "location": "NP15", "solar_mw": 2000, "wind_mw": 500},
        ]
        iso_config = gridstatus.GRIDSTATUS_ISO_MAP["CISO"]
        result = gridstatus._get_renewable_forecast(iso_config, "key", "2026-03-10")
        assert result[_SLOT] == {"solar_mw": 8000, "wind_mw": 1500}

    @mock.patch("providers.gridstatus._query_dataset")
    def test_separate_solar_wind_datasets(self, mock_query):
        """PJM-style: separate solar and wind datasets."""
        mock_query.side_effect = [
            [{"interval_start_utc": _SLOT, "solar_forecast": 3000}],
            [{"interval_start_utc": _SLOT, "wind_forecast": 2000}],
        ]
        iso_config = gridstatus.GRIDSTATUS_ISO_MAP["PJM"]
        result = gridstatus._get_renewable_forecast(iso_config, "key", "2026-03-10")
        assert result[_SLOT]["solar_mw"] == 3000
        assert result[_SLOT]["wind_mw"] == 2000

    @mock.patch("providers.gridstatus._query_dataset")
    def test_sum_columns_branch(self, mock_query):
        """ISNE-style sum_columns: sum all numeric forecast columns per row."""
        iso = next(
            (cfg for cfg in gridstatus.GRIDSTATUS_ISO_MAP.values() if cfg.get("sum_columns")), None
        )
        assert iso is not None, "expected at least one sum_columns ISO"
        mock_query.side_effect = [
            # solar dataset: two zones summed
            [{"interval_start_utc": _SLOT, "publish_time_utc": "x", "zone_a": 1000, "zone_b": 500}],
            # wind dataset
            [{"interval_start_utc": _SLOT, "zone_a": 800}],
        ]
        slot = gridstatus._get_renewable_forecast(iso, "key", "2026-03-10")[_SLOT]
        assert slot["solar_mw"] == 1500  # 1000 + 500, publish_/interval_ excluded
        assert slot["wind_mw"] == 800

    @mock.patch("providers.gridstatus.api_request_with_header")
    def test_query_dataset_none_on_failure(self, mock_req):
        mock_req.return_value = None
        assert gridstatus._query_dataset("ds", "key", "2026-03-10") == []

    @mock.patch("providers.gridstatus._query_dataset")
    def test_load_forecast_parsing(self, mock_query):
        mock_query.return_value = [
            {"interval_start_utc": _SLOT, "load_forecast": 12345},
            {"interval_start_utc": None, "load_forecast": 999},  # skipped (no ts)
        ]
        iso = gridstatus.GRIDSTATUS_ISO_MAP["CISO"]
        assert gridstatus._get_load_forecast(iso, "key", "2026-03-10") == {_SLOT: 12345.0}

    def test_load_forecast_no_dataset(self):
        # ERCO has load_dataset=None
        iso = gridstatus.GRIDSTATUS_ISO_MAP["ERCO"]
        assert gridstatus._get_load_forecast(iso, "key", "2026-03-10") is None


# ---------------------------------------------------------------------------
# ONS Brazil provider tests
# ---------------------------------------------------------------------------


class TestOnsBrazilProvider:
    @pytest.mark.parametrize(
        ("balance", "expected"),
        [
            pytest.param(None, (None, None), id="api_unavailable"),
            pytest.param({"unexpected": "shape"}, (None, None), id="unparseable_response"),
            # hydro 5000*24 + thermal 1000*650 = 120000+650000 = 770000/6000 = 128
            pytest.param(
                {"sul": {"geracao": {"total": 6000, "hidraulica": 5000, "termica": 1000}}},
                (True, 128),
                id="success",
            ),
        ],
    )
    @mock.patch("providers.ons_brazil._fetch_energy_balance")
    def test_check(self, mock_fetch, balance, expected):
        mock_fetch.return_value = balance
        assert_verdict(ons_brazil.check_carbon_intensity("BR-S", 250), *expected)

    @pytest.mark.parametrize("zone", ["BR-XX", "XX"])
    def test_check_unknown_zone(self, zone):
        assert_verdict(ons_brazil.check_carbon_intensity(zone, 250), None, None)

    def test_forecast_offpeak_already_green_returns_none(self):
        # Pin the clock to an off-peak hour (10:00 BRT = 13:00 UTC) so the test
        # is deterministic regardless of when CI runs. Off-peak + high threshold
        # means the grid is already green, so no future window is needed
        fixed = datetime(2026, 3, 10, 13, 0, tzinfo=timezone.utc)
        with mock.patch("providers.ons_brazil.datetime") as mock_dt:
            # Only now() is pinned, so real datetime construction still works
            mock_dt.now.return_value = fixed
            assert ons_brazil.get_forecast("BR-S", 500) == (None, None)

    def test_calculate_intensity_hydro_dominant(self):
        gen = {"hidraulica": 7000, "termica": 1000, "eolica": 1500, "solar": 500}
        intensity = base.mix_to_intensity(gen, ons_brazil.BRAZIL_EMISSION_FACTORS, substring=True)
        assert intensity is not None
        assert intensity < 200  # Hydro-dominant grid should be clean

    def test_calculate_intensity_empty(self):
        assert base.mix_to_intensity({}, ons_brazil.BRAZIL_EMISSION_FACTORS, substring=True) is None

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
        # the aggregate "total" and zero-valued sources are dropped
        assert ons_brazil._parse_energy_balance(data, "sul") == {
            "hidraulica": 5000.0,
            "termica": 2000.0,
        }

    def test_parse_energy_balance_missing_region(self):
        data = {"sul": {"geracao": {"hidraulica": 5000.0}}}
        assert ons_brazil._parse_energy_balance(data, "nordeste") is None

    def test_parse_energy_balance_none(self):
        assert ons_brazil._parse_energy_balance(None, "sul") is None

    def test_trend_returns_none(self):
        assert ons_brazil.get_history_trend("BR-S") is None


# ---------------------------------------------------------------------------
# Provider-agnostic tests
# ---------------------------------------------------------------------------


class TestCanonicalFuelFactors:
    """Every provider sources its factors from base.FUEL_FACTORS, so shared
    fuels must agree across providers and match the canonical table."""

    def test_shared_fuels_agree_across_providers(self):
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
        # The unknown-fuel fallback is centralized in base.mix_to_intensity,
        # so only base and the providers that still own a fallback expose it
        assert base.FUEL_FACTORS["other"] == base.DEFAULT_FUEL_FACTOR
        assert base.FUEL_FACTORS["other"] == entsoe.DEFAULT_FUEL_FACTOR

    def test_every_provider_value_is_in_canonical_table(self):
        """Every value in every provider's factor dict must come from the
        canonical FUEL_FACTORS, proving none reintroduced a bare number."""
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
    @pytest.mark.parametrize(
        ("points", "trend"),
        [
            pytest.param(_DECREASING, "decreasing", id="decreasing"),
            pytest.param([100, 120, 130, 200, 250, 300], "increasing", id="increasing"),
            pytest.param([200] * 6, "stable", id="stable"),
            pytest.param([100, 200], None, id="insufficient_data"),
        ],
    )
    def test_trend(self, points, trend):
        assert compute_trend(points) == trend


class TestCheckMultipleZones:
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.detect_provider", return_value=PROVIDER_EIA)
    def test_picks_greenest(self, _mock_detect, mock_check):
        # Map by zone: zones are checked concurrently, so the
        # nth call is not guaranteed to be the nth zone
        by_zone = {"CISO": (True, 200), "NYIS": (True, 50), "ERCO": (False, 400)}
        mock_check.side_effect = lambda zone, *a, **k: by_zone[zone]
        zones = [
            {"zone": "CISO", "runner_label": "label-a"},
            {"zone": "NYIS", "runner_label": "label-b"},
            {"zone": "ERCO", "runner_label": "label-c"},
        ]
        assert check_grid.check_multiple_zones(zones, 250) == ("NYIS", 50, "label-b", [])

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.detect_provider", return_value=PROVIDER_EIA)
    def test_all_dirty(self, _mock_detect, mock_check):
        mock_check.return_value = (False, 400)
        zone, _intensity, _label, _skipped = check_grid.check_multiple_zones(
            [{"zone": "ERCO"}, {"zone": "PJM"}], 250
        )
        assert zone is None

    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.detect_provider", return_value=PROVIDER_EIA)
    def test_all_errors(self, _mock_detect, mock_check):
        mock_check.return_value = (None, None)
        zone, _intensity, _label, skipped = check_grid.check_multiple_zones(
            [{"zone": "CISO"}, {"zone": "ERCO"}], 250
        )
        assert zone is None
        assert len(skipped) == 2

    def test_skips_emaps_zones_without_token_or_coordinates(self):
        """Zones needing Electricity Maps token with no Open-Meteo fallback are skipped."""
        # Use fake zones that have no coordinates and no free provider
        zones = [{"zone": "XX-FAKE1"}, {"zone": "XX-FAKE2"}]
        zone, _intensity, _label, skipped = check_grid.check_multiple_zones(
            zones, 250, emaps_api_key=""
        )
        assert zone is None
        assert skipped == [
            ("XX-FAKE1", "no electricity_maps_token"),
            ("XX-FAKE2", "no electricity_maps_token"),
        ]

    @mock.patch("check_grid.check_carbon_intensity")
    def test_emaps_zones_fallback_to_open_meteo(self, mock_check):
        """Zones with Open-Meteo coordinates fall back instead of being skipped."""
        mock_check.return_value = (True, 200)
        zones = [{"zone": "DE", "runner_label": "eu"}]
        zone, _intensity, _label, skipped = check_grid.check_multiple_zones(
            zones, 250, emaps_api_key=""
        )
        # DE has Open-Meteo coordinates, so it is checked rather than skipped
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
        zone, intensity, _label, skipped = check_grid.check_multiple_zones(
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

    @pytest.mark.parametrize(
        "failure",
        [
            pytest.param(mock.Mock(status_code=422, text="Validation Failed"), id="http_422"),
            pytest.param(check_grid.requests.RequestException("timeout"), id="network_error"),
        ],
    )
    @mock.patch("check_grid.requests.post")
    def test_failure_exits(self, mock_post, failure):
        respond(mock_post, failure)
        with pytest.raises(SystemExit) as exc_info:
            check_grid.trigger_workflow("owner/repo", "build.yml", "token", "main")
        assert exc_info.value.code == 1


class TestSetOutput:
    def test_writes_to_github_output(self, github_output):
        check_grid.set_output("grid_clean", "true")
        assert "grid_clean=true" in github_output.read_text()


class TestGetRequiredEnv:
    @pytest.mark.parametrize("value", [None, ""], ids=["missing", "empty"])
    def test_missing_or_empty_var_exits(self, monkeypatch, value):
        if value is None:
            monkeypatch.delenv("REQUIRED_VAR_TEST", raising=False)
        else:
            monkeypatch.setenv("REQUIRED_VAR_TEST", value)
        with pytest.raises(SystemExit) as exc_info:
            check_grid.get_required_env("REQUIRED_VAR_TEST")
        assert exc_info.value.code == 1

    def test_present_var_returns(self, monkeypatch):
        monkeypatch.setenv("PRESENT_VAR_TEST", "value123")
        assert check_grid.get_required_env("PRESENT_VAR_TEST") == "value123"


class TestHandleDirtyGrid:
    # get_forecast is called as (zone, max_carbon, provider, gridstatus_key, emaps_key,
    # entsoe_token, eia_key). Free forecast providers (UK, Electricity Maps) are
    # queried even without enable_forecast. EIA needs a GridStatus key
    @pytest.mark.parametrize(
        ("zone", "intensity", "kwargs", "trend", "forecast", "expected", "absent", "forecast_call"),
        [
            pytest.param(
                "GB",
                400,
                {"enable_forecast": False},
                "decreasing",
                ("2026-03-10T06:00Z", 120),
                {
                    "grid_clean": "false",
                    "carbon_intensity": "400",
                    "intensity_trend": "decreasing",
                    "forecast_green_at": "2026-03-10T06:00Z",
                },
                (),
                ("GB", 250, PROVIDER_UK, "", "", "", ""),
                id="uk_forecast_without_enable_forecast",
            ),
            pytest.param(
                "CISO",
                400,
                {"enable_forecast": True},
                "increasing",
                (None, None),
                {"grid_clean": "false", "intensity_trend": "increasing"},
                ("forecast_green_at",),
                None,
                id="eia_no_forecast_without_key",
            ),
            pytest.param(
                "CISO",
                400,
                {"enable_forecast": True, "gridstatus_api_key": "gs-key"},
                "decreasing",
                (_SLOT, 50),
                {"forecast_green_at": _SLOT, "forecast_intensity": "50"},
                (),
                ("CISO", 250, PROVIDER_EIA, "gs-key", "", "", ""),
                id="eia_forecast_with_gridstatus_key",
            ),
            pytest.param(
                "GB",
                None,
                {"enable_forecast": False},
                None,
                (None, None),
                {"carbon_intensity": "unknown"},
                (),
                None,
                id="unknown_intensity",
            ),
            pytest.param(
                "GB",
                400,
                {"enable_forecast": False},
                "stable",
                ("none_in_forecast", None),
                {"forecast_green_at": "none_in_forecast"},
                ("forecast_intensity",),
                None,
                id="no_green_in_forecast",
            ),
            pytest.param(
                "JP",
                400,
                {"enable_forecast": False, "emaps_api_key": "em-key"},
                "stable",
                ("2026-03-10T14:00Z", 90),
                {"forecast_green_at": "2026-03-10T14:00Z"},
                (),
                ("JP", 250, detect_provider("JP"), "", "em-key", "", ""),
                id="electricity_maps_forecast_without_enable_forecast",
            ),
            pytest.param(
                "GB",
                400,
                {"enable_forecast": False},
                "decreasing",
                ("2026-03-10T14:00Z", 90),
                {},
                (),
                None,
                id="returns_trend_and_forecast",
            ),
        ],
    )
    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.get_history_trend")
    @mock.patch("check_grid.set_output")
    def test_outputs(
        self,
        mock_output,
        mock_trend,
        mock_forecast,
        zone,
        intensity,
        kwargs,
        trend,
        forecast,
        expected,
        absent,
        forecast_call,
    ):
        mock_trend.return_value = trend
        mock_forecast.return_value = forecast

        result = check_grid.handle_dirty_grid(zone, 250, intensity, **kwargs)

        assert result == (trend, *forecast)
        out = outputs_of(mock_output)
        assert {k: out[k] for k in expected} == expected
        for key in absent:
            assert key not in out
        if forecast_call is not None:
            mock_forecast.assert_called_once_with(*forecast_call)


class TestWriteJobSummary:
    def test_writes_summary_green(self, step_summary):
        check_grid.write_job_summary("CISO", 45, True, 200)
        content = step_summary.read_text()
        assert "Carbon-Aware Dispatcher" in content
        assert "CISO" in content
        assert "45" in content
        assert "clean" in content.lower()

    def test_writes_summary_dirty_with_forecast(self, step_summary):
        check_grid.write_job_summary(
            "PJM",
            380,
            False,
            200,
            trend="decreasing",
            forecast_at="2026-03-10T14:00Z",
            forecast_intensity=150,
        )
        content = step_summary.read_text()
        assert "dirty" in content.lower()
        assert "380" in content
        assert "decreasing" in content
        assert "2026-03-10T14:00Z" in content

    def test_writes_summary_with_skipped_zones(self, step_summary):
        check_grid.write_job_summary(
            "CISO", 100, True, 200, skipped=[("DE", "no electricity_maps_token")]
        )
        content = step_summary.read_text()
        assert "DE" in content
        assert "no electricity_maps_token" in content

    def test_no_summary_without_env(self):
        """Does nothing if GITHUB_STEP_SUMMARY is not set."""
        os.environ.pop("GITHUB_STEP_SUMMARY", None)
        # Should not raise
        check_grid.write_job_summary("CISO", 45, True, 200)

    def test_summary_dry_run_banner(self, step_summary):
        check_grid.write_job_summary("AU-NSW", 400, False, 250, dry_run=True)
        content = step_summary.read_text()
        assert "Report-only" in content
        assert "would defer" in content

    def test_summary_includes_co2_saved(self, step_summary):
        check_grid.write_job_summary("CISO", 50, True, 250, co2_saved=5.0)
        content = step_summary.read_text()
        assert "Saved vs global avg" in content
        assert "5 g (benchmark)" in content

    @pytest.mark.usefixtures("reset_once_flags")
    def test_summary_includes_lifetime_budget_and_marginal(
        self, step_summary, github_output, ledger_path, monkeypatch
    ):
        monkeypatch.setenv("MONTHLY_BUDGET_GRAMS", "1000")
        check_grid.record_lifetime_savings(100, emitted_grams=100)
        check_grid._marginal_summary = {
            "region": "CAISO_NORTH",
            "percentile": 20,
            "clean": True,
            "max_pct": 33,
        }
        check_grid.write_job_summary("CISO", 50, True, 250)
        content = step_summary.read_text()
        assert "Lifetime CO2 Saved" in content
        assert "**Carbon Budget** | 100 / 1000 gCO2eq this month (10%, ok)" in content
        assert "**Marginal (CAISO_NORTH)** | 20th percentile MOER (clean)" in content

    def test_heuristic_forecast_is_labeled(self, step_summary):
        check_grid.write_job_summary(
            "ZA",
            700,
            False,
            250,
            forecast_at="2026-03-10T03:00Z",
            forecast_intensity=650,
            forecast_heuristic=True,
        )
        content = step_summary.read_text()
        assert "Next Green Window (estimated)" in content
        assert "650 gCO2eq/kWh (estimate)" in content

    def test_real_forecast_is_not_labeled_estimate(self, step_summary):
        check_grid.write_job_summary(
            "GB",
            300,
            False,
            250,
            forecast_at="2026-03-10T14:00Z",
            forecast_intensity=90,
            forecast_heuristic=False,
        )
        content = step_summary.read_text()
        assert "**Next Green Window**" in content
        assert "(estimated)" not in content
        assert "(estimate)" not in content

    def test_summary_sets_tier_output(self, github_output, step_summary, monkeypatch):
        monkeypatch.setenv("TIER_THRESHOLDS", "120,280")
        check_grid.write_job_summary("CISO", 90, True, 250)
        out = github_output.read_text()
        assert "carbon_tier=green" in out
        assert "carbon_tier_reason=" in out
        assert "Carbon Tier" in step_summary.read_text()


class TestSmartWaitSingle:
    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid._time.sleep")
    def test_becomes_green_after_wait(self, mock_sleep, mock_check, mock_forecast):
        """Grid goes green on second check."""
        mock_check.return_value = (True, 100)
        mock_forecast.return_value = (None, None)

        is_green, intensity, _waited = check_grid.smart_wait_single("CISO", 250, 10, PROVIDER_EIA)
        assert is_green is True
        assert intensity == 100
        mock_sleep.assert_called_once()

    @mock.patch("check_grid.get_forecast")
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid._time.sleep")
    @mock.patch("check_grid._time.time")
    def test_stays_dirty_after_max_wait(self, mock_time, _mock_sleep, mock_check, mock_forecast):
        """Grid stays dirty, so the wait gives up once max_wait is exceeded."""
        # start, loop check, loop check (past deadline), final
        mock_time.side_effect = [0, 0, 601, 601]
        mock_check.return_value = (False, 400)
        mock_forecast.return_value = (None, None)

        is_green, intensity, _waited = check_grid.smart_wait_single("CISO", 250, 10, PROVIDER_EIA)
        assert is_green is False
        assert intensity == 400


class TestSmartWaitMulti:
    @mock.patch("check_grid.check_multiple_zones")
    @mock.patch("check_grid._time.sleep")
    def test_zone_goes_green(self, mock_sleep, mock_multi):
        """A zone becomes green during wait."""
        mock_multi.return_value = ("CISO", 50, "us-west", [])

        zone, intensity, _label, _waited, _skipped = check_grid.smart_wait_multi(
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
    def test_inline_mode_green(self, _mock_summary, mock_output, mock_check):
        """Inline mode sets outputs but doesn't dispatch."""
        mock_check.return_value = (True, 50)

        os.environ.update(GRID_ZONE="GB", WORKFLOW_ID="")
        os.environ.pop("GITHUB_TOKEN", None)
        os.environ.pop("TARGET_REPO", None)

        # Should not raise (no required env check for token/repo)
        check_grid.main()

        out = outputs_of(mock_output)
        assert out["grid_clean"] == "true"
        assert out["carbon_intensity"] == "50"


@mock.patch("check_grid.check_carbon_intensity")
@mock.patch("check_grid.set_output")
@mock.patch("check_grid.write_job_summary")
class TestDryRun:
    """Report-only mode never gates the build but reports the real verdict."""

    def test_dirty_grid_does_not_gate(self, _mock_summary, mock_output, mock_check):
        # Single dirty zone, but dry_run must keep grid_clean true and exit 0
        mock_check.return_value = (False, 400)
        os.environ.update(GRID_ZONES="AU-NSW", MAX_CARBON="250", DRY_RUN="true", WORKFLOW_ID="")

        assert main_exit_code() == 0

        out = outputs_of(mock_output)
        assert out["grid_clean"] == "true"  # build is never blocked
        assert out["would_defer"] == "true"  # but the verdict is exposed
        assert out["dry_run"] == "true"

    def test_clean_grid_reports_dispatch(self, _mock_summary, mock_output, mock_check):
        mock_check.return_value = (True, 80)
        os.environ.update(GRID_ZONES="GB", MAX_CARBON="250", DRY_RUN="true", WORKFLOW_ID="")

        assert main_exit_code() == 0

        out = outputs_of(mock_output)
        assert out["grid_clean"] == "true"
        assert out["would_defer"] == "false"

    @mock.patch("check_grid.trigger_workflow")
    def test_never_dispatches(self, mock_trigger, _mock_summary, _mock_output, mock_check):
        # Even in dispatch mode (workflow_id set), dry_run must not trigger
        mock_check.return_value = (True, 80)
        os.environ.update(
            GRID_ZONES="GB",
            DRY_RUN="true",
            WORKFLOW_ID="heavy.yml",
            GITHUB_TOKEN="tok",
            TARGET_REPO="owner/repo",
        )

        main_exit_code()
        mock_trigger.assert_not_called()

    def test_never_fails_build_even_with_fail_on_api_error(
        self, _mock_summary, _mock_output, mock_check
    ):
        # dry_run must exit 0 even when every zone errors AND fail_on_api_error
        # is set: report-only never breaks the build
        mock_check.return_value = (None, None)
        os.environ.update(
            GRID_ZONES="GB,AU-NSW", DRY_RUN="true", FAIL_ON_API_ERROR="true", WORKFLOW_ID=""
        )

        assert main_exit_code() == 0


# ---------------------------------------------------------------------------
# Runner provider tests
# ---------------------------------------------------------------------------


class TestCloudRegionMapping:
    @pytest.mark.parametrize(
        ("zone", "region"),
        [
            ("CISO", "us-west-1"),
            ("BPAT", "us-west-2"),
            ("PJM", "us-east-1"),
            ("ERCO", "us-east-2"),
            ("GB", "eu-west-2"),
            ("GB-16", "eu-west-2"),
            ("NO-NO1", "eu-north-1"),
            ("FR", "eu-west-3"),
            ("DE", "eu-central-1"),
            ("CA-QC", "ca-central-1"),
            ("JP-TK", "ap-northeast-1"),
            ("AU-NSW", "ap-southeast-2"),
            ("SG", "ap-southeast-1"),
            ("BR-CS", "sa-east-1"),
            ("UNKNOWN-ZONE", "us-east-1"),  # default
        ],
    )
    def test_aws(self, zone, region):
        assert get_cloud_region(zone) == region

    @pytest.mark.parametrize(
        ("zone", "region"),
        [
            ("CISO", "us-west1"),
            ("PJM", "us-east4"),
            ("ERCO", "us-south1"),
            ("DE", "europe-west3"),
            ("FR", "europe-west9"),
            ("NO-NO1", "europe-north1"),
            ("JP-TK", "asia-northeast1"),
            ("AU-NSW", "australia-southeast1"),
            ("IN-NO", "asia-south1"),
            ("BR-S", "southamerica-east1"),
            ("UNKNOWN-ZONE", "us-central1"),  # default
        ],
    )
    def test_gcp(self, zone, region):
        assert get_gcp_region(zone) == region

    @pytest.mark.parametrize(
        ("zone", "region"),
        [
            ("CISO", "westus2"),
            ("PJM", "eastus"),
            ("ERCO", "southcentralus"),
            ("DE", "germanywestcentral"),
            ("FR", "francecentral"),
            ("NO-NO1", "norwayeast"),
            ("SE-SE2", "swedencentral"),
            ("JP-TK", "japaneast"),
            ("AU-NSW", "australiaeast"),
            ("IN-NO", "centralindia"),
            ("ZA", "southafricanorth"),
            ("UNKNOWN-ZONE", "eastus"),  # default
        ],
    )
    def test_azure(self, zone, region):
        assert get_azure_region(zone) == region


class TestFormatRunsonLabel:
    @pytest.mark.parametrize(
        ("args", "label"),
        [
            pytest.param(
                ("CISO", "12345"),
                "runs-on=12345/runner=2cpu-linux-x64/region=us-west-1",
                id="basic",
            ),
            pytest.param(
                ("GB", "99999", "4cpu-linux-arm64"),
                "runs-on=99999/runner=4cpu-linux-arm64/region=eu-west-2",
                id="custom_spec",
            ),
            pytest.param(
                ("NO-NO1", "111"),
                "runs-on=111/runner=2cpu-linux-x64/region=eu-north-1",
                id="europe_region",
            ),
        ],
    )
    def test_label(self, args, label):
        assert format_runson_label(*args) == label


class TestFormatRunnerLabel:
    @pytest.mark.parametrize(
        ("args", "label"),
        [
            pytest.param(
                ("CISO", "runson", "12345"),
                "runs-on=12345/runner=2cpu-linux-x64/region=us-west-1",
                id="runson_provider",
            ),
            pytest.param(
                ("DE", "runson", "12345", "8cpu-linux-x64"),
                "runs-on=12345/runner=8cpu-linux-x64/region=eu-central-1",
                id="runson_with_custom_spec",
            ),
            pytest.param(("CISO", "runson", ""), None, id="runson_without_run_id"),
            pytest.param(("CISO", "unknown-provider", "12345"), None, id="unknown_provider"),
            pytest.param(("CISO", "", "12345"), None, id="empty_provider"),
            pytest.param(
                ("CISO", "RunsOn", "12345"),
                "runs-on=12345/runner=2cpu-linux-x64/region=us-west-1",
                id="case_insensitive",
            ),
        ],
    )
    def test_label(self, args, label):
        assert format_runner_label(*args) == label


@mock.patch("check_grid.set_output")
class TestSetRunnerOutputs:
    def test_no_provider_with_user_label(self, mock_output):
        check_grid.set_runner_outputs("CISO", "my-runner", "", "", "")
        out = outputs_of(mock_output)
        assert out["cloud_region"] == "us-west-1"
        assert out["runner_label"] == "my-runner"

    def test_no_provider_no_label(self, mock_output):
        check_grid.set_runner_outputs("CISO", None, "", "", "")
        out = outputs_of(mock_output)
        assert out["cloud_region"] == "us-west-1"
        assert "runner_label" not in out

    def test_runson_provider(self, mock_output):
        check_grid.set_runner_outputs("DE", None, "runson", "", "12345")
        out = outputs_of(mock_output)
        assert out["cloud_region"] == "eu-central-1"
        assert "runs-on=12345" in out["runner_label"]
        assert "region=eu-central-1" in out["runner_label"]

    def test_runson_overrides_user_label(self, mock_output):
        """Provider-formatted label takes precedence over user label."""
        check_grid.set_runner_outputs("CISO", "my-label", "runson", "", "12345")
        out = outputs_of(mock_output)
        assert "runs-on=12345" in out["runner_label"]
        assert out["runner_label"] != "my-label"

    def test_runson_fallback_to_user_label_without_run_id(self, mock_output):
        """Falls back to user label if RunsOn can't format (no run_id)."""
        check_grid.set_runner_outputs("CISO", "my-label", "runson", "", "")
        assert outputs_of(mock_output)["runner_label"] == "my-label"


class TestCloudRegionRecommender:
    def test_set_runner_outputs_includes_all_clouds(self, github_output):
        """set_runner_outputs should set gcp_region and azure_region."""
        check_grid.set_runner_outputs("CISO", None, "", "", "")
        content = github_output.read_text()
        assert "cloud_region=us-west-1" in content
        assert "gcp_region=us-west1" in content
        assert "azure_region=westus2" in content


@mock.patch("check_grid.check_carbon_intensity")
@mock.patch("check_grid.set_output")
@mock.patch("check_grid.write_job_summary")
class TestRoutingIntegration:
    """Integration tests: main() sets cloud_region and provider-formatted labels."""

    def test_single_zone_with_runson_provider(self, _mock_summary, mock_output, mock_check):
        mock_check.return_value = (True, 50)
        os.environ.update(
            GRID_ZONE="CISO",
            WORKFLOW_ID="",
            RUNNER_PROVIDER="runson",
            RUNNER_SPEC="4cpu-linux-x64",
            GITHUB_RUN_ID="98765",
        )

        check_grid.main()

        out = outputs_of(mock_output)
        assert out["grid_clean"] == "true"
        assert out["cloud_region"] == "us-west-1"
        assert out["runner_label"] == "runs-on=98765/runner=4cpu-linux-x64/region=us-west-1"

    def test_multi_zone_with_runson_provider(self, _mock_summary, mock_output, mock_check):
        mock_check.side_effect = [
            (False, 400),  # ERCO dirty
            (True, 80),  # GB green
        ]
        os.environ.update(
            GRID_ZONES="ERCO,GB", WORKFLOW_ID="", RUNNER_PROVIDER="runson", GITHUB_RUN_ID="11111"
        )

        check_grid.main()

        out = outputs_of(mock_output)
        assert out["grid_zone"] == "GB"
        assert out["cloud_region"] == "eu-west-2"
        assert "region=eu-west-2" in out["runner_label"]

    def test_cloud_region_output_without_provider(self, _mock_summary, mock_output, mock_check):
        """cloud_region is always set even without a runner_provider."""
        mock_check.return_value = (True, 100)
        os.environ.update(GRID_ZONE="NO-NO1", WORKFLOW_ID="")
        os.environ.pop("RUNNER_PROVIDER", None)

        check_grid.main()

        assert outputs_of(mock_output)["cloud_region"] == "eu-north-1"


# ---------------------------------------------------------------------------
# AEMO provider tests
# ---------------------------------------------------------------------------


def _aemo_rows(*rows):
    return [{"REGIONID": region, "FUELTYPE": fuel, "GEN_MW": mw} for region, fuel, mw in rows]


class TestAemoFuelMixToIntensity:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            pytest.param(_aemo_rows(("NSW1", "Black Coal", 1000)), 820, id="all_coal"),
            # IPCC AR5 wind onshore = 11, renewables are not treated as zero
            pytest.param(_aemo_rows(("NSW1", "Wind", 500)), 11, id="all_wind"),
            # coal 820, solar 48: (500*820 + 500*48) / 1000 = 434.0 -> 434
            pytest.param(
                _aemo_rows(("NSW1", "Black Coal", 500), ("NSW1", "Solar", 500)), 434, id="mixed"
            ),
            # only NSW wind is counted, wind = 11
            pytest.param(
                _aemo_rows(("NSW1", "Wind", 1000), ("QLD1", "Black Coal", 1000)),
                11,
                id="filters_by_region",
            ),
            pytest.param([], None, id="empty_data"),
            # wind = 11, battery is storage and excluded anyway
            pytest.param(
                _aemo_rows(("NSW1", "Wind", 100), ("NSW1", "Battery", -50)),
                11,
                id="negative_gen_ignored",
            ),
        ],
    )
    def test_intensity(self, data, expected):
        mix = aemo._region_fuel_mix(data, "NSW1")
        intensity = base.mix_to_intensity(mix, aemo.AEMO_EMISSION_FACTORS, aemo.AEMO_STORAGE_FUELS)
        assert intensity == expected


class TestAemoCheckCarbonIntensity:
    @pytest.mark.parametrize(
        ("zone", "fetched", "expected"),
        [
            # hydro 24, wind 12: (900*24 + 100*12) / 1000 = 22.8 -> 23
            pytest.param(
                "AU-TAS",
                _aemo_rows(("TAS1", "Hydro", 900), ("TAS1", "Wind", 100)),
                (True, 23),
                id="green",
            ),
            # brown coal/lignite 1050, wind 12: (800*1050 + 200*12) / 1000 = 842.4 -> 842
            pytest.param(
                "AU-VIC",
                _aemo_rows(("VIC1", "Brown Coal", 800), ("VIC1", "Wind", 200)),
                (False, 842),
                id="dirty",
            ),
            pytest.param("AU-NSW", None, (None, None), id="api_error"),
        ],
    )
    @mock.patch("providers.aemo._fetch_fuel_data")
    def test_verdict(self, mock_fetch, zone, fetched, expected):
        mock_fetch.return_value = fetched
        assert_verdict(aemo.check_carbon_intensity(zone, 250), *expected)

    def test_unknown_zone(self):
        assert_verdict(aemo.check_carbon_intensity("AU-UNKNOWN", 250), None, None)

    def test_forecast_not_available(self):
        assert aemo.get_forecast("AU-NSW", 250) == (None, None)


# ---------------------------------------------------------------------------
# ENTSO-E provider tests
# ---------------------------------------------------------------------------


def _entsoe_xml(*series):
    """TimeSeries fragments with one Point each, as (psrType, quantity) pairs."""
    return "\n".join(
        f"""
        <TimeSeries>
            <MktPSRType><psrType>{psr}</psrType></MktPSRType>
            <Period><Point><quantity>{qty}</quantity></Point></Period>
        </TimeSeries>
        """
        for psr, qty in series
    )


class TestEntsoeParseGenerationXml:
    def test_basic_parse(self):
        result = entsoe._parse_generation_xml(_entsoe_xml(("B16", "500.0"), ("B04", "300.0")))
        assert len(result) == 2
        assert ("B16", 500.0) in result
        assert ("B04", 300.0) in result

    def test_zero_quantity_excluded(self):
        assert entsoe._parse_generation_xml(_entsoe_xml(("B16", "0"))) == []

    def test_empty_xml(self):
        assert entsoe._parse_generation_xml("") == []

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
        # latest period end is 02:00 with quantity 500 (a sum would give 800)
        assert entsoe._parse_generation_xml(xml) == [("B05", 500.0)]

    def test_pumped_storage_excluded_from_intensity(self):
        # B10 Hydro Pumped Storage must be excluded from the weighted mix,
        # so the result is pure hard coal = 820
        assert entsoe._intensity_from_gen_data([("B05", 100.0), ("B10", 100.0)]) == 820

    def test_unknown_psr_warns_and_falls_back(self, capsys):
        assert entsoe._intensity_from_gen_data([("B99", 100.0)]) == entsoe.DEFAULT_FUEL_FACTOR
        out = capsys.readouterr().out
        assert "::warning::" in out
        assert "B99" in out


class TestEntsoeCheckCarbonIntensity:
    @pytest.mark.parametrize(
        ("response", "expected"),
        [
            # wind B19 = 11, gas B04 = 490: (800*11 + 200*490) / 1000
            # = (8800 + 98000) / 1000 = 106.8 -> 107
            pytest.param(
                mock.Mock(status_code=200, text=_entsoe_xml(("B19", 800), ("B04", 200))),
                (True, 107),
                id="green",
            ),
            # (700*820 + 300*490) / 1000 = 721
            pytest.param(
                mock.Mock(status_code=200, text=_entsoe_xml(("B05", 700), ("B04", 300))),
                (False, 721),
                id="dirty",
            ),
            pytest.param(
                mock.Mock(status_code=401, text="Unauthorized"), (None, None), id="auth_failure"
            ),
            pytest.param(
                mock.Mock(status_code=429, text="Too Many Requests"), (None, None), id="rate_limit"
            ),
            pytest.param(requests.RequestException("timeout"), (None, None), id="network_error"),
        ],
    )
    @mock.patch("providers.base._SESSION.get")
    def test_verdict(self, mock_get, response, expected):
        respond(mock_get, response)
        assert_verdict(entsoe.check_carbon_intensity("DE", 250, "token"), *expected)

    def test_no_token(self):
        assert_verdict(entsoe.check_carbon_intensity("DE", 250, ""), None, None)

    def test_unknown_zone(self):
        assert_verdict(entsoe.check_carbon_intensity("XX-UNKNOWN", 250, "token"), None, None)


class TestEntsoeForecast:
    def test_no_token(self):
        assert entsoe.get_forecast("DE", 250, "") == (None, None)

    def test_unknown_zone(self):
        assert entsoe.get_forecast("XX-FAKE", 250, "token") == (None, None)

    def test_series_parser_averages_subhourly(self):
        # Two 15-min points in the same hour are averaged, matching TimeSeries summed
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
        assert series[next(iter(series))] == 200.0

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
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        mock_prod.return_value = (400, 50000)  # dirty now, 20% VRE
        # now: 20% renewable. +3h: 80% renewable -> much cleaner
        mock_curve.return_value = {
            now: 0.20,
            now + timedelta(hours=1): 0.30,
            now + timedelta(hours=3): 0.80,
        }
        dt, intensity = entsoe.get_forecast("DE", 250, "token")
        # base_fossil=0.8. +3h projected = 400*(1-0.8)/0.8 = 100 <= 250
        assert intensity == 100
        assert dt.endswith("Z")

    @mock.patch("providers.entsoe.production_for_zone")
    @mock.patch("providers.entsoe._vre_fraction_curve")
    def test_forecast_no_green_window(self, mock_curve, mock_prod):
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        mock_prod.return_value = (800, 50000)  # dirty with low VRE throughout
        mock_curve.return_value = {now: 0.05, now + timedelta(hours=1): 0.06}
        assert entsoe.get_forecast("DE", 50, "token") == ("none_in_forecast", None)

    @mock.patch("providers.entsoe.production_for_zone")
    @mock.patch("providers.entsoe._vre_fraction_curve")
    def test_forecast_unavailable_curve(self, mock_curve, mock_prod):
        mock_prod.return_value = (400, 50000)
        mock_curve.return_value = {}  # forecast API failed
        assert entsoe.get_forecast("DE", 250, "token") == (None, None)


# ---------------------------------------------------------------------------
# Open-Meteo provider tests
# ---------------------------------------------------------------------------


def _weather(irradiance, wind):
    return mock.Mock(
        status_code=200,
        json=lambda: {"current": {"global_tilted_irradiance": irradiance, "wind_speed_10m": wind}},
    )


class TestOpenMeteoEstimateIntensity:
    @pytest.mark.parametrize(
        ("irradiance", "wind", "expected"),
        [
            # 40% solar reduction * 25% wind reduction: 550 * 0.60 * 0.75 = 247.5 -> 248
            # (above the renewable floor)
            pytest.param(700, 10, 248, id="high_solar_high_wind"),
            # Night, calm: full base intensity
            pytest.param(0, 1, 550, id="no_solar_no_wind"),
            pytest.param(400, 1, round(550 * 0.80 * 1.0), id="medium_solar_only"),
            # 25% wind reduction only: 550 * 1.0 * 0.75 = 412.5 -> 412
            pytest.param(0, 9, 412, id="high_wind_only"),
        ],
    )
    def test_estimate(self, irradiance, wind, expected):
        assert open_meteo._estimate_intensity_from_weather(irradiance, wind) == expected


class TestOpenMeteoCheckCarbonIntensity:
    @pytest.mark.parametrize(
        ("zone", "threshold", "response", "expected"),
        [
            # A clean grid (France ~56 prior) reads clean even modulated by weather
            pytest.param(
                "FR", 300, _weather(700, 10), (True, round(56 * 0.60 * 0.75)), id="green_zone"
            ),
            # A coal grid (South Africa ~700 prior) sits at its prior when calm/dark
            pytest.param("ZA", 300, _weather(0, 1), (False, 700), id="dirty_zone"),
            # Regression: before per-zone priors, nuclear France read ~550 at night
            # (about 7x too high) and would be wrongly skipped as dirty. It tracks
            # its ~56 prior even with zero sun and no wind
            pytest.param(
                "FR", 100, _weather(0, 0), (True, 56), id="clean_zone_not_misread_as_dirty"
            ),
            pytest.param(
                "ZA", 300, requests.RequestException("timeout"), (None, None), id="api_error"
            ),
            pytest.param(
                "ZA",
                300,
                mock.Mock(status_code=500, text="Server Error"),
                (None, None),
                id="non_200",
            ),
        ],
    )
    @mock.patch("providers.base._SESSION.get")
    def test_verdict(self, mock_get, zone, threshold, response, expected):
        respond(mock_get, response)
        assert_verdict(open_meteo.check_carbon_intensity(zone, threshold), *expected)

    def test_unknown_zone_no_coords(self):
        assert_verdict(open_meteo.check_carbon_intensity("XX-NONE", 300), None, None)

    @mock.patch("providers.base._SESSION.get")
    def test_with_explicit_lat_lon(self, mock_get):
        mock_get.return_value = _weather(600, 5)
        is_green, _intensity = open_meteo.check_carbon_intensity("CUSTOM", 500, lat=40.0, lon=-74.0)
        assert is_green is True


class TestOpenMeteoForecast:
    @mock.patch("providers.base._SESSION.get")
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
        # ZA prior ~700: the dark 06:00 hour stays dirty. Midday sun+wind
        # (700*0.45=315) crosses a 350 threshold, so the green window is 12:00
        dt, _intensity = open_meteo.get_forecast("ZA", 350)
        assert dt is not None
        assert "12:00" in dt

    @mock.patch("providers.base._SESSION.get")
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
        assert open_meteo.get_forecast("ZA", 100) == ("none_in_forecast", None)

    def test_history_trend_returns_none(self):
        assert open_meteo.get_history_trend("ZA") is None


# ---------------------------------------------------------------------------
# Time-aware auto:green sorting tests
# ---------------------------------------------------------------------------


class TestTimePriorityScore:
    @pytest.mark.parametrize(
        ("utc_hour", "score"),
        [
            pytest.param(20, 100, id="solar_peak_noon_local"),
            pytest.param(10, 10, id="solar_night_2am_local"),
        ],
    )
    def test_solar_follows_local_sun(self, utc_hour, score):
        zone = {"zone": "CISO", "utc_offset": -8, "type": "solar"}
        assert _time_priority_score(zone, utc_hour) == score

    def test_hydro_always_high(self):
        zone = {"zone": "NO-NO1", "utc_offset": 1, "type": "hydro"}
        # Any hour, hydro should be consistently high
        for utc_hour in [0, 6, 12, 18]:
            assert _time_priority_score(zone, utc_hour) >= 80

    def test_wind_higher_at_night(self):
        zone = {"zone": "GB-16", "utc_offset": 0, "type": "wind"}
        night_score = _time_priority_score(zone, 2)  # 2am local
        day_score = _time_priority_score(zone, 14)  # 2pm local
        assert night_score > day_score


class TestSortAutoGreenByTime:
    def test_solar_ranked_high_at_noon(self):
        # 20 UTC = noon in California (UTC-8)
        zone_names = [z["zone"] for z in sort_auto_green_by_time(list(AUTO_GREEN_ZONES), 20)]
        # CISO (solar, UTC-8) should be near the top at noon local time
        assert zone_names.index("CISO") < 5

    def test_solar_ranked_low_at_night(self):
        # 10 UTC = 2am in California (UTC-8)
        zone_names = [z["zone"] for z in sort_auto_green_by_time(list(AUTO_GREEN_ZONES), 10)]
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
        # Americas (EIA), UK, Australia (AEMO) and Brazil (ONS) are all free
        assert {"CISO", "BPAT", "GB-16", "AU-TAS", "BR-S"} <= zones

    def test_auto_green_excludes_geowalled_india(self):
        """Grid India zones are geo-walled (Indian IPs only), so curated
        presets omit them to keep the default experience clean from CI."""
        green = {z["zone"] for z in AUTO_GREEN_ZONES}
        cleanest = {z["zone"] for z in AUTO_CLEANEST_ZONES}
        assert not any(z.startswith("IN-") for z in green)
        assert not any(z.startswith("IN-") for z in cleanest)

    def test_auto_green_only_free_providers(self):
        """auto:green is the curated free set. The token-only extras live in
        auto:green:full."""
        zones = {z["zone"] for z in AUTO_GREEN_ZONES}
        full = {z["zone"] for z in AUTO_GREEN_ZONES_FULL}
        # Token-tier zones are reserved for auto:green:full
        for token_zone in ("NO-NO1", "FR", "NZ-NZN"):
            assert token_zone not in zones
            assert token_zone in full
        # Canada is keyless (IESO / Hydro-Quebec), so CA-QC belongs in auto:green
        assert "CA-QC" in zones

    def test_auto_green_full_includes_token_zones(self):
        """auto:green:full includes both free and token-requiring zones."""
        zones = {z["zone"] for z in AUTO_GREEN_ZONES_FULL}
        assert "CISO" in zones  # Free
        assert {"NO-NO1", "CA-QC", "NZ-NZN"} <= zones  # Token-requiring

    def test_all_zones_have_required_fields(self):
        for zone in AUTO_GREEN_ZONES:
            assert {"zone", "runner_label", "utc_offset", "type"} <= set(zone)
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
        saved, _badge_url = check_grid.estimate_carbon_savings(500)
        assert saved == 0

    def test_none_intensity(self):
        assert check_grid.estimate_carbon_savings(None) == (0, None)

    def test_custom_job_minutes(self):
        saved_short, _ = check_grid.estimate_carbon_savings(100, job_minutes=15)
        saved_long, _ = check_grid.estimate_carbon_savings(100, job_minutes=60)
        assert saved_long > saved_short

    def test_badge_url_format(self):
        _, badge_url = check_grid.estimate_carbon_savings(100)
        assert "CO2_saved" in badge_url
        assert "brightgreen" in badge_url


class TestCarbonEquivalents:
    @pytest.mark.parametrize("grams", [0, None, -5])
    def test_zero_and_none_have_empty_phrase(self, grams):
        eq = check_grid.carbon_equivalents(grams)
        assert eq["phrase"] == ""
        assert eq["km_driven"] == 0
        assert eq["phone_charges"] == 0

    def test_small_amount_uses_phone_charges(self):
        # 50 g is under a km of driving, so the phrase should be phone charges
        eq = check_grid.carbon_equivalents(50)
        assert "phone charges" in eq["phrase"]
        # 50 / 12.4 ~= 4 charges
        assert eq["phone_charges"] == pytest.approx(50 / 12.4, rel=0.01)

    def test_large_amount_uses_km_driven(self):
        # 1000 g => ~4.1 km driven, comfortably over the 1 km switchover
        eq = check_grid.carbon_equivalents(1000)
        assert "km not driven" in eq["phrase"]
        assert eq["km_driven"] == pytest.approx(1000 / 244, rel=0.01)

    def test_switchover_at_one_km(self):
        # Exactly one km's worth should report km (>= 1)
        eq = check_grid.carbon_equivalents(check_grid.CO2_GRAMS_PER_KM_DRIVEN)
        assert "km not driven" in eq["phrase"]

    def test_tree_years_present(self):
        eq = check_grid.carbon_equivalents(60000)
        assert eq["tree_years"] == pytest.approx(1.0, rel=0.01)


class TestSetSavingsOutputs:
    def test_emits_equivalent_output(self, github_output):
        check_grid.set_savings_outputs(1000, "https://img.shields.io/badge/x")
        content = github_output.read_text()
        assert "co2_saved_grams=1000" in content
        assert "co2_saved_equivalent=" in content
        assert "km not driven" in content
        assert "carbon_badge_url=" in content

    def test_no_savings_emits_nothing(self, github_output):
        check_grid.set_savings_outputs(0, None)
        content = github_output.read_text()
        assert "co2_saved_grams" not in content
        assert "co2_saved_equivalent" not in content

    def test_emits_honest_emitted_and_basis(self, github_output):
        # measured intensity -> per-run emissions + basis are set
        check_grid.set_savings_outputs(500, None, intensity=120)
        content = github_output.read_text()
        assert "co2_emitted_grams=" in content
        assert "co2_saved_basis=" in content
        assert "benchmark" in content  # the basis is stated in the output

    def test_no_emitted_output_when_intensity_unknown(self, github_output):
        check_grid.set_savings_outputs(0, None, intensity=None)
        assert "co2_emitted_grams" not in github_output.read_text()


class TestCarbonTier:
    @pytest.mark.parametrize(
        ("raw", "thresholds"),
        [
            pytest.param("", check_grid.DEFAULT_TIER_THRESHOLDS, id="defaults_on_empty"),
            pytest.param("120,280", (120.0, 280.0), id="valid"),
            pytest.param("300,100", check_grid.DEFAULT_TIER_THRESHOLDS, id="bad_order_falls_back"),
            pytest.param("abc", check_grid.DEFAULT_TIER_THRESHOLDS, id="garbage_falls_back"),
            pytest.param("100", check_grid.DEFAULT_TIER_THRESHOLDS, id="wrong_count_falls_back"),
        ],
    )
    def test_parse_thresholds(self, raw, thresholds):
        assert check_grid.parse_tier_thresholds(raw) == thresholds

    @pytest.mark.parametrize(
        ("intensity", "tier"),
        [
            pytest.param(80, "green", id="green"),
            pytest.param(200, "amber", id="amber"),
            pytest.param(500, "red", id="red"),
            pytest.param(150, "green", id="green_boundary_inclusive"),
            pytest.param(300, "amber", id="amber_boundary_inclusive"),
            pytest.param(None, "unknown", id="unknown_on_none"),
            # Negative intensity should not crash, treated as cleanest (green)
            pytest.param(-10, "green", id="negative_is_green"),
        ],
    )
    def test_classify(self, intensity, tier):
        assert check_grid.classify_tier(intensity, (150, 300))[0] == tier

    def test_classify_green_reason(self):
        tier, reason = check_grid.classify_tier(80, (150, 300))
        assert tier == "green"
        assert "full" in reason


class TestWorthWaiting:
    def test_waits_when_savings_beat_idle(self):
        # 1 kWh job, grid drops 300 -> 50 in 1h. Saved = 250 g, idle = 0.013*1*300
        # = 3.9 g. Worth waiting
        should, saved, idle = check_grid.worth_waiting(300, 50, 1.0, 1.0)
        assert should is True
        assert saved == 250.0
        assert idle == pytest.approx(3.9)

    def test_runs_now_when_idle_dominates(self):
        # Tiny improvement (300 -> 290) but a 20h wait on a small job: idle wins
        should, _saved, _idle = check_grid.worth_waiting(300, 290, 20.0, 0.1)
        assert should is False

    def test_future_dirtier_never_waits(self):
        should, saved, _ = check_grid.worth_waiting(100, 200, 1.0, 5.0)
        assert should is False
        assert saved == 0.0

    @pytest.mark.parametrize(
        "args",
        [(None, 50, 1.0, 1.0), (300, 50, 0, 1.0), (300, 50, 1.0, 0)],
        ids=["no_current_intensity", "zero_wait_hours", "zero_energy"],
    )
    def test_missing_inputs_fail_to_run_now(self, args):
        assert check_grid.worth_waiting(*args)[0] is False


class TestAllocateShards:
    def test_all_to_cleanest_when_unbounded(self):
        alloc, unplaced = check_grid.allocate_shards([("GB", 200), ("FR", 50)], 10)
        assert alloc == [("FR", 10, 50)]
        assert unplaced == 0

    def test_fills_cleanest_first_with_capacity(self):
        alloc, unplaced = check_grid.allocate_shards(
            [("GB", 200), ("FR", 50), ("DE", 400)], 10, {"FR": 4, "GB": 3, "DE": 10}
        )
        # FR (cleanest) takes its 4, GB next takes 3, DE takes the last 3
        assert alloc == [("FR", 4, 50), ("GB", 3, 200), ("DE", 3, 400)]
        assert unplaced == 0

    def test_reports_unplaced_when_capacity_short(self):
        alloc, unplaced = check_grid.allocate_shards([("FR", 50)], 10, {"FR": 4})
        assert alloc == [("FR", 4, 50)]
        assert unplaced == 6

    def test_zero_capacity_excludes_zone(self):
        alloc, _ = check_grid.allocate_shards([("FR", 50), ("GB", 200)], 5, {"FR": 0})
        assert alloc == [("GB", 5, 200)]

    def test_drops_none_intensity(self):
        alloc, _ = check_grid.allocate_shards([("GB", None), ("FR", 50)], 3)
        assert alloc == [("FR", 3, 50)]

    def test_emissions_and_even_split(self):
        alloc, _ = check_grid.allocate_shards([("FR", 50), ("GB", 250)], 4, {"FR": 2, "GB": 2})
        # 2 shards FR @ 1 kWh @ 50 + 2 @ 250 = 100 + 500 = 600
        assert check_grid.allocation_emissions(alloc, 1.0) == 600.0
        # even split: 2 @ 50 + 2 @ 250 = 600 (same here, both capacity-limited)
        assert check_grid.even_split_emissions([("FR", 50), ("GB", 250)], 4, 1.0) == 600.0

    def test_optimal_beats_even_split(self):
        zones = [("FR", 50), ("GB", 250)]
        alloc, _ = check_grid.allocate_shards(zones, 4)  # all to FR
        opt = check_grid.allocation_emissions(alloc, 1.0)  # 4*50 = 200
        even = check_grid.even_split_emissions(zones, 4, 1.0)  # 2*50 + 2*250 = 600
        assert opt < even


class TestComputeCarbonScale:
    @pytest.mark.parametrize(
        ("intensity", "scale"),
        [
            pytest.param(100, 1.0, id="clean_returns_max"),
            pytest.param(400, 0.25, id="dirty_returns_min"),
            pytest.param(150, 1.0, id="lower_boundary_inclusive"),
            pytest.param(300, 0.25, id="upper_boundary_inclusive"),
            # Halfway between 150 and 300 -> halfway between 1.0 and 0.25 = 0.625
            pytest.param(225, 0.625, id="linear_interpolation_midpoint"),
            pytest.param(None, 1.0, id="unknown_fails_open_to_max"),
        ],
    )
    def test_scale(self, intensity, scale):
        assert check_grid.compute_carbon_scale(intensity, (150, 300)) == scale

    def test_custom_bounds(self):
        # Floor of 0.5, ceiling of 2.0, dirty -> floor
        assert check_grid.compute_carbon_scale(400, (150, 300), 0.5, 2.0) == 0.5
        assert check_grid.compute_carbon_scale(100, (150, 300), 0.5, 2.0) == 2.0

    def test_scale_bounds_from_env(self, monkeypatch):
        monkeypatch.setenv("SCALE_MIN", "0.1")
        monkeypatch.setenv("SCALE_MAX", "3")
        assert check_grid._scale_bounds() == (0.1, 3.0)

    def test_scale_bounds_rejects_inverted(self, monkeypatch):
        monkeypatch.setenv("SCALE_MIN", "2")
        monkeypatch.setenv("SCALE_MAX", "1")
        assert check_grid._scale_bounds() == (
            check_grid.DEFAULT_SCALE_MIN,
            check_grid.DEFAULT_SCALE_MAX,
        )


class TestCostCarbonRanking:
    def test_cost_weight_default_zero(self, monkeypatch):
        monkeypatch.delenv("COST_WEIGHT", raising=False)
        assert check_grid._cost_weight() == 0.0

    @pytest.mark.parametrize(
        ("raw", "weight"),
        [
            pytest.param("1.7", 1.0, id="clamped_to_one"),
            pytest.param("-3", 0.0, id="clamped_to_zero"),
            pytest.param("abc", 0.0, id="garbage_is_zero"),
        ],
    )
    def test_cost_weight_parsing(self, monkeypatch, raw, weight):
        monkeypatch.setenv("COST_WEIGHT", raw)
        assert check_grid._cost_weight() == weight

    @pytest.mark.parametrize(
        ("cost_weight", "winner"),
        [
            pytest.param(1.0, "FR", id="pure_cost_picks_cheapest"),
            pytest.param(0.0, "CISO", id="pure_carbon_picks_cleanest"),
        ],
    )
    @mock.patch("check_grid.azure_pricing.get_region_price")
    def test_ranking_extremes(self, price, cost_weight, winner):
        # CISO cleaner (50) but pricier (0.10). FR dirtier (100) but cheaper (0.05)
        candidates = [("CISO", 50, "l1"), ("FR", 100, "l2")]
        price.side_effect = [0.10, 0.05]
        zone, _intensity, _label = check_grid.rank_by_cost_carbon(candidates, cost_weight)
        assert zone == winner

    @mock.patch("check_grid.azure_pricing.get_region_price")
    def test_missing_price_falls_back(self, price):
        candidates = [("CISO", 50, "l1"), ("FR", 100, "l2")]
        price.side_effect = [0.10, None]
        assert check_grid.rank_by_cost_carbon(candidates, 0.5) is None

    @pytest.mark.parametrize(
        ("raw", "price_map"),
        [
            pytest.param(None, {}, id="unset_is_empty"),
            pytest.param(
                '{"CISO": "0.09", "GB": "0.11"}', {"CISO": "0.09", "GB": "0.11"}, id="parses_json"
            ),
            pytest.param("{not json", {}, id="bad_json_is_empty"),
        ],
    )
    def test_load_price_map(self, monkeypatch, raw, price_map):
        if raw is None:
            monkeypatch.delenv("COST_PRICE_MAP", raising=False)
        else:
            monkeypatch.setenv("COST_PRICE_MAP", raw)
        assert check_grid._load_price_map() == price_map

    @mock.patch("check_grid.azure_pricing.get_region_price")
    def test_price_map_used_before_azure(self, azure):
        azure.return_value = 99.0  # should not be consulted for mapped zones
        assert check_grid._zone_price("CISO", {"CISO": "0.07"}) == 0.07
        azure.assert_not_called()

    @mock.patch("check_grid.azure_pricing.get_region_price")
    def test_price_map_falls_back_to_azure(self, azure):
        azure.return_value = 0.12
        assert check_grid._zone_price("GB", {"CISO": "0.07"}) == 0.12

    @mock.patch("check_grid.azure_pricing.get_region_price")
    def test_multi_cloud_price_map_ranking(self, azure, monkeypatch):
        # All prices from the map (any cloud), cheapest wins at cost_weight=1
        monkeypatch.setenv("COST_PRICE_MAP", '{"CISO": "0.20", "GB": "0.05"}')
        zone, _, _ = check_grid.rank_by_cost_carbon([("CISO", 50, "l1"), ("GB", 60, "l2")], 1.0)
        assert zone == "GB"
        azure.assert_not_called()

    @mock.patch("check_grid.azure_pricing.get_region_price")
    def test_single_candidate_zero_span(self, price):
        # One candidate: price and carbon spans are both zero, so must not divide by 0
        price.side_effect = [0.10]
        zone, _intensity, _label = check_grid.rank_by_cost_carbon([("CISO", 50, "l1")], 0.5)
        assert zone == "CISO"

    def test_empty_candidates(self):
        assert check_grid.rank_by_cost_carbon([], 0.5) is None


@pytest.mark.usefixtures("reset_once_flags")
class TestEmitRunSignalsIntegration:
    """End-to-end coverage of the composed signal-emission path."""

    def test_file_ledger_budget_and_tier(self, github_output, ledger_path, monkeypatch):
        monkeypatch.setenv("MONTHLY_BUDGET_GRAMS", "2000")
        monkeypatch.setenv("TIER_THRESHOLDS", "150,300")
        tier, _ = check_grid.emit_run_signals("GB", 192, True, 250)
        assert tier == "amber"
        content = github_output.read_text()
        assert "carbon_tier=amber" in content
        assert "budget_state=ok" in content
        assert "budget_exceeded=false" in content
        assert ledger_path.exists()  # ledger actually written


class TestDoctor:
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.detect_provider")
    def test_run_doctor_end_to_end(self, detect, check, step_summary, monkeypatch):
        detect.return_value = "uk_carbon_intensity"
        check.return_value = (True, 120)
        monkeypatch.setenv("GRID_ZONES", "GB")
        check_grid.run_doctor()
        written = step_summary.read_text()
        assert "Zone connectivity" in written
        assert "`GB`" in written
        assert "OK" in written

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


@pytest.mark.usefixtures("reset_once_flags")
class TestMarginalOutputs:
    def test_noop_without_creds(self, monkeypatch):
        monkeypatch.delenv("WATTTIME_USERNAME", raising=False)
        monkeypatch.delenv("WATTTIME_PASSWORD", raising=False)
        check_grid.emit_marginal_outputs()
        assert check_grid._marginal_summary is None

    @pytest.mark.parametrize(
        ("percentile", "max_pct", "clean"),
        [
            pytest.param(20, "33", True, id="clean_below_threshold"),
            pytest.param(90, None, False, id="dirty_above_threshold"),
        ],
    )
    @mock.patch("check_grid.watttime.get_marginal_index")
    @mock.patch("check_grid.watttime.login")
    def test_verdict(self, login, idx, github_output, monkeypatch, percentile, max_pct, clean):
        login.return_value = "tok"
        idx.return_value = percentile
        monkeypatch.setenv("WATTTIME_USERNAME", "u")
        monkeypatch.setenv("WATTTIME_PASSWORD", "p")
        if max_pct is not None:
            monkeypatch.setenv("MARGINAL_MAX_PERCENTILE", max_pct)
        check_grid.emit_marginal_outputs()
        content = github_output.read_text()
        assert f"marginal_percentile={percentile}" in content
        assert f"marginal_clean={str(clean).lower()}" in content
        assert check_grid._marginal_summary["clean"] is clean
        # A second call is a no-op: the once-guard skips even the login
        check_grid.emit_marginal_outputs()
        login.assert_called_once()


class TestEstimateEmissions:
    @pytest.mark.parametrize(
        ("intensity", "kwargs"),
        [
            pytest.param(None, {}, id="none_intensity"),
            pytest.param(-50, {}, id="negative_intensity_clamped"),
            pytest.param(100, {"job_minutes": -5}, id="negative_job_minutes_clamped"),
        ],
    )
    def test_zero_emissions(self, intensity, kwargs):
        assert check_grid.estimate_emissions(intensity, **kwargs) == 0.0

    def test_proportional_to_intensity(self):
        # Chosen so both products land exactly on a tenth: the default job is
        # 0.00325 kWh, so smaller intensities are dominated by round(x, 1)
        low = check_grid.estimate_emissions(400)
        high = check_grid.estimate_emissions(1600)
        assert high > low > 0
        assert high == pytest.approx(low * 4, rel=0.01)

    def test_longer_job_emits_more(self):
        assert check_grid.estimate_emissions(100, job_minutes=60) > check_grid.estimate_emissions(
            100, job_minutes=15
        )


class TestResolveEnergy:
    @pytest.fixture(autouse=True)
    def _clear_energy_env(self, monkeypatch):
        for k in ("JOB_ENERGY_KWH", "JOB_POWER_WATTS", "JOB_DURATION_MINUTES"):
            monkeypatch.delenv(k, raising=False)

    def test_default_ci_estimate(self):
        # 13 W x 0.25 h = 0.00325 kWh
        assert check_grid.resolve_energy_kwh() == pytest.approx(0.00325, rel=1e-6)

    def test_bounds_bracket_the_point_estimate(self):
        low, high = check_grid.energy_bounds_kwh()
        assert low < check_grid.resolve_energy_kwh() < high

    def test_measured_energy_collapses_the_bounds(self, monkeypatch):
        monkeypatch.setenv("JOB_ENERGY_KWH", "12")
        assert check_grid.energy_bounds_kwh() == (12.0, 12.0)
        assert check_grid.emissions_bounds(400) is None

    def test_emissions_bounds_bracket_the_estimate(self):
        low, high = check_grid.emissions_bounds(400)
        assert low < check_grid.estimate_emissions(400) < high
        assert check_grid.emissions_bounds(None) is None

    def test_explicit_energy_wins(self, monkeypatch):
        monkeypatch.setenv("JOB_ENERGY_KWH", "12")
        monkeypatch.setenv("JOB_POWER_WATTS", "999999")  # must be ignored
        assert check_grid.resolve_energy_kwh() == 12.0

    def test_power_and_duration(self, monkeypatch):
        monkeypatch.setenv("JOB_POWER_WATTS", "300")  # 0.3 kW
        monkeypatch.setenv("JOB_DURATION_MINUTES", "120")  # 2 h
        assert check_grid.resolve_energy_kwh() == pytest.approx(0.6, rel=1e-6)

    def test_emissions_use_resolved_energy(self, monkeypatch):
        monkeypatch.setenv("JOB_ENERGY_KWH", "10")  # 10 kWh
        # 400 gCO2/kWh x 10 kWh = 4000 g
        assert check_grid.estimate_emissions(400) == 4000.0


class TestPueAndEmbodied:
    @pytest.fixture(autouse=True)
    def _clear_energy_env(self, monkeypatch):
        for k in ("JOB_ENERGY_KWH", "PUE", "EMBODIED_GRAMS"):
            monkeypatch.delenv(k, raising=False)

    def test_defaults_pue_1_embodied_0(self):
        assert check_grid._pue() == 1.0
        assert check_grid._embodied_grams() == 0.0

    def test_pue_scales_emissions(self, monkeypatch):
        monkeypatch.setenv("JOB_ENERGY_KWH", "10")
        monkeypatch.setenv("PUE", "1.2")
        # 400 x 10 x 1.2 = 4800
        assert check_grid.estimate_emissions(400) == 4800.0

    def test_embodied_added(self, monkeypatch):
        monkeypatch.setenv("JOB_ENERGY_KWH", "10")
        monkeypatch.setenv("EMBODIED_GRAMS", "500")
        # 400 x 10 x 1.0 + 500 = 4500
        assert check_grid.estimate_emissions(400) == 4500.0

    def test_pue_scales_savings_benchmark(self, monkeypatch):
        monkeypatch.setenv("JOB_ENERGY_KWH", "10")
        monkeypatch.setenv("PUE", "2.0")
        # saved = (458 - 50) x 10 x 2.0 = 8160
        saved, _ = check_grid.estimate_carbon_savings(50)
        assert saved == 8160.0


class TestDataSource:
    def test_measured_provider(self):
        check_grid._provider_used["GB"] = check_grid.PROVIDER_UK
        assert check_grid.data_source_for("GB") == (check_grid.PROVIDER_UK, "measured")

    def test_estimated_provider(self):
        check_grid._provider_used["ZZ"] = PROVIDER_OPEN_METEO
        assert check_grid.data_source_for("ZZ") == (PROVIDER_OPEN_METEO, "estimated")

    def test_falls_back_to_detect_when_unrecorded(self):
        check_grid._provider_used.pop("GB", None)
        # GB routes to UK
        assert check_grid.data_source_for("GB") == (check_grid.PROVIDER_UK, "measured")

    @mock.patch("check_grid.uk.check_carbon_intensity", return_value=(True, 100))
    def test_check_records_actual_provider(self, _mock):
        check_grid._provider_used.pop("GB", None)
        check_grid.check_carbon_intensity("GB", 250, check_grid.PROVIDER_UK)
        assert check_grid._provider_used["GB"] == check_grid.PROVIDER_UK


@pytest.mark.usefixtures("reset_once_flags")
class TestGreenSLA:
    @pytest.mark.parametrize(
        ("green", "dirty", "status", "breached"),
        [
            # 9 green + this run green = 10 green, 0 dirty -> 100% >= 95
            pytest.param(9, 0, "compliant", "false", id="compliant"),
            # 1 green + this run green = 2 green of 10 total -> 20% < 95
            pytest.param(1, 8, "breached", "true", id="breached"),
            # only 3 runs total (<5) -> unknown
            pytest.param(2, 0, "unknown", None, id="unknown_too_few_runs"),
        ],
    )
    def test_status(self, github_output, ledger_path, monkeypatch, green, dirty, status, breached):
        # Seed a ledger with `green` green runs + `dirty` dirty runs, then emit SLA
        data = ledger.empty_ledger()
        seed_date = datetime.now(timezone.utc).strftime("%Y-%m-01")
        for _ in range(green):
            data = ledger.merge_entry(data, 0, seed_date, is_green=True)
        for _ in range(dirty):
            data = ledger.merge_entry(data, 0, seed_date, is_green=False)
        ledger_path.write_text(json.dumps(data))
        monkeypatch.setenv("GREEN_SLA_TARGET", "95")

        check_grid.record_lifetime_savings(0, 0, is_green=True)  # records one more green run

        content = github_output.read_text()
        assert f"sla_status={status}" in content
        if breached is not None:
            assert f"sla_breached={breached}" in content


@pytest.mark.usefixtures("reset_once_flags")
class TestCarbonBudget:
    @pytest.mark.parametrize(
        ("budget", "emitted", "exceeded", "expected"),
        [
            pytest.param(
                1000,
                100,
                False,
                ["budget_used_pct=10.0", "budget_exceeded=false", "budget_state=ok"],
                id="under_budget_state_ok",
            ),
            # remaining is clamped to 0, never negative
            pytest.param(
                50,
                80,
                True,
                ["budget_exceeded=true", "budget_state=exceeded", "budget_remaining_grams=0"],
                id="over_budget_exceeded",
            ),
            pytest.param(
                1000,
                800,
                False,
                ["budget_state=warning", "budget_exceeded=false"],
                id="warning_at_80_percent",
            ),
        ],
    )
    def test_budget_outputs(
        self, github_output, ledger_path, monkeypatch, budget, emitted, exceeded, expected
    ):
        monkeypatch.setenv("MONTHLY_BUDGET_GRAMS", str(budget))
        # emitted comes from intensity via estimate_emissions in production code,
        # so passing the grams directly drives the budget computation without it
        check_grid.record_lifetime_savings(0, emitted_grams=emitted)
        content = github_output.read_text()
        for line in expected:
            assert line in content
        assert check_grid._budget_summary["exceeded"] is exceeded

    def test_budget_emitted_on_dirty_path(self, github_output, ledger_path, monkeypatch):
        # On a dirty grid (no savings recorded), budget gating must still work:
        # write_job_summary force-records so budget_exceeded is emitted
        monkeypatch.setenv("MONTHLY_BUDGET_GRAMS", "1000")
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        # dirty grid: is_green False, no set_savings_outputs call beforehand
        check_grid.write_job_summary("PL", 600, False, 250)
        content = github_output.read_text()
        assert "budget_exceeded=" in content
        assert "budget_state=" in content


@pytest.mark.usefixtures("reset_once_flags")
class TestRecordLifetimeSavings:
    def test_no_ledger_config_is_noop(self, github_output, monkeypatch):
        monkeypatch.delenv("LEDGER", raising=False)
        check_grid.record_lifetime_savings(100)
        assert "co2_saved_total_grams" not in github_output.read_text()
        assert check_grid._lifetime_summary is None

    def test_file_ledger_sets_outputs_and_summary(self, github_output, ledger_path):
        check_grid.record_lifetime_savings(100)
        content = github_output.read_text()
        assert "co2_saved_total_grams=100" in content
        assert "co2_saved_total_equivalent=" in content
        assert check_grid._lifetime_summary["total_runs"] == 1

    def test_records_at_most_once_per_process(self, ledger_path):
        check_grid.record_lifetime_savings(100)
        check_grid.record_lifetime_savings(100)  # second call must be ignored
        assert check_grid._lifetime_summary["total_runs"] == 1


@pytest.mark.usefixtures("reset_once_flags")
@mock.patch("check_grid.pr_comment.post_comment")
class TestPostPrCommentOnce:
    def test_noop_when_disabled(self, posted, monkeypatch):
        monkeypatch.delenv("PR_COMMENT", raising=False)
        check_grid.post_pr_comment_once("CISO", 80, True, 250)
        posted.assert_not_called()

    def test_posts_when_enabled(self, posted, monkeypatch):
        monkeypatch.setenv("PR_COMMENT", "true")
        check_grid.post_pr_comment_once("CISO", 80, True, 250, co2_saved=1500)
        posted.assert_called_once()
        # body is the 5th positional arg
        assert "CISO" in posted.call_args.args[4]

    def test_only_once(self, posted, monkeypatch):
        monkeypatch.setenv("PR_COMMENT", "true")
        check_grid.post_pr_comment_once("CISO", 80, True, 250)
        check_grid.post_pr_comment_once("CISO", 80, True, 250)
        assert posted.call_count == 1


class TestRoutingComparison:
    def test_renders_bars_and_markers(self):
        measured = [("BR-NE", 27), ("GB", 169), ("AU-NSW", 501)]
        panel = check_grid.render_routing_comparison(measured, "BR-NE")
        assert panel is not None
        text = "\n".join(panel)
        # chosen zone is marked, and the dirtiest is left unmarked (chart speaks for itself)
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

    def test_summary_includes_comparison(self, step_summary):
        check_grid.write_job_summary(
            "GB", 169, True, 250, comparison=[("GB", 169), ("AU-NSW", 501)]
        )
        content = step_summary.read_text()
        assert "Carbon-aware routing" in content
        assert "routed here" in content


# ---------------------------------------------------------------------------
# check_grid.py dispatch routing tests
# ---------------------------------------------------------------------------


class TestCheckGridDispatchRouting:
    @pytest.mark.parametrize(
        ("module_name", "zone", "max_carbon", "provider", "kwargs", "call_args", "verdict"),
        [
            pytest.param(
                "aemo", "AU-NSW", 250, PROVIDER_AEMO, {}, ("AU-NSW", 250), (True, 100), id="aemo"
            ),
            pytest.param(
                "entsoe",
                "DE",
                250,
                PROVIDER_ENTSOE,
                {"entsoe_token": "token"},
                ("DE", 250, "token"),
                (True, 80),
                id="entsoe_with_token",
            ),
            pytest.param(
                "open_meteo",
                "ZA",
                250,
                PROVIDER_OPEN_METEO,
                {},
                ("ZA", 250),
                (True, 200),
                id="open_meteo",
            ),
            pytest.param(
                "grid_india",
                "IN-NO",
                500,
                PROVIDER_GRID_INDIA,
                {},
                ("IN-NO", 500),
                (True, 300),
                id="grid_india",
            ),
            pytest.param(
                "ons_brazil",
                "BR-S",
                250,
                PROVIDER_ONS_BRAZIL,
                {},
                ("BR-S", 250),
                (True, 100),
                id="ons_brazil",
            ),
            pytest.param(
                "eskom", "ZA", 250, PROVIDER_ESKOM, {}, ("ZA", 250), (False, 750), id="eskom"
            ),
        ],
    )
    def test_check_routes_to_provider(
        self, module_name, zone, max_carbon, provider, kwargs, call_args, verdict
    ):
        target = f"providers.{module_name}.check_carbon_intensity"
        with mock.patch(target, return_value=verdict) as mock_check:
            result = check_grid.check_carbon_intensity(zone, max_carbon, provider, **kwargs)
        assert result[0] is verdict[0]
        mock_check.assert_called_once_with(*call_args)

    @pytest.mark.parametrize(
        ("module_name", "zone", "provider", "kwargs", "call_args", "forecast"),
        [
            pytest.param(
                "aemo", "AU-NSW", PROVIDER_AEMO, {}, ("AU-NSW", 250), (None, None), id="aemo"
            ),
            pytest.param(
                "entsoe",
                "DE",
                PROVIDER_ENTSOE,
                {"entsoe_token": "tok"},
                ("DE", 250, "tok"),
                ("2026-03-10T12:00Z", 90),
                id="entsoe_with_token",
            ),
            pytest.param(
                "open_meteo",
                "ZA",
                PROVIDER_OPEN_METEO,
                {},
                ("ZA", 250),
                ("2026-03-10T12:00Z", 200),
                id="open_meteo",
            ),
            pytest.param(
                "grid_india",
                "IN-SO",
                PROVIDER_GRID_INDIA,
                {},
                ("IN-SO", 250),
                (None, None),
                id="grid_india",
            ),
            pytest.param(
                "ons_brazil",
                "BR-NE",
                PROVIDER_ONS_BRAZIL,
                {},
                ("BR-NE", 250),
                (None, None),
                id="ons_brazil",
            ),
            pytest.param("eskom", "ZA", PROVIDER_ESKOM, {}, ("ZA", 250), (None, None), id="eskom"),
        ],
    )
    def test_forecast_routes_to_provider(
        self, module_name, zone, provider, kwargs, call_args, forecast
    ):
        with mock.patch(f"providers.{module_name}.get_forecast", return_value=forecast) as mock_fc:
            check_grid.get_forecast(zone, 250, provider, **kwargs)
        mock_fc.assert_called_once_with(*call_args)

    @pytest.mark.parametrize(
        ("module_name", "zone", "provider"),
        [
            pytest.param("open_meteo", "ZA", PROVIDER_OPEN_METEO, id="open_meteo"),
            pytest.param("grid_india", "IN-WE", PROVIDER_GRID_INDIA, id="grid_india"),
            pytest.param("ons_brazil", "BR-S", PROVIDER_ONS_BRAZIL, id="ons_brazil"),
            pytest.param("eskom", "ZA", PROVIDER_ESKOM, id="eskom"),
        ],
    )
    def test_trend_routes_to_provider(self, module_name, zone, provider):
        with mock.patch(f"providers.{module_name}.get_history_trend", return_value=None) as mock_tr:
            check_grid.get_history_trend(zone, provider)
        mock_tr.assert_called_once_with(zone)


# --- Grid India provider tests ---


class TestGridIndiaProvider:
    def test_unknown_zone(self):
        assert_verdict(grid_india.check_carbon_intensity("XX", 250), None, None)

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
        assert grid_india._estimate_from_national_mix([{"coal": 3000, "solar": 1000}]) is not None

    @mock.patch("providers.grid_india._fetch_generation_data")
    def test_check_intensity_api_failure(self, mock_fetch):
        mock_fetch.return_value = None
        is_green, _intensity = grid_india.check_carbon_intensity("IN-NO", 250)
        assert is_green is None

    @mock.patch("providers.grid_india._fetch_generation_data")
    def test_check_intensity_with_data(self, mock_fetch):
        mock_fetch.return_value = {"coal": 5000, "solar": 3000, "wind": 2000}
        is_green, intensity = grid_india.check_carbon_intensity("IN-SO", 500)
        assert is_green is not None
        assert intensity is not None

    def test_trend_returns_none(self):
        assert grid_india.get_history_trend("IN-NO") is None


# --- Eskom provider tests ---


class TestEskomProvider:
    def test_unknown_zone(self):
        assert_verdict(eskom.check_carbon_intensity("XX", 250), None, None)

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
        is_green, _intensity = eskom.check_carbon_intensity("ZA", 1000)
        assert is_green is True  # Even SA is green at 1000 threshold

    def test_trend_returns_none(self):
        assert eskom.get_history_trend("ZA") is None


# --- Auto presets tests ---


class TestAutoCleanestPreset:
    def test_auto_cleanest_expansion(self):
        result = check_grid.expand_auto_zones("auto:cleanest")
        assert result is not None
        assert len(result) == len(AUTO_CLEANEST_ZONES)
        assert {z["zone"] for z in result} == {z["zone"] for z in AUTO_CLEANEST_ZONES}

    def test_auto_cleanest_includes_free_providers(self):
        zone_names = {z["zone"] for z in check_grid.expand_auto_zones("auto:cleanest")}
        # Should include zones from each free provider
        assert "CISO" in zone_names  # EIA
        assert "GB" in zone_names or "GB-16" in zone_names  # UK
        assert "AU-TAS" in zone_names  # AEMO
        assert "BR-S" in zone_names  # ONS Brazil
        # ZA excluded: ~85% coal (~750 gCO2eq/kWh)
        assert "ZA" not in zone_names
        # Grid India excluded: geo-walled API, always fails from CI runners
        assert not any(z.startswith("IN-") for z in zone_names)

    def test_auto_cleanest_case_insensitive(self):
        assert check_grid.expand_auto_zones("AUTO:CLEANEST") is not None


class TestAutoEscapeCoalPreset:
    @pytest.mark.parametrize(
        "preset",
        ["auto:escape-coal", "auto:escape-coal:XX", "auto:escape-coal:ZZ-NOWHERE"],
        ids=["bare", "unknown_zone", "unlocatable_zone"],
    )
    def test_default_set(self, preset):
        result = check_grid.expand_auto_zones(preset)
        assert result is not None
        assert len(result) == len(AUTO_ESCAPE_COAL_ZONES)

    def test_escape_coal_specific_zone(self):
        result = check_grid.expand_auto_zones("auto:escape-coal:IN")
        assert result is not None
        # Should contain clean alternatives for India
        assert {z["zone"] for z in result} == set(ESCAPE_COAL_MAPPINGS["IN"])

    def test_escape_coal_china(self):
        result = check_grid.expand_auto_zones("auto:escape-coal:CN")
        assert result is not None
        zone_names = {z["zone"] for z in result}
        assert "NZ-NZN" in zone_names or "AU-TAS" in zone_names

    def test_escape_coal_poland(self):
        result = check_grid.expand_auto_zones("auto:escape-coal:PL")
        assert result is not None
        assert "NO-NO1" in {z["zone"] for z in result}

    def test_escape_coal_mappings_exist(self):
        """All dirty-grid mappings should have valid clean alternatives."""
        for dirty, alternatives in ESCAPE_COAL_MAPPINGS.items():
            assert len(alternatives) > 0, f"No alternatives for {dirty}"

    def test_escape_coal_dynamic_for_uncurated_zone(self):
        # Vietnam has no curated mapping but has coordinates, so it routes to a
        # short list of the nearest clean grids (5) instead of the full default set
        result = check_grid.expand_auto_zones("auto:escape-coal:VN")
        assert result is not None and len(result) == 5
        pool = {z["zone"] for z in AUTO_ESCAPE_COAL_ZONES}
        assert {z["zone"] for z in result} <= pool


class TestNearestCleanZones:
    def test_returns_nearest_first(self):
        result = nearest_clean_zones("VN")  # Hanoi
        assert len(result) == 5
        origin = _zone_latlon("VN")
        dists = [_haversine_km(origin, _zone_latlon(z["zone"])) for z in result]
        assert dists == sorted(dists)  # nearest-first ordering

    def test_respects_n(self):
        assert len(nearest_clean_zones("PL", n=3)) == 3

    def test_none_for_unlocatable(self):
        assert nearest_clean_zones("ZZ-NOWHERE") is None

    def test_haversine_known_distance(self):
        # London (51.5, -0.13) to Paris (48.85, 2.35) is ~340 km
        assert 320 < _haversine_km((51.5, -0.13), (48.85, 2.35)) < 360


class TestParseZonesAutoPresets:
    @pytest.mark.parametrize("preset", ["auto:cleanest", "auto:escape-coal"])
    def test_parse_expands_preset(self, preset):
        result = check_grid.parse_zones_input(preset)
        assert result is not None
        assert len(result) > 0

    def test_parse_auto_escape_coal_specific(self):
        result = check_grid.parse_zones_input("auto:escape-coal:ZA")
        assert result is not None
        assert "IS" in {z["zone"] for z in result}  # Iceland is in ZA escape list


# --- Carbon policy (org config) tests ---


class TestCarbonPolicy:
    def test_no_policy_file(self, monkeypatch):
        monkeypatch.setenv("CARBON_POLICY_PATH", "/nonexistent/path.yml")
        assert check_grid.load_carbon_policy() == {}

    def test_load_simple_policy(self, tmp_path, monkeypatch):
        policy_path = tmp_path / "policy.yml"
        policy_path.write_text(
            "max_carbon_intensity: 150\n"
            "grid_zones: 'auto:green'\n"
            "enable_forecast: true\n"
            "# This is a comment\n"
            "strategy: queue\n"
        )
        monkeypatch.setenv("CARBON_POLICY_PATH", str(policy_path))
        assert check_grid.load_carbon_policy() == {
            "max_carbon_intensity": "150",
            "grid_zones": "auto:green",
            "enable_forecast": "true",
            "strategy": "queue",
        }

    def test_policy_ignores_comments_and_blanks(self, tmp_path, monkeypatch):
        policy_path = tmp_path / "policy.yml"
        policy_path.write_text("# Comment\n\nmax_carbon_intensity: 200\n\n# Another comment\n")
        monkeypatch.setenv("CARBON_POLICY_PATH", str(policy_path))
        assert check_grid.load_carbon_policy() == {"max_carbon_intensity": "200"}


# --- Queue strategy tests ---


class TestQueueStrategy:
    @mock.patch("check_grid.check_multiple_zones")
    @mock.patch("check_grid.get_forecast")
    def test_queue_find_optimal_window_found(self, mock_forecast, _mock_check):
        mock_forecast.return_value = ("2026-03-10T14:00Z", 120)
        zones = [{"zone": "CISO", "runner_label": None}]
        assert check_grid.queue_find_optimal_window(zones, 250, 24) == (
            "CISO",
            "2026-03-10T14:00Z",
            120,
        )

    @mock.patch("check_grid.get_forecast")
    def test_queue_find_optimal_window_none(self, mock_forecast):
        mock_forecast.return_value = ("none_in_forecast", None)
        zones = [{"zone": "PJM", "runner_label": None}]
        zone, _when, _intensity = check_grid.queue_find_optimal_window(zones, 250, 24)
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
        zone, _when, intensity = check_grid.queue_find_optimal_window(zones, 250, 24)
        assert zone == "BPAT"  # Lower intensity
        assert intensity == 80


@mock.patch("check_grid.check_multiple_zones")
@mock.patch("check_grid.set_output")
@mock.patch("check_grid.write_job_summary")
class TestQueueStrategyMain:
    """Exercise the queue-strategy branch of main() end to end."""

    @mock.patch("check_grid.trigger_workflow")
    def test_queue_already_green_dispatches_now(
        self, mock_trigger, _mock_summary, mock_output, mock_multi
    ):
        # A zone is already green: dispatch immediately, optimal_dispatch_at=now
        mock_multi.return_value = ("CISO", 90, None, [])
        os.environ.update(
            GRID_ZONES="CISO,GB",
            STRATEGY="queue",
            WORKFLOW_ID="heavy.yml",
            GITHUB_TOKEN="tok",
            TARGET_REPO="owner/repo",
        )

        assert main_exit_code() == 0
        out = outputs_of(mock_output)
        assert out["optimal_dispatch_at"] == "now"
        assert out["grid_clean"] == "true"
        mock_trigger.assert_called_once()

    @mock.patch("check_grid.queue_find_optimal_window")
    def test_queue_finds_future_window(self, mock_window, _mock_summary, mock_output, mock_multi):
        # Nothing green now, but a future window exists within the deadline
        mock_multi.return_value = (None, None, None, [])
        mock_window.return_value = ("CISO", "2026-03-10T14:00Z", 120)
        os.environ.update(GRID_ZONES="CISO,GB", STRATEGY="queue", WORKFLOW_ID="")

        assert main_exit_code() == 0
        out = outputs_of(mock_output)
        assert out["optimal_dispatch_at"] == "2026-03-10T14:00Z"
        assert out["optimal_zone"] == "CISO"
        assert out["grid_clean"] == "false"

    @mock.patch("check_grid.queue_find_optimal_window")
    def test_queue_no_window_no_fail(self, mock_window, _mock_summary, mock_output, mock_multi):
        mock_multi.return_value = (None, None, None, [])
        mock_window.return_value = (None, None, None)
        os.environ.update(GRID_ZONES="CISO,GB", STRATEGY="queue", WORKFLOW_ID="")

        assert main_exit_code() == 0
        assert outputs_of(mock_output)["optimal_dispatch_at"] == "none_in_deadline"

    @mock.patch("check_grid.queue_find_optimal_window")
    def test_queue_no_window_fail_on_api_error(
        self, mock_window, _mock_summary, _mock_output, mock_multi
    ):
        # No window + fail_on_api_error: must exit non-zero
        mock_multi.return_value = (None, None, None, [])
        mock_window.return_value = (None, None, None)
        os.environ.update(
            GRID_ZONES="CISO,GB", STRATEGY="queue", FAIL_ON_API_ERROR="true", WORKFLOW_ID=""
        )

        assert main_exit_code() == 1


@mock.patch("check_grid.check_carbon_intensity")
@mock.patch("check_grid.set_output")
@mock.patch("check_grid.write_job_summary")
class TestSingleZoneDirtyMain:
    """Exercise the single-zone dirty and API-error paths of main()."""

    @mock.patch("check_grid.handle_dirty_grid")
    def test_dirty_grid_sets_outputs_no_dispatch(
        self, mock_dirty, _mock_summary, _mock_output, mock_check
    ):
        mock_check.return_value = (False, 480)
        mock_dirty.return_value = ("stable", "2026-03-10T03:00Z", 90)
        os.environ.update(GRID_ZONE="AU-NSW", WORKFLOW_ID="")

        assert main_exit_code() == 0
        mock_dirty.assert_called_once()

    def test_api_error_skips_without_fail_flag(self, _mock_summary, mock_output, mock_check):
        mock_check.return_value = (None, None)
        os.environ.update(GRID_ZONE="CISO", WORKFLOW_ID="")

        assert main_exit_code() == 0
        out = outputs_of(mock_output)
        assert out["grid_clean"] == "false"
        assert out["carbon_intensity"] == "unknown"

    def test_api_error_fails_with_flag(self, _mock_summary, _mock_output, mock_check):
        mock_check.return_value = (None, None)
        os.environ.update(GRID_ZONE="CISO", FAIL_ON_API_ERROR="true", WORKFLOW_ID="")

        assert main_exit_code() == 1

    @mock.patch("check_grid.smart_wait_single")
    def test_smart_wait_invoked_when_dirty(self, mock_wait, _mock_summary, mock_output, mock_check):
        # Dirty now + max_wait set: smart_wait_single runs and turns it green
        mock_check.return_value = (False, 400)
        mock_wait.return_value = (True, 90, 12.0)
        os.environ.update(GRID_ZONE="CISO", MAX_WAIT="60", WORKFLOW_ID="")

        # Green single-zone path returns normally (no sys.exit)
        check_grid.main()
        mock_wait.assert_called_once()
        assert outputs_of(mock_output)["grid_clean"] == "true"


# --- Inline mode simplification test ---


class TestInlineModeDispatch:
    @mock.patch("check_grid.check_carbon_intensity")
    def test_inline_no_workflow_id(self, mock_check, github_output):
        """Inline mode should work without workflow_id or github_token."""
        mock_check.return_value = (True, 100)
        os.environ["GRID_ZONE"] = "GB"
        os.environ.pop("WORKFLOW_ID", None)
        os.environ.pop("GITHUB_TOKEN", None)
        # Should not raise, since inline mode doesn't need token
        check_grid.main()
        assert "grid_clean=true" in github_output.read_text()


# ---------------------------------------------------------------------------
# Setup wizard tests
# ---------------------------------------------------------------------------


class TestSetupWizard:
    def test_wizard_names_every_dispatcher_provider(self):
        """The wizard must know every provider the dispatcher routes to, so it
        can't silently mislabel a zone (e.g. after a new provider is added)."""
        for p in check_grid._PROVIDER_MODULES:
            assert p in setup_wizard._PROVIDER_NAMES, f"Wizard missing display name for {p}"

    @pytest.mark.parametrize(
        ("module_name", "zone", "verdict", "provider_name"),
        [
            pytest.param("canada", "CA-QC", (True, 30), "Canada", id="canada"),
            pytest.param("taiwan", "TW", (False, 527), "Taipower", id="taiwan"),
            pytest.param("uk", "GB", (True, 100), None, id="uk"),
        ],
    )
    def test_zone_ok(self, module_name, zone, verdict, provider_name):
        with mock.patch(f"providers.{module_name}.check_carbon_intensity", return_value=verdict):
            result = setup_wizard.test_zone(zone)
        assert result["status"] == "ok"
        assert result["intensity"] == verdict[1]
        if provider_name is not None:
            assert provider_name in result["provider"]

    @mock.patch("providers.eia.check_carbon_intensity", return_value=(None, None))
    def test_zone_error_on_no_data(self, _mock_check):
        result = setup_wizard.test_zone("CISO")
        assert result["status"] == "error"
        assert "no data" in result["error"]

    @mock.patch("providers.uk.check_carbon_intensity", side_effect=RuntimeError("boom"))
    def test_zone_error_on_exception(self, _mock_check):
        result = setup_wizard.test_zone("GB")
        assert result["status"] == "error"
        assert "boom" in result["error"]

    def test_zone_test_entsoe_skipped_without_token(self):
        result = setup_wizard.test_zone("DE", entsoe_token="")
        # DE without entsoe token should use Open-Meteo (if coordinates exist)
        # or be skipped for ENTSO-E
        assert result["status"] in ("ok", "skipped", "error")

    def test_zone_test_emaps_skipped_without_token(self):
        # Use a fake zone that only Electricity Maps can handle (no coordinates)
        result = setup_wizard.test_zone("XX-NOCOORDS", emaps_api_key="")
        assert result["status"] == "skipped"
        assert "portal.electricitymaps.com" in result["error"]

    @mock.patch("providers.open_meteo.check_carbon_intensity", return_value=(True, 300))
    def test_zone_with_open_meteo_fallback(self, _mock_check):
        # SG has Open-Meteo coordinates, should work without emaps token
        assert setup_wizard.test_zone("SG", emaps_api_key="")["status"] == "ok"


# ---------------------------------------------------------------------------
# Cloud region mapping completeness
# ---------------------------------------------------------------------------


class TestCloudRegionMappingCompleteness:
    # Every curated preset's zones should have an EXPLICIT cloud-region mapping:
    # get_cloud_region() falls back to a default (us-east-1) for unmapped zones,
    # so we assert membership in the mapping dicts rather than "is not None",
    # which would pass even for a totally unmapped zone
    @pytest.mark.parametrize(
        ("cloud", "mapping"),
        [
            pytest.param("AWS", ZONE_TO_AWS_REGION, id="aws"),
            pytest.param("GCP", ZONE_TO_GCP_REGION, id="gcp"),
            pytest.param("Azure", ZONE_TO_AZURE_REGION, id="azure"),
        ],
    )
    def test_all_preset_zones_have_mapping(self, cloud, mapping):
        zones = set()
        for preset in (AUTO_GREEN_ZONES, AUTO_CLEANEST_ZONES, AUTO_GREEN_ZONES_FULL):
            zones.update(e["zone"] for e in preset)
        missing = [z for z in zones if z not in mapping]
        assert not missing, f"Preset zones missing {cloud} region: {sorted(missing)}"

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
    @pytest.mark.parametrize(
        "provider",
        [
            PROVIDER_UK,
            PROVIDER_EIA,
            PROVIDER_AEMO,
            PROVIDER_GRID_INDIA,
            PROVIDER_ONS_BRAZIL,
            PROVIDER_ESKOM,
            PROVIDER_ENTSOE,
            PROVIDER_OPEN_METEO,
            PROVIDER_ELECTRICITY_MAPS,
        ],
    )
    def test_provider_in_check_grid_registry(self, provider):
        """All provider constants should be in check_grid's module registry."""
        assert provider in check_grid._PROVIDER_MODULES, f"Missing {provider} in _PROVIDER_MODULES"

    def test_all_provider_modules_have_required_functions(self):
        """Every provider module exposes the three required provider functions."""
        for provider_id, module in check_grid._PROVIDER_MODULES.items():
            for fn in ("check_carbon_intensity", "get_forecast", "get_history_trend"):
                assert hasattr(module, fn), f"{provider_id} missing {fn}"


# ---------------------------------------------------------------------------
# Fallback chain tests
# ---------------------------------------------------------------------------


class TestFallbackChain:
    @mock.patch("check_grid.open_meteo.check_carbon_intensity", return_value=(True, 200))
    @mock.patch("check_grid.eia.check_carbon_intensity", return_value=(None, None))
    def test_eia_failure_falls_back_to_open_meteo(self, _mock_eia, mock_meteo, monkeypatch):
        """When EIA fails for a zone with Open-Meteo coordinates, fallback works."""
        # CISO doesn't have Open-Meteo coords (it's EIA), so a zone that hits
        # EIA and also has coords doesn't exist naturally. Add coords for CISO
        # here to test the generic fallback path
        monkeypatch.setitem(open_meteo.ZONE_COORDINATES, "CISO", (37.8, -122.4))
        result = check_grid.check_carbon_intensity("CISO", 250, PROVIDER_EIA, eia_api_key="")
        assert_verdict(result, True, 200)
        mock_meteo.assert_called_once()

    @mock.patch("check_grid.open_meteo.check_carbon_intensity")
    @mock.patch("check_grid.uk.check_carbon_intensity", return_value=(True, 150))
    def test_no_fallback_when_primary_succeeds(self, _mock_uk, mock_meteo):
        """Fallback should NOT trigger when primary provider succeeds."""
        result = check_grid.check_carbon_intensity("GB", 250, PROVIDER_UK)
        assert_verdict(result, True, 150)
        mock_meteo.assert_not_called()

    @mock.patch("check_grid.open_meteo.check_carbon_intensity")
    def test_no_double_fallback_for_open_meteo(self, mock_meteo):
        """Open-Meteo itself should not trigger fallback to Open-Meteo."""
        mock_meteo.return_value = (None, None)
        is_green, _intensity = check_grid.check_carbon_intensity("IS", 250, PROVIDER_OPEN_METEO)
        assert is_green is None
        # Should be called once (primary only, no self-fallback)
        assert mock_meteo.call_count == 1


# ---------------------------------------------------------------------------
# Cloud auto-detection tests
# ---------------------------------------------------------------------------

_CLOUD_ENV_KEYS = [
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


@pytest.fixture
def no_cloud_env(monkeypatch):
    """Strip every env var the cloud detector reads."""
    for key in _CLOUD_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.usefixtures("no_cloud_env")
class TestCloudAutoDetection:
    @pytest.mark.parametrize(
        ("env", "zone", "source_tag"),
        [
            pytest.param({"AWS_REGION": "us-west-2"}, "BPAT", "AWS", id="aws_region"),
            pytest.param({"AWS_DEFAULT_REGION": "eu-west-2"}, "GB", None, id="aws_default_region"),
            pytest.param({"GOOGLE_CLOUD_REGION": "europe-west9"}, "FR", "GCP", id="gcp_region"),
            pytest.param({"AZURE_REGION": "japaneast"}, "JP-TK", "Azure", id="azure_region"),
            pytest.param(
                {"CLOUD_REGION_OVERRIDE": "ap-southeast-1"},
                "SG",
                "CLOUD_REGION_OVERRIDE",
                id="cloud_region_override",
            ),
        ],
    )
    def test_detects_zone_from_env(self, monkeypatch, env, zone, source_tag):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        detected, source = detect_cloud_zone()
        assert detected == zone
        if source_tag is not None:
            assert source_tag in source

    def test_no_cloud_env_returns_none(self):
        assert detect_cloud_zone() == (None, None)

    def test_unknown_region_returns_none(self, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "xx-unknown-99")
        zone, _source = detect_cloud_zone()
        assert zone is None


class TestReverseRegionMappings:
    @pytest.mark.parametrize(
        ("cloud", "reverse", "major"),
        [
            pytest.param(
                "AWS",
                AWS_REGION_TO_ZONE,
                ["us-west-1", "us-west-2", "us-east-1", "eu-west-2", "ap-northeast-1"],
                id="aws",
            ),
            pytest.param(
                "GCP",
                GCP_REGION_TO_ZONE,
                ["us-west1", "us-east4", "europe-west2", "asia-northeast1"],
                id="gcp",
            ),
            pytest.param(
                "Azure",
                AZURE_REGION_TO_ZONE,
                ["eastus", "westus2", "uksouth", "japaneast"],
                id="azure",
            ),
        ],
    )
    def test_reverse_map_covers_major_regions(self, cloud, reverse, major):
        for region in major:
            assert region in reverse, f"Missing {cloud} reverse: {region}"

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


@pytest.mark.usefixtures("no_cloud_env")
class TestAutoDetectPreset:
    def test_auto_detect_expansion_with_aws_region(self, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "us-west-1")
        result = check_grid.expand_auto_zones("auto:detect")
        assert result is not None
        assert len(result) == 1
        assert result[0]["zone"] == "CISO"

    def test_auto_detect_fallback_to_cleanest(self):
        """auto:detect falls back to auto:cleanest when no cloud env is set."""
        result = check_grid.expand_auto_zones("auto:detect")
        assert result is not None
        assert len(result) == len(AUTO_CLEANEST_ZONES)


class TestZeroConfigDefault:
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_no_zone_input_uses_auto_detect(
        self, _mock_summary, mock_output, mock_check, monkeypatch
    ):
        """When no zone is specified, should use auto:detect."""
        mock_check.return_value = (True, 100)
        for key in ("GRID_ZONE", "GRID_ZONES", "CARBON_POLICY_PATH"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("WORKFLOW_ID", "")
        # Set a cloud region so auto:detect finds something
        monkeypatch.setenv("AWS_REGION", "us-west-2")
        check_grid.main()
        assert outputs_of(mock_output)["grid_clean"] == "true"


# ---------------------------------------------------------------------------
# Forecast heuristic tests (Grid India, ONS Brazil, Eskom time-of-day curves)
# ---------------------------------------------------------------------------


class TestHeuristicForecasts:
    @pytest.mark.parametrize(
        ("module", "zone", "threshold"),
        [
            pytest.param(grid_india, "IN-SO", 500, id="india_south_high_threshold"),
            pytest.param(grid_india, "IN-SO", 1000, id="india_south_very_high_threshold"),
            pytest.param(grid_india, "IN-NO", 1000, id="india_north_very_high_threshold"),
            pytest.param(ons_brazil, "BR-S", 500, id="brazil_south_high_threshold"),
            # BR-S (hydro) should be green at moderate threshold
            pytest.param(ons_brazil, "BR-S", 200, id="brazil_hydro_moderate_threshold"),
            pytest.param(ons_brazil, "BR-NE", 10, id="brazil_northeast_tiny_threshold"),
            # SA midday is ~650, so with 800 threshold it should find a window
            pytest.param(eskom, "ZA", 800, id="south_africa_high_threshold"),
        ],
    )
    def test_window_is_none_or_within_threshold(self, module, zone, threshold):
        # Either already green (None), no window, or a window at or under the
        # threshold, never a crash, at any hour
        dt, intensity = module.get_forecast(zone, threshold)
        assert dt is None or isinstance(dt, str)
        if dt is not None and dt != "none_in_forecast":
            assert intensity is not None
            assert intensity <= threshold

    @pytest.mark.parametrize(
        ("module", "zone", "threshold"),
        [
            # SA grid (650+ gCO2eq/kWh) will never be green at 250
            pytest.param(eskom, "ZA", 250, id="south_africa_coal_never_green"),
            pytest.param(grid_india, "IN-NO", 50, id="india_north_tiny_threshold"),
        ],
    )
    def test_no_window_below_floor(self, module, zone, threshold):
        assert module.get_forecast(zone, threshold) == ("none_in_forecast", None)


# ---------------------------------------------------------------------------
# auto:nearest preset tests
# ---------------------------------------------------------------------------


class TestAutoNearestPreset:
    @pytest.mark.parametrize(
        ("tz", "present"),
        [
            pytest.param("UTC+0", {"GB-16", "GB"}, id="utc_resolves_to_uk"),
            # Grid India itself is geo-walled, so UTC+5.5 maps to Australian clean zones
            pytest.param("UTC+5.5", {"AU-TAS"}, id="india_offset_resolves_to_australia"),
            pytest.param("UTC-8", {"CISO"}, id="us_west"),
            # Etc/GMT-5 means UTC+5 (inverted sign)
            pytest.param("Etc/GMT-5", {"AU-TAS"}, id="etc_gmt_inverted_sign"),
        ],
    )
    def test_tz_resolves_to_reachable_zones(self, monkeypatch, tz, present):
        monkeypatch.setenv("TZ", tz)
        zone_ids = [z["zone"] for z in check_grid.expand_auto_zones("auto:nearest")]
        assert present & set(zone_ids)
        assert not any(z.startswith("IN-") for z in zone_ids)

    def test_nearest_fallback_to_cleanest(self, monkeypatch):
        """No TZ env var falls back to system timezone (which resolves to some zones)."""
        monkeypatch.delenv("TZ", raising=False)
        assert len(check_grid.expand_auto_zones("auto:nearest")) > 0


class TestDetectUtcOffset:
    @pytest.mark.parametrize(
        ("tz", "offset"),
        [
            pytest.param("UTC", 0, id="utc_zero"),
            pytest.param("UTC+5.5", 5.5, id="utc_plus_half_hour"),
            pytest.param("UTC-8", -8, id="utc_minus"),
            pytest.param("GMT+3", 3, id="gmt_plus"),
            # Etc/GMT offsets are inverted: Etc/GMT-5 = UTC+5
            pytest.param("Etc/GMT-5", 5, id="etc_gmt_inverted"),
        ],
    )
    def test_parses_tz(self, monkeypatch, tz, offset):
        monkeypatch.setenv("TZ", tz)
        assert check_grid._detect_utc_offset() == offset

    def test_system_fallback(self, monkeypatch):
        """With no TZ env, should fall back to system time."""
        monkeypatch.delenv("TZ", raising=False)
        offset = check_grid._detect_utc_offset()
        assert offset is not None
        assert -12 <= offset <= 14


# ---------------------------------------------------------------------------
# Cron schedule optimizer tests
# ---------------------------------------------------------------------------


class TestSuggestGreenCron:
    @pytest.mark.parametrize(
        ("zone", "phrase"),
        [
            pytest.param("CISO", "solar peak", id="solar_zone_midday"),
            pytest.param("BPAT", "off-peak", id="hydro_zone_off_peak"),
            pytest.param("GB-16", "wind peak", id="wind_zone_night"),
        ],
    )
    def test_description_matches_zone_type(self, zone, phrase):
        cron, desc = check_grid.suggest_green_cron(zone)
        assert cron is not None
        assert phrase in desc

    def test_unknown_zone_returns_none(self):
        assert check_grid.suggest_green_cron("UNKNOWN-ZONE-XYZ") == (None, None)

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
        for offset in range(-10, 14):
            assert offset in NEAREST_ZONES_BY_OFFSET, f"Missing offset {offset}"

    def test_half_hour_offsets(self):
        assert 5.5 in NEAREST_ZONES_BY_OFFSET  # India
        assert 9.5 in NEAREST_ZONES_BY_OFFSET  # Australia Central


# ---------------------------------------------------------------------------
# Guarded env parsing helpers
# ---------------------------------------------------------------------------


class TestEnvParsingHelpers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            pytest.param(None, 250, id="default_when_unset"),
            pytest.param("", 250, id="default_when_empty"),
            pytest.param("123.5", 123.5, id="parses_value"),
        ],
    )
    def test_env_float(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("MAX_CARBON", raising=False)
        else:
            monkeypatch.setenv("MAX_CARBON", raw)
        assert check_grid._env_float("MAX_CARBON", 250) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [pytest.param(None, 0, id="default_when_unset"), pytest.param("30", 30, id="parses_value")],
    )
    def test_env_int(self, monkeypatch, raw, expected):
        if raw is None:
            monkeypatch.delenv("MAX_WAIT", raising=False)
        else:
            monkeypatch.setenv("MAX_WAIT", raw)
        assert check_grid._env_int("MAX_WAIT", 0) == expected

    @pytest.mark.parametrize(
        ("parser", "name", "raw"),
        [
            pytest.param(check_grid._env_float, "MAX_CARBON", "notanumber", id="float"),
            pytest.param(check_grid._env_int, "MAX_WAIT", "soon", id="int"),
        ],
    )
    def test_exits_on_malformed(self, monkeypatch, parser, name, raw):
        monkeypatch.setenv(name, raw)
        with pytest.raises(SystemExit) as exc:
            parser(name, 0)
        assert exc.value.code == check_grid.EXIT_FAILURE

    def test_env_float_raw_overrides_env(self, monkeypatch):
        # Explicit raw string takes precedence (policy-fallback path)
        monkeypatch.setenv("MAX_CARBON", "999")
        assert check_grid._env_float("MAX_CARBON", 250, "100") == 100

    def test_env_float_raw_empty_uses_default(self):
        assert check_grid._env_float("DEADLINE_HOURS", 24, "") == 24


# ---------------------------------------------------------------------------
# get_forecast forwards eia_api_key
# ---------------------------------------------------------------------------


class TestGetForecastEiaKey:
    @mock.patch("providers.eia.get_forecast", create=True)
    def test_eia_key_forwarded_to_extra_args(self, mock_fc):
        # EIA resolves the eia_api_key from the extra-args dict. Verify it
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
        assert check_grid.get_forecast("CISO", 250, PROVIDER_EIA, "") == (None, None)
        mock_gs.assert_not_called()


# ---------------------------------------------------------------------------
# _emit_green_result shared helper
# ---------------------------------------------------------------------------


@mock.patch("check_grid.trigger_workflow")
@mock.patch("check_grid.write_job_summary")
@mock.patch("check_grid.set_output")
class TestEmitGreenResult:
    def test_sets_grid_clean_and_co2_saved(self, mock_output, mock_summary, mock_trigger):
        # Low intensity vs global average produces positive savings
        check_grid._emit_green_result(
            "CISO", 50, None, 250, False, "", "", "", "main", "", "", "run-1"
        )
        out = outputs_of(mock_output)
        assert out["grid_clean"] == "true"
        assert out["carbon_intensity"] == "50"
        assert "co2_saved_grams" in out
        assert float(out["co2_saved_grams"]) > 0
        mock_summary.assert_called_once()
        # Inline mode (no dispatch) should not trigger a workflow
        mock_trigger.assert_not_called()

    def test_dispatch_mode_triggers_workflow(self, _mock_output, _mock_summary, mock_trigger):
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
    def test_quebec_is_fixed_estimate(self):
        assert_verdict(canada.check_carbon_intensity("CA-QC", 250), True, 30)

    @pytest.mark.parametrize(
        ("zone", "body", "expected"),
        [
            # nuclear 8000*12 + hydro 4000*24 + gas 1000*490 + wind 500*12
            # = 96000 + 96000 + 490000 + 6000 = 688000 / 13500 = 51
            pytest.param("CA-ON", _IESO_XML, (True, 51), id="ieso_ontario_parse"),
            # coal 800*820 + gas 1500*490 + wind 300*12 = 656000+735000+3600
            # = 1394600 / 2600 = 536
            pytest.param("CA-AB", _AESO_HTML, (False, 536), id="aeso_alberta_parse"),
        ],
    )
    @mock.patch("providers.base._SESSION.get")
    def test_parses_live_mix(self, mock_get, zone, body, expected):
        mock_get.return_value = mock.Mock(status_code=200, text=body)
        assert_verdict(canada.check_carbon_intensity(zone, 250), *expected)

    @mock.patch("providers.base._SESSION.get")
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
        intensity = base.mix_to_intensity(
            mix, canada.CANADA_EMISSION_FACTORS, canada.CANADA_STORAGE_FUELS
        )
        assert intensity == 24


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
    @mock.patch("providers.base._SESSION.get")
    def test_parse_generation(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=200, content=_TAIPOWER_JSON)
        # coal 5000*820 + lng 3000*490 + nuclear 2000*12 + solar 1000*45
        # = 4100000 + 1470000 + 24000 + 45000 = 5639000 / 11000 = 513
        # (the "Load" row is skipped as storage charging)
        assert_verdict(taiwan.check_carbon_intensity("TW", 250), False, 513)

    @mock.patch("providers.base._SESSION.get")
    def test_api_failure_returns_none(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=500, content=b"", text="err")
        assert taiwan.check_carbon_intensity("TW", 250) == (None, None)

    def test_unknown_zone(self):
        assert taiwan.check_carbon_intensity("TW-XX", 250) == (None, None)

    @pytest.mark.parametrize(
        ("label", "fuel"),
        [
            ("Coal", "coal"),
            ("LNG", "natural_gas"),
            ("Energy Storage Load", None),
            ("Energy Storage", "battery"),
        ],
    )
    def test_fuel_mapping(self, label, fuel):
        assert taiwan._fuel_of(label) == fuel

    def test_no_forecast_or_trend(self):
        assert taiwan.get_forecast("TW", 250) == (None, None)
        assert taiwan.get_history_trend("TW") is None


# ---------------------------------------------------------------------------
# Flow tracing / consumption-based intensity (EU)
# ---------------------------------------------------------------------------


class TestFlowTracing:
    def test_solver_attributes_imports(self):
        # IT-NO imports clean FR nuclear -> reads cleaner. NL imports DE coal -> dirtier
        prod_mw = {"FR": 50000, "IT-NO": 20000, "DE": 60000, "NL": 10000}
        prod_int = {"FR": 55, "IT-NO": 380, "DE": 420, "NL": 350}
        flows = {("FR", "IT-NO"): 4000, ("DE", "NL"): 8000}
        cons = flow_tracing.trace_consumption_intensity(prod_mw, prod_int, flows)
        assert cons["FR"] == 55.0  # exporter unchanged
        assert cons["IT-NO"] < prod_int["IT-NO"]  # importing clean -> lower
        assert cons["NL"] > prod_int["NL"]  # importing dirty -> higher

    def test_solver_empty(self):
        assert flow_tracing.trace_consumption_intensity({}, {}, {}) == {}

    def test_solver_ignores_unknown_and_zero_flows(self):
        prod_mw = {"FR": 1000}
        prod_int = {"FR": 50}
        # flow from an unknown zone and a zero flow are both ignored
        flows = {("XX", "FR"): 500, ("FR", "FR"): 0}
        assert flow_tracing.trace_consumption_intensity(prod_mw, prod_int, flows) == {"FR": 50.0}

    @pytest.mark.parametrize(
        ("zone", "traced", "is_green", "intensity", "expected"),
        [
            pytest.param(
                "IT-NO", {"IT-NO": 326.0}, False, 380, (False, 326), id="traced_zone_overridden"
            ),
            pytest.param("FR", {}, True, 55, (True, 55), id="no_value_falls_back"),
            # production 280 (dirty), consumption 240 (green) at threshold 250
            pytest.param("FR", {"FR": 240.0}, False, 280, (True, 240), id="flips_verdict_to_green"),
        ],
    )
    @mock.patch("providers.flow_tracing.compute_consumption_intensities")
    def test_apply_override(self, mock_compute, zone, traced, is_green, intensity, expected):
        mock_compute.return_value = traced
        result = check_grid._apply_consumption_intensity(zone, 250, is_green, intensity, "tok")
        assert_verdict(result, *expected)

    def test_apply_override_untraced_zone_unchanged(self):
        result = check_grid._apply_consumption_intensity("CISO", 250, True, 100, "tok")
        assert_verdict(result, True, 100)

    @mock.patch("providers.flow_tracing.compute_consumption_intensities")
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_main_consumption_mode_end_to_end(
        self, _mock_summary, mock_output, mock_check, mock_compute
    ):
        mock_check.return_value = (False, 380)  # FR production dirty
        mock_compute.return_value = {"FR": 240.0}  # consumption green
        os.environ.update(
            GRID_ZONE="FR", ENTSOE_TOKEN="tok", CONSUMPTION_BASED="true", WORKFLOW_ID=""
        )

        check_grid.main()
        out = outputs_of(mock_output)
        assert out["grid_clean"] == "true"
        assert out["carbon_intensity"] == "240"

    @mock.patch("providers.flow_tracing.compute_consumption_intensities")
    @mock.patch("check_grid.check_carbon_intensity")
    @mock.patch("check_grid.set_output")
    @mock.patch("check_grid.write_job_summary")
    def test_main_consumption_off_uses_production(
        self, _mock_summary, _mock_output, mock_check, mock_compute
    ):
        mock_check.return_value = (True, 90)
        os.environ.update(GRID_ZONE="FR", ENTSOE_TOKEN="tok", WORKFLOW_ID="")
        os.environ.pop("CONSUMPTION_BASED", None)  # default off

        check_grid.main()
        mock_compute.assert_not_called()  # never computed when mode is off

    def test_flow_parse_latest(self):
        xml = (
            "<TimeSeries><Period>"
            "<Point><position>1</position><quantity>1200</quantity></Point>"
            "<Point><position>2</position><quantity>1500</quantity></Point>"
            "</Period></TimeSeries>"
        )
        assert entsoe._parse_flow_latest(xml) == 1500.0
        assert entsoe._parse_flow_latest("") is None
