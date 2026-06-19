"""Tests for the carbon-aware Dagster gate."""

from unittest import mock

from integrations import dagster_carbon as dc


class TestCarbonGate:
    def test_delegates_to_wait(self):
        with mock.patch.object(dc, "wait_until_clean", return_value=True) as w:
            assert dc.carbon_gate(zones="GB", max_carbon=150) is True
            assert w.call_args.args[0] == "GB" and w.call_args.args[1] == 150

    def test_returns_false_on_timeout(self):
        with mock.patch.object(dc, "wait_until_clean", return_value=False):
            assert dc.carbon_gate() is False

    def test_tokens_threaded(self):
        with mock.patch.object(dc, "wait_until_clean", return_value=True) as w:
            dc.carbon_gate(tokens={"eia": "k"})
            assert w.call_args.kwargs == {"tokens": {"eia": "k"}}
