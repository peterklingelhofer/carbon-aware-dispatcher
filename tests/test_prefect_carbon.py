"""Tests for the carbon-aware Prefect gate."""

from unittest import mock

from integrations import prefect_carbon as pc


class TestCarbonGate:
    def test_delegates_to_wait(self):
        with mock.patch.object(pc, "wait_until_clean", return_value=True) as w:
            assert pc.carbon_gate(zones="GB", max_carbon=150) is True
            assert w.call_args.args[0] == "GB" and w.call_args.args[1] == 150

    def test_returns_false_on_timeout(self):
        with mock.patch.object(pc, "wait_until_clean", return_value=False):
            assert pc.carbon_gate() is False

    def test_tokens_threaded(self):
        with mock.patch.object(pc, "wait_until_clean", return_value=True) as w:
            pc.carbon_gate(tokens={"eia": "k"})
            assert w.call_args.kwargs == {"tokens": {"eia": "k"}}
