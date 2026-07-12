"""Tests for the carbon-aware Ray gate."""

import pytest

from integrations import ray_carbon as rc


class TestRunWhenClean:
    def test_runs_when_clean(self):
        called = []
        result = rc.run_when_clean(lambda x: called.append(x) or x * 2, 21, gate=lambda: True)
        assert result == 42 and called == [21]

    def test_raises_when_never_clean(self):
        with pytest.raises(RuntimeError):
            rc.run_when_clean(lambda: 1, gate=lambda: False)

    def test_does_not_run_when_dirty(self):
        called = []
        with pytest.raises(RuntimeError):
            rc.run_when_clean(lambda: called.append(1), gate=lambda: False)
        assert called == []

    def test_passes_args_and_kwargs(self):
        out = rc.run_when_clean(lambda a, b=0: a + b, 5, b=7, gate=lambda: True)
        assert out == 12
