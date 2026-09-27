"""Tests for the outbound webhook notifications."""

from unittest import mock

import pytest

import notify


class TestParseEvents:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", {"green", "exceeded"}),  # default when empty
            ("dirty, always", {"dirty", "always"}),
            ("green,bogus", {"green"}),  # unknown tokens dropped
            ("nonsense", {"green", "exceeded"}),  # all unknown falls back to the default
        ],
    )
    def test_parse(self, raw, expected):
        assert notify.parse_events(raw) == expected


class TestShouldNotify:
    @pytest.mark.parametrize(
        ("events", "is_green", "exceeded", "expected"),
        [
            ({"always"}, False, False, True),
            ({"green"}, True, False, True),
            ({"green"}, False, False, False),
            ({"dirty"}, False, False, True),
            ({"exceeded"}, True, True, True),
            ({"green"}, False, True, False),
        ],
    )
    def test_matches_subscribed_events(self, events, is_green, exceeded, expected):
        assert notify.should_notify(events, is_green, exceeded) is expected


class TestFormatPayload:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://hooks.slack.com/services/xxx", {"text": "hi"}),
            ("https://discord.com/api/webhooks/xxx", {"content": "hi"}),
            ("https://example.com/hook", {"event": "carbon-aware-dispatcher", "message": "hi"}),
        ],
    )
    def test_shape_follows_destination(self, url, expected):
        assert notify.format_payload(url, "hi") == expected


class TestBuildMessage:
    def test_green_dispatch(self):
        msg = notify.build_message("CISO", 80, True, "green", None)
        assert "dispatching" in msg
        assert "CISO" in msg
        assert "80 gCO2eq/kWh" in msg
        assert "tier green" in msg

    def test_dry_run(self):
        msg = notify.build_message("GB", 90, True, "unknown", None, dry_run=True)
        assert "would dispatch" in msg
        assert "tier" not in msg  # unknown tier omitted

    def test_budget_exceeded_note(self):
        msg = notify.build_message("PL", 600, False, "red", {"exceeded": True, "used_pct": 120})
        assert "EXCEEDED" in msg


class TestSend:
    def test_no_url(self):
        assert notify.send("", "GB", 50, True, "green", None) is False

    @mock.patch("notify.base.request")
    def test_success(self, mock_request):
        mock_request.return_value = "ok"
        assert notify.send("https://example.com/h", "GB", 50, True, "green", None) is True
        assert mock_request.call_count == 1

    @mock.patch("notify.base.request")
    def test_failure_returns_false(self, mock_request):
        mock_request.return_value = None
        assert notify.send("https://example.com/h", "GB", 50, True, "green", None) is False
