"""Tests for the outbound webhook notifications."""

from unittest import mock

import notify


class TestParseEvents:
    def test_default_when_empty(self):
        assert notify.parse_events("") == {"green", "exceeded"}

    def test_parsed(self):
        assert notify.parse_events("dirty, always") == {"dirty", "always"}

    def test_unknown_tokens_dropped(self):
        assert notify.parse_events("green,bogus") == {"green"}

    def test_all_unknown_falls_back(self):
        assert notify.parse_events("nonsense") == {"green", "exceeded"}


class TestShouldNotify:
    def test_always(self):
        assert notify.should_notify({"always"}, False, False) is True

    def test_green_match(self):
        assert notify.should_notify({"green"}, True, False) is True
        assert notify.should_notify({"green"}, False, False) is False

    def test_dirty_match(self):
        assert notify.should_notify({"dirty"}, False, False) is True

    def test_exceeded_match(self):
        assert notify.should_notify({"exceeded"}, True, True) is True
        assert notify.should_notify({"green"}, False, True) is False


class TestFormatPayload:
    def test_slack(self):
        p = notify.format_payload("https://hooks.slack.com/services/xxx", "hi")
        assert p == {"text": "hi"}

    def test_discord(self):
        p = notify.format_payload("https://discord.com/api/webhooks/xxx", "hi")
        assert p == {"content": "hi"}

    def test_generic(self):
        p = notify.format_payload("https://example.com/hook", "hi")
        assert p["message"] == "hi"
        assert p["event"] == "carbon-aware-dispatcher"


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
