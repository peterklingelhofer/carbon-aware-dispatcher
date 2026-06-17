"""Tests for the standalone carbon-aware CLI."""

import json
from unittest import mock

import pytest

import cli


class TestParseDuration:
    @pytest.mark.parametrize(
        "text,seconds",
        [("6h", 21600), ("15m", 900), ("30s", 30), ("2d", 172800), ("90", 90), ("1.5h", 5400)],
    )
    def test_valid(self, text, seconds):
        assert cli.parse_duration(text) == seconds

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            cli.parse_duration("")


def _zones(_):
    return [{"zone": "GB"}]


class TestCheck:
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_green_exit_0(self, cmz, capsys):
        cmz.return_value = ("GB", 80, None, [])
        rc = cli.main(["check", "--zones", "GB", "--max-carbon", "200", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "green" and out["zone"] == "GB"

    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_dirty_exit_1(self, cmz, capsys):
        def fake(zones, max_carbon, *a, collect=None, **k):
            collect.append(("GB", 300))
            return (None, None, None, [])

        cmz.side_effect = fake
        rc = cli.main(["check", "--zones", "GB", "--max-carbon", "200"])
        assert rc == cli.EXIT_DIRTY
        assert "DIRTY" in capsys.readouterr().out

    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_nodata_exit_2(self, cmz, capsys):
        cmz.return_value = (None, None, None, [("GB", "network error")])
        rc = cli.main(["check", "--zones", "GB"])
        assert rc == cli.EXIT_NODATA


class TestWait:
    @mock.patch("cli.time.sleep")
    @mock.patch("cli.evaluate")
    def test_becomes_green(self, ev, sleep, capsys):
        ev.side_effect = [
            {"status": "dirty", "zone": "GB", "intensity": 300},
            {"status": "green", "zone": "GB", "intensity": 80},
        ]
        rc = cli.main(["wait-for-green", "--max-wait", "1h", "--poll", "1m"])
        assert rc == cli.EXIT_GREEN
        sleep.assert_called_once()

    @mock.patch("cli.time.sleep")
    @mock.patch("cli.evaluate")
    def test_times_out(self, ev, sleep, capsys):
        ev.return_value = {"status": "dirty", "zone": "GB", "intensity": 300}
        rc = cli.main(["wait-for-green", "--max-wait", "30s", "--poll", "60s"])
        assert rc == cli.EXIT_DIRTY
        assert "TIMEOUT" in capsys.readouterr().out


class TestBestWindow:
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.queue_find_optimal_window")
    def test_window_found(self, qf, capsys):
        qf.return_value = ("FR", "2026-06-17T03:00:00Z", 60)
        rc = cli.main(["best-window", "--zones", "FR", "--json"])
        assert rc == cli.EXIT_GREEN
        assert json.loads(capsys.readouterr().out)["zone"] == "FR"

    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.queue_find_optimal_window")
    def test_no_window(self, qf, capsys):
        qf.return_value = (None, None, None)
        rc = cli.main(["best-window", "--zones", "FR"])
        assert rc == cli.EXIT_DIRTY


class TestSuggestCron:
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_history_derived(self, _bp, capsys):
        _bp.return_value = {11: 85.0, 12: 82.0, 19: 145.0}
        rc = cli.main(["suggest-cron", "--zones", "GB", "--energy-kwh", "10", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["cron"] == "0 12 * * *"
        assert out["source"] == "history"
        # savings = (mean - cleanest) * energy; mean ~104, cleanest 82 -> ~220 g
        assert out["savings_g_per_run"] > 0

    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_duration_window(self, bp, capsys):
        profile = {h: 100.0 for h in range(24)}
        profile.update({11: 50.0, 12: 40.0, 13: 45.0})
        bp.return_value = profile
        rc = cli.main(
            [
                "suggest-cron",
                "--zones",
                "GB",
                "--duration-hours",
                "3",
                "--energy-kwh",
                "10",
                "--json",
            ]
        )
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["cron"] == "0 11 * * *"  # cleanest 3h block starts at 11
        assert "3h window" in out["description"]

    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_flat_grid_adds_note(self, _bp, capsys):
        _bp.return_value = {0: 100.0, 1: 102.0, 2: 99.0}  # flat
        rc = cli.main(["suggest-cron", "--zones", "GB", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert "flat" in out.get("note", "")

    @mock.patch("carbon_curve.build_profile", return_value=None)
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.queue_find_optimal_window")
    def test_forecast_derived(self, qf, _bp, capsys):
        qf.return_value = ("GB", "2026-06-17T23:00:00Z", 158)
        rc = cli.main(["suggest-cron", "--zones", "GB", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["cron"] == "0 23 * * *"
        assert out["source"] == "forecast"

    @mock.patch("carbon_curve.build_profile", return_value=None)
    @mock.patch("cli.check_grid.suggest_green_cron")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.queue_find_optimal_window")
    def test_heuristic_fallback(self, qf, sgc, _bp, capsys):
        qf.return_value = (None, None, None)
        sgc.return_value = ("0 2 * * *", "daily at 2am (wind)")
        rc = cli.main(["suggest-cron", "--zones", "GB", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["cron"] == "0 2 * * *"
        assert out["source"] == "heuristic"

    @mock.patch("carbon_curve.build_profile", return_value=None)
    @mock.patch("cli.check_grid.suggest_green_cron")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.queue_find_optimal_window")
    def test_no_suggestion(self, qf, sgc, _bp, capsys):
        qf.return_value = (None, None, None)
        sgc.return_value = (None, None)
        rc = cli.main(["suggest-cron", "--zones", "GB"])
        assert rc == cli.EXIT_NODATA


class TestSuggestRegion:
    def _measure(self, pairs):
        def fake(zones, max_carbon, *a, collect=None, **k):
            collect.extend(pairs)
            return (None, None, None, [])

        return fake

    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "X"}])
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_recommends_cleanest(self, cmz, capsys):
        cmz.side_effect = self._measure([("CISO", 90), ("PJM", 380)])
        rc = cli.main(["suggest-region", "--zones", "CISO,PJM", "--energy-kwh", "10", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["cleanest_zone"] == "CISO"
        assert out["baseline_zone"] == "PJM"
        assert out["savings_g_per_run"] == 2900.0  # (380-90)*10

    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "X"}])
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_current_baseline(self, cmz, capsys):
        cmz.side_effect = self._measure([("CISO", 90), ("PJM", 380), ("GB", 200)])
        rc = cli.main(
            [
                "suggest-region",
                "--zones",
                "CISO,PJM,GB",
                "--current",
                "GB",
                "--energy-kwh",
                "10",
                "--json",
            ]
        )
        out = json.loads(capsys.readouterr().out)
        assert out["baseline_zone"] == "GB"
        assert out["savings_g_per_run"] == 1100.0  # (200-90)*10

    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "X"}])
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_already_cleanest(self, cmz, capsys):
        cmz.side_effect = self._measure([("CISO", 90), ("PJM", 380)])
        rc = cli.main(["suggest-region", "--zones", "CISO,PJM", "--current", "CISO"])
        assert rc == cli.EXIT_DIRTY
        assert "Already" in capsys.readouterr().out

    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "X"}])
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_no_data(self, cmz, capsys):
        cmz.return_value = (None, None, None, [])
        rc = cli.main(["suggest-region", "--zones", "CISO"])
        assert rc == cli.EXIT_NODATA


class TestCurve:
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_curve_json(self, bp, capsys):
        bp.return_value = {12: 82.0, 19: 145.0}
        rc = cli.main(["curve", "--zones", "GB", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["cleanest_hour"] == 12
        assert out["spread_pct"] > 0

    @mock.patch("carbon_curve.build_profile", return_value=None)
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_curve_unavailable(self, _bp, capsys):
        rc = cli.main(["curve", "--zones", "FR"])
        assert rc == cli.EXIT_NODATA


class TestWorthIt:
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_worth(self, bp, capsys):
        bp.return_value = {12: 80.0, 19: 160.0}  # big spread
        rc = cli.main(["worth-it", "--zones", "GB", "--energy-kwh", "10", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "worth"
        assert out["best_case_savings_g_per_run"] == 800.0  # (160-80)*10

    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_not_worth(self, bp, capsys):
        bp.return_value = {0: 100.0, 1: 101.0, 2: 99.0}  # flat
        rc = cli.main(["worth-it", "--zones", "GB", "--json"])
        assert rc == cli.EXIT_DIRTY
        assert json.loads(capsys.readouterr().out)["status"] == "not_worth"

    @mock.patch("carbon_curve.build_profile", return_value=None)
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_unknown(self, _bp, capsys):
        rc = cli.main(["worth-it", "--zones", "FR"])
        assert rc == cli.EXIT_NODATA


class TestReport:
    def _clear(self):
        import os

        for k in (
            "JOB_ENERGY_KWH",
            "JOB_POWER_WATTS",
            "JOB_DURATION_MINUTES",
            "PUE",
            "EMBODIED_GRAMS",
        ):
            os.environ.pop(k, None)

    @mock.patch("cli.evaluate")
    def test_report_json(self, ev, capsys):
        ev.return_value = {"status": "green", "zone": "GB", "intensity": 100}
        try:
            rc = cli.main(
                [
                    "report",
                    "--zones",
                    "GB",
                    "--energy-kwh",
                    "10",
                    "--pue",
                    "1.0",
                    "--embodied-grams",
                    "0",
                    "--json",
                ]
            )
            assert rc == cli.EXIT_GREEN
            out = json.loads(capsys.readouterr().out)
            assert out["zone"] == "GB"
            assert out["energy_kwh"] == 10.0
            assert out["emitted_grams"] == 1000.0  # 100 g/kWh x 10 kWh x 1.0
            assert out["functional_unit"] == "run"
            assert out["schema"] == "sci-report/1"
        finally:
            self._clear()

    @mock.patch("cli.evaluate")
    def test_report_no_data(self, ev, capsys):
        ev.return_value = {"status": "error", "skipped": 1}
        try:
            rc = cli.main(["report", "--zones", "GB"])
            assert rc == cli.EXIT_NODATA
        finally:
            self._clear()


class TestUsage:
    def test_bad_duration_returns_usage(self, capsys):
        rc = cli.main(["wait-for-green", "--max-wait", "notaduration"])
        assert rc == cli.EXIT_USAGE
