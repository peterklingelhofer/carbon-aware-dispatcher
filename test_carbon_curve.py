"""Tests for the hour-of-day carbon curve."""

from unittest import mock

import carbon_curve


class TestProfileFromSamples:
    def test_averages_by_hour(self):
        samples = [(12, 80), (12, 100), (3, 200)]
        assert carbon_curve.profile_from_samples(samples) == {12: 90.0, 3: 200.0}

    def test_ignores_none(self):
        assert carbon_curve.profile_from_samples([(1, None), (1, 50)]) == {1: 50.0}

    def test_empty(self):
        assert carbon_curve.profile_from_samples([]) == {}


class TestCleanestHour:
    def test_picks_minimum(self):
        assert carbon_curve.cleanest_hour({12: 80, 3: 200, 18: 150}) == (12, 80)

    def test_empty(self):
        assert carbon_curve.cleanest_hour({}) == (None, None)


class TestSpreadPct:
    def test_known(self):
        # values 50..150, mean 100, spread (150-50)/100 = 100%
        assert carbon_curve.spread_pct({0: 50, 1: 100, 2: 150}) == 100.0

    def test_flat_grid_low_spread(self):
        assert carbon_curve.spread_pct({0: 100, 1: 102, 2: 98}) < 5

    def test_empty_zero(self):
        assert carbon_curve.spread_pct({}) == 0.0


class TestUkHistory:
    @mock.patch("carbon_curve.base.request")
    def test_parses_periods(self, req):
        req.return_value = {
            "data": [
                {"from": "2026-06-10T12:00Z", "intensity": {"actual": 82}},
                {"from": "2026-06-10T12:30Z", "intensity": {"actual": 88}},
                {"from": "2026-06-10T19:00Z", "intensity": {"actual": 145}},
                {"from": "2026-06-10T20:00Z", "intensity": {"actual": None}},  # skipped
            ]
        }
        samples = carbon_curve.uk_history_samples(7)
        assert (12, 82) in samples and (12, 88) in samples and (19, 145) in samples
        assert all(s[1] is not None for s in samples)

    @mock.patch("carbon_curve.base.request")
    def test_empty_response(self, req):
        req.return_value = None
        assert carbon_curve.uk_history_samples() == []


class TestIsWorthShifting:
    def test_high_spread_worth(self):
        assert carbon_curve.is_worth_shifting({0: 50, 1: 150}) is True

    def test_flat_not_worth(self):
        assert carbon_curve.is_worth_shifting({0: 100, 1: 102, 2: 99}) is False

    def test_custom_threshold(self):
        # ~2% spread; worth only if threshold drops below it
        assert carbon_curve.is_worth_shifting({0: 100, 1: 102}, min_spread_pct=1.0) is True


class TestBuildProfile:
    @mock.patch("carbon_curve.base.request")
    def test_gb_builds_profile(self, req):
        req.return_value = {"data": [{"from": "2026-06-10T12:00Z", "intensity": {"actual": 82}}]}
        assert carbon_curve.build_profile("GB") == {12: 82.0}

    def test_non_gb_returns_none(self):
        assert carbon_curve.build_profile("FR") is None
