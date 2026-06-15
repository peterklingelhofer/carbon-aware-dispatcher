"""Tests for the WattTime marginal-emissions provider."""

from unittest import mock

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

    @mock.patch("providers.watttime.base.request")
    def test_v3_data_shape(self, mock_request):
        mock_request.return_value = {
            "data": [{"point_time": "2026-06-15T00:00:00Z", "value": 18}],
            "meta": {"signal_type": "co2_moer"},
        }
        assert watttime.get_marginal_index("CAISO_NORTH", "tok") == 18

    @mock.patch("providers.watttime.base.request")
    def test_flat_value_shape(self, mock_request):
        mock_request.return_value = {"value": 42.6}
        assert watttime.get_marginal_index("CAISO_NORTH", "tok") == 43

    @mock.patch("providers.watttime.base.request")
    def test_v2_percent_fallback(self, mock_request):
        mock_request.return_value = {"percent": "75"}
        assert watttime.get_marginal_index("CAISO_NORTH", "tok") == 75

    @mock.patch("providers.watttime.base.request")
    def test_empty_response(self, mock_request):
        mock_request.return_value = None
        assert watttime.get_marginal_index("CAISO_NORTH", "tok") is None

    @mock.patch("providers.watttime.base.request")
    def test_unparseable_value(self, mock_request):
        mock_request.return_value = {"data": [{"value": "n/a"}]}
        assert watttime.get_marginal_index("CAISO_NORTH", "tok") is None
