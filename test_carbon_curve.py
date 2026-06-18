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


class TestSavings:
    def test_mean_intensity(self):
        assert carbon_curve.mean_intensity({0: 100, 1: 200}) == 150.0
        assert carbon_curve.mean_intensity({}) == 0.0

    def test_shift_savings(self):
        # (200 - 80) * 10 kWh = 1200 g
        assert carbon_curve.shift_savings_grams({12: 80, 19: 200}, 19, 12, 10) == 1200.0

    def test_shift_savings_clamped_and_missing(self):
        assert carbon_curve.shift_savings_grams({12: 80, 19: 200}, 12, 19, 10) == 0.0  # uphill
        assert carbon_curve.shift_savings_grams({12: 80}, 5, 12, 10) == 0.0  # missing hour

    def test_best_case_savings(self):
        # (200 - 80) * 5 kWh = 600 g
        assert carbon_curve.best_case_savings_grams({12: 80, 19: 200}, 5) == 600.0


class TestCleanestWindow:
    def test_picks_lowest_block(self):
        profile = {h: 100 for h in range(24)}
        profile.update({11: 50, 12: 40, 13: 45})  # cleanest block around noon
        start, avg = carbon_curve.cleanest_window(profile, 3)
        assert start == 11 and avg < 50

    def test_wraps_midnight(self):
        profile = {h: 100 for h in range(24)}
        profile.update({23: 10, 0: 10, 1: 10})  # cleanest block spans midnight
        start, _ = carbon_curve.cleanest_window(profile, 3)
        assert start == 23

    def test_skips_incomplete_windows(self):
        # only 4 hours known; a 3h window only fits fully starting at 10 or 11
        profile = {10: 50, 11: 40, 12: 45, 13: 60}
        start, _ = carbon_curve.cleanest_window(profile, 3)
        assert start in (10, 11)

    def test_none_when_no_full_window(self):
        assert carbon_curve.cleanest_window({1: 50, 5: 40}, 3) == (None, None)

    def test_invalid_hours(self):
        assert carbon_curve.cleanest_window({1: 50}, 0) == (None, None)


class TestIsWorthShifting:
    def test_high_spread_worth(self):
        assert carbon_curve.is_worth_shifting({0: 50, 1: 150}) is True

    def test_flat_not_worth(self):
        assert carbon_curve.is_worth_shifting({0: 100, 1: 102, 2: 99}) is False

    def test_custom_threshold(self):
        # ~2% spread; worth only if threshold drops below it
        assert carbon_curve.is_worth_shifting({0: 100, 1: 102}, min_spread_pct=1.0) is True


class TestWeekday:
    def test_profile_needs_min_days(self):
        samples = [(0, 100), (0, 120), (1, 90)]  # only 2 distinct days
        assert carbon_curve.weekday_profile_from_samples(samples) == {}
        samples += [(2, 80), (3, 110)]  # now 4 days
        prof = carbon_curve.weekday_profile_from_samples(samples)
        assert prof[0] == 110.0 and len(prof) == 4

    def test_cleanest_weekday(self):
        assert carbon_curve.cleanest_weekday({0: 200, 5: 80, 6: 90}) == (5, 80)
        assert carbon_curve.cleanest_weekday({}) == (None, None)

    def test_build_weekday_non_gb_empty(self):
        assert carbon_curve.build_weekday_profile("FR") == {}

    @mock.patch("carbon_curve.base.request")
    def test_build_weekday_gb(self, req):
        # two periods on Mon (weekday 0), enough distinct days won't be met -> {} unless 3+
        req.return_value = {
            "data": [
                {"from": "2026-06-15T00:00Z", "intensity": {"actual": 100}},  # Mon
                {"from": "2026-06-16T00:00Z", "intensity": {"actual": 120}},  # Tue
                {"from": "2026-06-17T00:00Z", "intensity": {"actual": 90}},  # Wed
            ]
        }
        prof = carbon_curve.build_weekday_profile("GB")
        assert len(prof) == 3


class TestBuildProfile:
    @mock.patch("carbon_curve.base.request")
    def test_gb_builds_profile(self, req):
        req.return_value = {"data": [{"from": "2026-06-10T12:00Z", "intensity": {"actual": 82}}]}
        assert carbon_curve.build_profile("GB") == {12: 82.0}

    def test_non_gb_uses_ledger_when_available(self, monkeypatch):
        monkeypatch.setattr(carbon_curve, "ledger_profile", lambda z: {h: 100.0 for h in range(8)})
        assert carbon_curve.build_profile("FR") == {h: 100.0 for h in range(8)}

    def test_non_gb_none_without_ledger(self, monkeypatch):
        monkeypatch.setattr(carbon_curve, "ledger_profile", lambda z: {})
        assert carbon_curve.build_profile("FR") is None


class TestCommunityProfile:
    def test_none_without_env(self, monkeypatch):
        monkeypatch.delenv("COMMUNITY_CURVE", raising=False)
        assert carbon_curve.community_profile("FR") == {}

    def test_reads_pooled_file(self, monkeypatch, tmp_path):
        import json

        import ledger

        data = ledger.empty_ledger()
        for hour in range(6):
            data = ledger.merge_curve_sample(data, "FR", hour, 100 + hour)
        path = tmp_path / "community.json"
        path.write_text(json.dumps({"curve": data["curve"]}))
        monkeypatch.setenv("COMMUNITY_CURVE", str(path))
        prof = carbon_curve.community_profile("FR")
        assert len(prof) == 6 and prof[3] == 103.0

    def test_build_profile_uses_community_fallback(self, monkeypatch, tmp_path):
        import json

        import ledger

        monkeypatch.delenv("LEDGER", raising=False)  # no local ledger
        data = ledger.empty_ledger()
        for hour in range(6):
            data = ledger.merge_curve_sample(data, "JP", hour, 400 + hour)
        path = tmp_path / "community.json"
        path.write_text(json.dumps({"curve": data["curve"]}))
        monkeypatch.setenv("COMMUNITY_CURVE", str(path))
        # JP has no UK history and no local ledger -> community fallback
        assert carbon_curve.build_profile("JP") == carbon_curve.community_profile("JP")
        assert len(carbon_curve.build_profile("JP")) == 6


class TestLedgerProfile:
    def test_no_ledger_config(self, monkeypatch):
        monkeypatch.delenv("LEDGER", raising=False)
        assert carbon_curve.ledger_profile("FR") == {}

    def test_reads_file_ledger(self, monkeypatch, tmp_path):
        import json

        import ledger

        data = ledger.empty_ledger()
        for hour in range(6):
            data = ledger.merge_curve_sample(data, "FR", hour, 100 + hour)
        path = tmp_path / "ledger.json"
        path.write_text(json.dumps(data))
        monkeypatch.setenv("LEDGER", f"file:{path}")
        prof = carbon_curve.ledger_profile("FR")
        assert len(prof) == 6 and prof[3] == 103.0
