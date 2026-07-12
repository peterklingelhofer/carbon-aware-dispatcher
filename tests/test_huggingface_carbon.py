"""Tests for the carbon-aware Hugging Face Trainer callback."""

from unittest import mock

from integrations import huggingface_carbon as hf


class TestCallback:
    def test_gate_calls_wait(self):
        cb = hf.CarbonAwareTrainerCallback(zones="GB", max_carbon=150)
        with mock.patch.object(hf, "wait_until_clean", return_value=True) as w:
            cb.on_epoch_begin()
            w.assert_called_once()
            assert w.call_args.args[0] == "GB" and w.call_args.args[1] == 150

    def test_returns_control(self):
        cb = hf.CarbonAwareTrainerCallback()
        sentinel = object()
        with mock.patch.object(hf, "wait_until_clean", return_value=True):
            assert cb.on_epoch_begin(control=sentinel) is sentinel

    def test_tokens_threaded(self):
        cb = hf.CarbonAwareTrainerCallback(tokens={"eia": "k"})
        with mock.patch.object(hf, "wait_until_clean", return_value=True) as w:
            cb.gate()
            assert w.call_args.kwargs == {"tokens": {"eia": "k"}}
