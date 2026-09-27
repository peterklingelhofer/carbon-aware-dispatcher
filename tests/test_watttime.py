"""Tests for the WattTime marginal-emissions provider."""

from unittest import mock

import pytest

from providers import watttime


class TestLogin:
    def test_missing_creds(self):
        assert watttime.login("", "") is None

    @mock.patch("providers.watttime.base.request")
    def test_returns_token(self, mock_request):
        mock_request.return_value = {"token": "abc123"}
        assert watttime.login("u", "p") == "abc123"
        # basic auth header is sent
        _, kwargs = mock_request.call_args
        assert kwargs["headers"]["Authorization"].startswith("Basic ")

    @mock.patch("providers.watttime.base.request")
    def test_failed_login(self, mock_request):
        mock_request.return_value = None
        assert watttime.login("u", "p") is None


class TestGetMarginalIndex:
    def test_no_token(self):
        assert watttime.get_marginal_index("CAISO_NORTH", "") is None

    @pytest.mark.parametrize(
        "payload,expected",
        [
            (
                {
                    "data": [{"point_time": "2026-06-15T00:00:00Z", "value": 18}],
                    "meta": {"signal_type": "co2_moer"},
                },
                18,
            ),
            ({"value": 42.6}, 43),
            ({"percent": "75"}, 75),
            (None, None),
            ({"data": [{"value": "n/a"}]}, None),
        ],
        ids=[
            "v3_data_shape",
            "flat_value_shape",
            "v2_percent_fallback",
            "empty_response",
            "unparseable_value",
        ],
    )
    @mock.patch("providers.watttime.base.request")
    def test_response_shape_handling(self, mock_request, payload, expected):
        mock_request.return_value = payload
        assert watttime.get_marginal_index("CAISO_NORTH", "tok") == expected
