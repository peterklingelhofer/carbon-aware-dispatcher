"""Tests for the carbon-aware ML training gate."""

from unittest import mock

from integrations import lightning_carbon as lc


class TestGridIsClean:
    @mock.patch("integrations.gate.check_grid.parse_zones_input", lambda s: [{"zone": "GB"}])
    @mock.patch("integrations.gate.check_grid.check_multiple_zones")
    def test_clean(self, cmz):
        cmz.return_value = ("GB", 80, None, [])
        assert lc.grid_is_clean("GB", 200) is True

    @mock.patch("integrations.gate.check_grid.parse_zones_input", lambda s: [{"zone": "GB"}])
    @mock.patch("integrations.gate.check_grid.check_multiple_zones")
    def test_dirty(self, cmz):
        cmz.return_value = (None, None, None, [])
        assert lc.grid_is_clean("GB", 200) is False


class TestWaitUntilClean:
    def test_returns_when_clean(self):
        states = iter([False, True])  # dirty, then clean
        naps = []
        ok = lc.wait_until_clean(
            max_wait_s=10_000, poll_s=100, sleep=naps.append, is_clean=lambda: next(states)
        )
        assert ok is True
        assert naps == [100]  # slept once before the clean check

    def test_times_out(self):
        naps = []
        ok = lc.wait_until_clean(
            max_wait_s=250, poll_s=100, sleep=naps.append, is_clean=lambda: False
        )
        assert ok is False
        assert sum(naps) >= 250  # waited up to the deadline


class TestCallback:
    def test_gate_calls_wait(self):
        cb = lc.CarbonAwareCallback(zones="GB", max_carbon=150)
        with mock.patch.object(lc, "wait_until_clean", return_value=True) as w:
            cb.on_train_epoch_start()
            w.assert_called_once()
            # config threaded through
            assert w.call_args.args[0] == "GB" and w.call_args.args[1] == 150
