"""Tests for the standalone carbon-aware CLI."""

import contextlib
import json
import sys
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

import cli
import ledger
import setup_wizard


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


def _measured(pairs):
    """A check_multiple_zones stand-in that reports the given (zone, intensity) readings."""

    def fake(zones, max_carbon, *a, collect=None, **k):
        if collect is not None:
            collect.extend(pairs)
        return (None, None, None, [])

    return fake


def _curve_file(tmp_path, name, zone="FR", base=100):
    """Write a six-hour curve file for zone and return its path."""
    data = ledger.empty_ledger()
    for hour in range(6):
        data = ledger.merge_curve_sample(data, zone, hour, base + hour)
    p = tmp_path / name
    p.write_text(json.dumps({"curve": data["curve"]}))
    return str(p)


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
        cmz.side_effect = _measured([("GB", 300)])
        rc = cli.main(["check", "--zones", "GB", "--max-carbon", "200"])
        assert rc == cli.EXIT_DIRTY
        assert "DIRTY" in capsys.readouterr().out

    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_nodata_exit_2(self, cmz, capsys):
        cmz.return_value = (None, None, None, [("GB", "network error")])
        rc = cli.main(["check", "--zones", "GB"])
        assert rc == cli.EXIT_NODATA


class TestScale:
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_clean_grid_full_scale(self, cmz, capsys):
        cmz.return_value = ("GB", 80, None, [])  # below green boundary
        rc = cli.main(["scale", "--zones", "GB"])
        assert rc == cli.EXIT_GREEN
        assert capsys.readouterr().out.strip() == "1.0"

    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_dirty_grid_floor_scale(self, cmz, capsys):
        cmz.side_effect = _measured([("GB", 400)])  # above amber boundary
        rc = cli.main(["scale", "--zones", "GB", "--json"])
        assert rc == cli.EXIT_GREEN  # a scaling signal always exits 0
        out = json.loads(capsys.readouterr().out)
        assert out["scale"] == 0.25 and out["intensity"] == 400

    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_max_replicas_rounds_up(self, cmz, capsys):
        cmz.return_value = ("GB", 80, None, [])  # full scale -> all replicas
        rc = cli.main(["scale", "--zones", "GB", "--max-replicas", "10"])
        assert rc == cli.EXIT_GREEN
        assert capsys.readouterr().out.strip() == "10"

    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_no_data_fails_open(self, cmz, capsys):
        cmz.return_value = (None, None, None, [("GB", "network error")])
        rc = cli.main(["scale", "--zones", "GB", "--max-replicas", "8"])
        assert rc == cli.EXIT_NODATA
        # fail open: still recommends full fleet rather than zero
        assert capsys.readouterr().out.strip() == "8"


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

    @pytest.mark.parametrize("flags,marker", [([], "TIMEOUT"), (["--json"], '"timeout"')])
    @mock.patch("cli.time.sleep")
    @mock.patch("cli.evaluate")
    def test_times_out(self, ev, sleep, capsys, flags, marker):
        ev.return_value = {"status": "dirty", "zone": "GB", "intensity": 300}
        rc = cli.main(["wait-for-green", "--max-wait", "30s", "--poll", "60s", *flags])
        assert rc == cli.EXIT_DIRTY
        assert marker in capsys.readouterr().out

    @mock.patch("cli.time.sleep")
    @mock.patch("cli.evaluate")
    def test_green_at_once_skips_waiting(self, ev, sleep, capsys):
        ev.return_value = {"status": "green", "zone": "GB", "intensity": 80}
        assert cli.main(["wait-for-green"]) == cli.EXIT_GREEN
        sleep.assert_not_called()
        assert "GREEN: GB" in capsys.readouterr().out


class TestSplit:
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_water_fills_cleanest_first(self, cmz, capsys):
        cmz.side_effect = _measured([("GB", 200), ("FR", 50)])
        rc = cli.main(
            ["split", "--zones", "GB,FR", "--shards", "6", "--capacity", '{"FR":4}', "--json"]
        )
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        alloc = {a["zone"]: a["shards"] for a in out["allocation"]}
        assert alloc == {"FR": 4, "GB": 2} and out["unplaced"] == 0

    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_reports_saving_vs_even_split(self, cmz, capsys):
        cmz.side_effect = _measured([("FR", 50), ("GB", 250)])
        rc = cli.main(["split", "--zones", "FR,GB", "--shards", "4", "--energy-kwh", "1", "--json"])
        out = json.loads(capsys.readouterr().out)
        assert rc == cli.EXIT_GREEN
        assert out["emitted_grams"] == 200.0  # all 4 to FR
        assert out["even_split_grams"] == 600.0
        assert out["saved_grams"] == 400.0

    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_no_data_exit_2(self, cmz, capsys):
        cmz.return_value = (None, None, None, [("GB", "network error")])
        rc = cli.main(["split", "--zones", "GB", "--shards", "4"])
        assert rc == cli.EXIT_NODATA

    def test_bad_capacity_is_usage_error(self, capsys):
        rc = cli.main(["split", "--zones", "GB", "--shards", "4", "--capacity", "not-json"])
        assert rc == cli.EXIT_USAGE


class TestForecastAccuracy:
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.queue_find_optimal_window")
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_resolves_seeded_prediction(self, cmz, qf, tmp_path, capsys):
        cmz.side_effect = _measured([("GB", 100)])  # actual reading now
        qf.return_value = ("GB", "2026-09-01T03:00Z", 150)  # a fresh forecast to log
        store = tmp_path / "log.json"
        target = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%MZ")
        store.write_text(
            json.dumps(
                {
                    "predictions": [
                        {
                            "zone": "GB",
                            "predicted_at": target,
                            "predicted_intensity": 130,
                            "made_at": "x",
                            "actual": None,
                            "error": None,
                        }
                    ]
                }
            )
        )
        rc = cli.main(["forecast-accuracy", "--zones", "GB", "--store", str(store), "--json"])
        out = json.loads(capsys.readouterr().out)
        assert rc == cli.EXIT_GREEN
        assert out["resolved_now"] == 1
        assert out["n"] == 1 and out["bias"] == 30.0  # predicted 130 vs actual 100

    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_no_reading_exit_2(self, cmz, tmp_path, capsys):
        cmz.return_value = (None, None, None, [("GB", "network error")])
        rc = cli.main(["forecast-accuracy", "--zones", "GB", "--store", str(tmp_path / "l.json")])
        assert rc == cli.EXIT_NODATA

    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.check_grid.queue_find_optimal_window")
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_first_run_records_a_prediction(self, cmz, qf, tmp_path, capsys):
        cmz.side_effect = _measured([("GB", 100)])
        qf.return_value = ("GB", "2026-09-01T03:00Z", 150)
        store = tmp_path / "log.json"
        rc = cli.main(["forecast-accuracy", "--zones", "GB", "--store", str(store)])
        assert rc == cli.EXIT_GREEN
        assert "nothing resolved yet" in capsys.readouterr().out
        assert len(json.loads(store.read_text())["predictions"]) == 1


class TestMarginalEstimate:
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("providers.eia.fuel_mix_series")
    def test_estimates_from_series(self, series, capsys):
        series.return_value = [
            (100.0, 100 * 24),
            (130.0, 100 * 24 + 30 * 490),
            (190.0, 100 * 24 + 90 * 490),
        ]
        rc = cli.main(["marginal-estimate", "--zones", "CISO", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["marginal"] == 490 and out["r_squared"] == 1.0

    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("providers.eia.fuel_mix_series", return_value=[])
    def test_no_series_exit_2(self, _series, capsys):
        rc = cli.main(["marginal-estimate", "--zones", "GB"])
        assert rc == cli.EXIT_NODATA


class TestWaitOptimalStopping:
    @mock.patch("cli.time.sleep")
    @mock.patch("cli.check_grid.queue_find_optimal_window")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.evaluate")
    def test_runs_now_when_waiting_not_worth_it(self, ev, qf, sleep, capsys):
        # Dirty now (300), but the cleanest forecast window is only slightly
        # cleaner and ~20h out, so idling that long emits more than it saves
        ev.return_value = {"status": "dirty", "zone": "GB", "intensity": 300}
        future = (datetime.now(timezone.utc) + timedelta(hours=20)).strftime("%Y-%m-%dT%H:%MZ")
        qf.return_value = ("GB", future, 280)
        rc = cli.main(
            ["wait-for-green", "--zones", "GB", "--max-wait", "24h", "--energy-kwh", "1", "--json"]
        )
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "run_now"
        sleep.assert_not_called()

    @mock.patch("cli.time.sleep")
    @mock.patch("cli.check_grid.queue_find_optimal_window")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    @mock.patch("cli.evaluate")
    def test_waits_when_worth_it(self, ev, qf, sleep, capsys):
        # Big drop (300 -> 50) soon (1h): waiting clearly pays, so it blocks and
        # then catches the green window
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%MZ")
        qf.return_value = ("GB", future, 50)
        ev.side_effect = [
            {"status": "dirty", "zone": "GB", "intensity": 300},
            {"status": "green", "zone": "GB", "intensity": 50},
        ]
        argv = ["wait-for-green", "--zones", "GB", "--max-wait", "6h", "--poll", "1m"]
        rc = cli.main([*argv, "--energy-kwh", "5"])
        assert rc == cli.EXIT_GREEN
        sleep.assert_called_once()


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
        # savings = (mean - cleanest) * energy, mean ~104, cleanest 82 -> ~220 g
        assert out["savings_g_per_run"] > 0

    @mock.patch("carbon_curve.build_weekday_profile")
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_weekly_picks_day(self, bp, wbp, capsys):
        bp.return_value = dict.fromkeys(range(24), 100.0) | {12: 60.0}  # cleanest hour 12
        wbp.return_value = {0: 200.0, 5: 80.0, 6: 90.0}  # cleanest day Sat (py 5)
        rc = cli.main(["suggest-cron", "--zones", "GB", "--weekly", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["cron"] == "0 12 * * 6"  # Sat=cron dow 6, hour 12
        assert "weekly on Sat" in out["description"]

    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_duration_window(self, bp, capsys):
        profile = dict.fromkeys(range(24), 100.0)
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
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "X"}])
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_recommends_cleanest(self, cmz, capsys):
        cmz.side_effect = _measured([("CISO", 90), ("PJM", 380)])
        rc = cli.main(["suggest-region", "--zones", "CISO,PJM", "--energy-kwh", "10", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["cleanest_zone"] == "CISO"
        assert out["baseline_zone"] == "PJM"
        assert out["savings_g_per_run"] == 2900.0  # (380-90)*10

    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "X"}])
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_current_baseline(self, cmz, capsys):
        cmz.side_effect = _measured([("CISO", 90), ("PJM", 380), ("GB", 200)])
        cli.main(
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
        cmz.side_effect = _measured([("CISO", 90), ("PJM", 380)])
        rc = cli.main(["suggest-region", "--zones", "CISO,PJM", "--current", "CISO"])
        assert rc == cli.EXIT_DIRTY
        assert "Already" in capsys.readouterr().out

    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "X"}])
    @mock.patch("cli.check_grid.check_multiple_zones")
    def test_no_data(self, cmz, capsys):
        cmz.return_value = (None, None, None, [])
        rc = cli.main(["suggest-region", "--zones", "CISO"])
        assert rc == cli.EXIT_NODATA


class TestPlan:
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "CISO"}, {"zone": "PJM"}])
    def test_picks_best_zone_and_hour(self, bp, capsys):
        profiles = {
            "CISO": dict.fromkeys(range(24), 300.0) | {3: 80.0},
            "PJM": dict.fromkeys(range(24), 400.0) | {5: 350.0},
        }
        bp.side_effect = lambda z: profiles.get(z)
        rc = cli.main(["plan", "--zones", "CISO,PJM", "--energy-kwh", "10", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["zone"] == "CISO" and out["hour"] == 3
        assert out["savings_g_per_run"] > 0

    @mock.patch("cli.cmd_suggest_region")
    @mock.patch("carbon_curve.build_profile", return_value=None)
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "FR"}])
    def test_falls_back_to_region(self, _bp, fallback, capsys):
        fallback.return_value = cli.EXIT_GREEN
        rc = cli.main(["plan", "--zones", "FR"])
        assert rc == cli.EXIT_GREEN
        fallback.assert_called_once()


class TestAudit:
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "GB"}])
    def test_ranks_shiftable_crons(self, bp, capsys, tmp_path):
        bp.return_value = dict.fromkeys(range(24), 100.0) | {3: 50.0}  # cleanest hour 3
        (tmp_path / "a.yml").write_text("on:\n  schedule:\n    - cron: '0 20 * * *'\n")
        (tmp_path / "b.yml").write_text("    - cron: '*/15 * * * *'\n")  # complex, skipped
        (tmp_path / "c.yml").write_text("    - cron: '0 3 * * *'\n")  # already optimal
        rc = cli.main(
            ["audit", "--zones", "GB", "--dir", str(tmp_path), "--energy-kwh", "10", "--json"]
        )
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["cleanest_hour"] == 3
        assert len(out["findings"]) == 1  # only the 0 20 cron is shiftable
        f = out["findings"][0]
        assert f["suggested_cron"] == "0 3 * * *"
        assert f["savings_g_per_run"] == 500.0  # (100-50)*10
        assert out["total_savings_kg_per_year"] > 0

    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "GB"}])
    def test_all_optimal(self, bp, capsys, tmp_path):
        bp.return_value = dict.fromkeys(range(24), 100.0) | {3: 50.0}
        (tmp_path / "a.yml").write_text("    - cron: '0 3 * * *'\n")
        rc = cli.main(["audit", "--zones", "GB", "--dir", str(tmp_path)])
        assert rc == cli.EXIT_DIRTY

    @mock.patch("carbon_curve.build_profile", return_value=None)
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "FR"}])
    def test_no_curve(self, _bp, capsys, tmp_path):
        rc = cli.main(["audit", "--zones", "FR", "--dir", str(tmp_path)])
        assert rc == cli.EXIT_NODATA


class TestScheduleCost:
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "GB"}])
    def test_ranks_by_annual_emissions(self, bp, capsys, tmp_path):
        bp.return_value = dict.fromkeys(range(24), 100.0)  # mean 100 gCO2/kWh
        (tmp_path / "hourly.yml").write_text("    - cron: '0 * * * *'\n")  # 24x/day
        (tmp_path / "daily.yml").write_text("    - cron: '0 3 * * *'\n")  # 1x/day
        rc = cli.main(
            [
                "schedule-cost",
                "--zones",
                "GB",
                "--dir",
                str(tmp_path),
                "--energy-kwh",
                "10",
                "--json",
            ]
        )
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        # per run = 100 g/kWh x 10 kWh = 1000 g = 1 kg
        assert out["per_run_grams"] == 1000.0
        # hourly job ranked first, ~24*365 kg/yr
        assert out["schedules"][0]["runs_per_day"] == 24
        assert out["schedules"][0]["annual_kg"] > out["schedules"][1]["annual_kg"]

    @mock.patch("carbon_curve.build_profile", return_value=None)
    @mock.patch("cli.check_grid.check_multiple_zones")
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "GB"}])
    def test_no_data(self, cmz, _bp, capsys, tmp_path):
        cmz.return_value = (None, None, None, [])
        rc = cli.main(["schedule-cost", "--zones", "GB", "--dir", str(tmp_path)])
        assert rc == cli.EXIT_NODATA


class TestAdvise:
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "GB"}])
    def test_shift_action(self, bp, capsys, tmp_path):
        bp.return_value = dict.fromkeys(range(24), 100.0) | {3: 10.0}  # spread big, cleanest 3
        (tmp_path / "a.yml").write_text("    - cron: '0 20 * * *'\n")
        rc = cli.main(
            ["advise", "--zones", "GB", "--dir", str(tmp_path), "--energy-kwh", "10", "--json"]
        )
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["total_avoidable_kg_per_year"] > 0
        assert any(a["type"] == "shift" for a in out["actions"])

    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "GB"}])
    def test_throttle_action_for_hourly(self, bp, capsys, tmp_path):
        bp.return_value = dict.fromkeys(range(24), 100.0) | {3: 10.0}
        (tmp_path / "a.yml").write_text("    - cron: '0 * * * *'\n")  # hourly, unshiftable
        cli.main(
            ["advise", "--zones", "GB", "--dir", str(tmp_path), "--energy-kwh", "10", "--json"]
        )
        out = json.loads(capsys.readouterr().out)
        assert any(a["type"] == "throttle" for a in out["actions"])

    @mock.patch("carbon_curve.build_profile", return_value=None)
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "FR"}])
    def test_no_curve(self, _bp, capsys, tmp_path):
        rc = cli.main(["advise", "--zones", "FR", "--dir", str(tmp_path)])
        assert rc == cli.EXIT_NODATA


class TestMarginal:
    @mock.patch("providers.watttime.get_marginal_index")
    @mock.patch("providers.watttime.login")
    def test_clean(self, login, idx, capsys, monkeypatch):
        monkeypatch.setenv("WATTTIME_USERNAME", "u")
        monkeypatch.setenv("WATTTIME_PASSWORD", "p")
        login.return_value = "tok"
        idx.return_value = 20
        rc = cli.main(["marginal", "--max-percentile", "33", "--json"])
        assert rc == cli.EXIT_GREEN
        assert json.loads(capsys.readouterr().out)["status"] == "clean"

    @mock.patch("providers.watttime.get_marginal_index")
    @mock.patch("providers.watttime.login")
    def test_dirty(self, login, idx, capsys, monkeypatch):
        monkeypatch.setenv("WATTTIME_USERNAME", "u")
        monkeypatch.setenv("WATTTIME_PASSWORD", "p")
        login.return_value = "tok"
        idx.return_value = 90
        rc = cli.main(["marginal", "--max-percentile", "33"])
        assert rc == cli.EXIT_DIRTY

    def test_no_credentials(self, capsys, monkeypatch):
        monkeypatch.delenv("WATTTIME_USERNAME", raising=False)
        monkeypatch.delenv("WATTTIME_PASSWORD", raising=False)
        assert cli.main(["marginal"]) == cli.EXIT_NODATA

    @mock.patch("providers.watttime.get_marginal_index", return_value=None)
    @mock.patch("providers.watttime.login", return_value="tok")
    def test_no_data(self, login, idx, monkeypatch):
        monkeypatch.setenv("WATTTIME_USERNAME", "u")
        monkeypatch.setenv("WATTTIME_PASSWORD", "p")
        assert cli.main(["marginal"]) == cli.EXIT_NODATA


class TestSla:
    def _seed(self, tmp_path, green, dirty):
        data = ledger.empty_ledger()
        for _ in range(green):
            data = ledger.merge_entry(data, 0, "2026-06-17", is_green=True)
        for _ in range(dirty):
            data = ledger.merge_entry(data, 0, "2026-06-17", is_green=False)
        p = tmp_path / "led.json"
        p.write_text(json.dumps(data))
        return f"file:{p}"

    def test_compliant(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LEDGER", self._seed(tmp_path, green=10, dirty=0))
        rc = cli.main(["sla", "--target", "95", "--window", "lifetime", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "compliant" and out["compliance_pct"] == 100.0

    def test_compliant_text(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LEDGER", self._seed(tmp_path, green=10, dirty=0))
        assert cli.main(["sla", "--target", "95", "--window", "lifetime"]) == cli.EXIT_GREEN
        assert "Green SLA: compliant, 100% of 10 runs clean" in capsys.readouterr().out

    def test_breached(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LEDGER", self._seed(tmp_path, green=5, dirty=5))
        rc = cli.main(["sla", "--target", "95", "--window", "lifetime", "--json"])
        assert rc == cli.EXIT_DIRTY
        assert json.loads(capsys.readouterr().out)["status"] == "breached"

    def test_unknown_few_runs(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LEDGER", self._seed(tmp_path, green=2, dirty=0))
        rc = cli.main(["sla", "--window", "lifetime"])
        assert rc == cli.EXIT_NODATA

    def test_no_ledger(self, capsys, monkeypatch):
        monkeypatch.delenv("LEDGER", raising=False)
        assert cli.main(["sla"]) == cli.EXIT_NODATA


class TestScore:
    def test_grade_thresholds(self):
        assert cli._grade(1.0)[0] == "A"
        assert cli._grade(0.85)[0] == "B"
        assert cli._grade(0.7)[0] == "C"
        assert cli._grade(0.5)[0] == "D"
        assert cli._grade(0.1)[0] == "F"

    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "GB"}])
    def test_low_grade_when_savings_unclaimed(self, bp, capsys, tmp_path):
        bp.return_value = dict.fromkeys(range(24), 100.0) | {3: 10.0}  # hour 3 well below the rest
        (tmp_path / "a.yml").write_text("    - cron: '0 20 * * *'\n")  # daily at dirty hour
        rc = cli.main(
            ["score", "--zones", "GB", "--dir", str(tmp_path), "--energy-kwh", "10", "--json"]
        )
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["avoidable_kg_per_year"] > 0
        assert out["grade"] in ("D", "F")  # lots left on the table

    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "GB"}])
    def test_writes_badge_file(self, bp, tmp_path):
        bp.return_value = dict.fromkeys(range(24), 100.0) | {3: 10.0}
        (tmp_path / "a.yml").write_text("    - cron: '0 3 * * *'\n")  # already optimal
        badge = tmp_path / "badge.json"
        rc = cli.main(
            ["score", "--zones", "GB", "--dir", str(tmp_path), "--badge-file", str(badge)]
        )
        assert rc == cli.EXIT_GREEN
        data = json.loads(badge.read_text())
        assert data["label"] == "carbon posture" and "A" in data["message"]

    @mock.patch("carbon_curve.build_profile", return_value=None)
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "FR"}])
    def test_no_curve(self, _bp, capsys, tmp_path):
        rc = cli.main(["score", "--zones", "FR", "--dir", str(tmp_path)])
        assert rc == cli.EXIT_NODATA


class TestExportCurves:
    def _seed(self, tmp_path):
        return f"file:{_curve_file(tmp_path, 'led.json')}"

    def test_exports_to_stdout(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LEDGER", self._seed(tmp_path))
        rc = cli.main(["export-curves"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert "FR" in out["curve"]

    def test_writes_file(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("LEDGER", self._seed(tmp_path))
        dest = tmp_path / "shared.json"
        rc = cli.main(["export-curves", "--output", str(dest)])
        assert rc == cli.EXIT_GREEN
        assert "FR" in json.loads(dest.read_text())["curve"]

    def test_no_ledger(self, monkeypatch):
        monkeypatch.delenv("LEDGER", raising=False)
        assert cli.main(["export-curves"]) == cli.EXIT_NODATA


class TestMergeCurves:
    def test_merges_to_stdout(self, capsys, tmp_path):
        a = _curve_file(tmp_path, "a.json", "FR", 100)
        b = _curve_file(tmp_path, "b.json", "DE", 300)
        rc = cli.main(["merge-curves", a, b])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert set(out["curve"]) == {"FR", "DE"}

    def test_writes_file(self, tmp_path):
        a = _curve_file(tmp_path, "a.json", "FR", 100)
        dest = tmp_path / "pool.json"
        rc = cli.main(["merge-curves", a, "--output", str(dest)])
        assert rc == cli.EXIT_GREEN
        assert "FR" in json.loads(dest.read_text())["curve"]

    def test_cap_n_clamps_weight(self, capsys, tmp_path):
        heavy = tmp_path / "heavy.json"
        heavy.write_text(json.dumps({"curve": {"FR": {"3": {"sum": 10000.0, "n": 100}}}}))
        rc = cli.main(["merge-curves", str(heavy), "--cap-n", "10"])
        assert rc == cli.EXIT_GREEN
        cell = json.loads(capsys.readouterr().out)["curve"]["FR"]["3"]
        assert cell["n"] == 10 and cell["sum"] == 1000.0

    def test_errors_when_no_readable_files(self, tmp_path):
        rc = cli.main(["merge-curves", str(tmp_path / "missing.json")])
        assert rc == cli.EXIT_NODATA


class TestSampleCurves:
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "DE"}, {"zone": "ES"}])
    def test_samples_into_new_file(self, tmp_path):
        dest = tmp_path / "seed.json"
        with mock.patch(
            "cli.check_grid.check_multiple_zones", _measured([("DE", 400), ("ES", 200)])
        ):
            rc = cli.main(["sample-curves", "--zones", "DE,ES", "--output", str(dest)])
        assert rc == cli.EXIT_GREEN
        doc = json.loads(dest.read_text())
        assert "DE" in doc["curve"] and "ES" in doc["curve"]
        assert "weekday_curve" in doc

    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "DE"}])
    def test_accumulates_into_existing_file(self, tmp_path):
        dest = tmp_path / "seed.json"
        with mock.patch("cli.check_grid.check_multiple_zones", _measured([("DE", 400)])):
            cli.main(["sample-curves", "--zones", "DE", "--output", str(dest)])
            cli.main(["sample-curves", "--zones", "DE", "--output", str(dest)])
        # two samples folded into the same hour cell
        de = json.loads(dest.read_text())["curve"]["DE"]
        total_n = sum(cell["n"] for cell in de.values())
        assert total_n == 2

    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "DE"}])
    def test_no_data(self, tmp_path):
        with mock.patch("cli.check_grid.check_multiple_zones", _measured([])):
            assert cli.main(["sample-curves", "--zones", "DE"]) == cli.EXIT_NODATA


class TestValidateCurves:
    def test_valid_file_passes(self, tmp_path):
        good = _curve_file(tmp_path, "good.json")
        assert cli.main(["validate-curves", good]) == cli.EXIT_GREEN

    def test_invalid_file_fails(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"curve": {"FR": {"3": {"sum": 99999.0, "n": 1}}}}))
        assert cli.main(["validate-curves", str(p)]) == cli.EXIT_DIRTY

    def test_unreadable_file_fails(self, tmp_path):
        assert cli.main(["validate-curves", str(tmp_path / "missing.json")]) == cli.EXIT_DIRTY

    def test_mixed_batch_fails_if_any_bad(self, tmp_path):
        good = _curve_file(tmp_path, "good.json")
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert cli.main(["validate-curves", good, str(bad)]) == cli.EXIT_DIRTY


class TestCurve:
    @mock.patch("carbon_curve.build_profile_samples", return_value=None)
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_curve_json(self, bp, _samples, capsys):
        bp.return_value = {12: 82.0, 19: 145.0}
        rc = cli.main(["curve", "--zones", "GB", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["cleanest_hour"] == 12
        assert out["spread_pct"] > 0
        assert "confidence_band" not in out  # no raw samples -> no band

    @mock.patch("carbon_curve.build_profile_samples")
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_curve_adds_band_and_median(self, bp, samples, capsys):
        bp.return_value = {12: 80.0, 19: 160.0}
        # Hour 12 has a spike (1000) the mean would chase but the median ignores
        samples.return_value = [(12, 70), (12, 70), (12, 1000), (19, 160), (19, 160)]
        rc = cli.main(["curve", "--zones", "GB", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["median_profile"]["12"] == 70.0  # resists the spike
        assert "confidence_band" in out

    @mock.patch("carbon_curve.build_profile", return_value=None)
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_curve_unavailable(self, _bp, capsys):
        rc = cli.main(["curve", "--zones", "FR"])
        assert rc == cli.EXIT_NODATA


class TestWorthIt:
    @mock.patch("carbon_curve.build_profile_samples", return_value=None)
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_worth(self, bp, _samples, capsys):
        bp.return_value = {12: 80.0, 19: 160.0}  # big spread
        rc = cli.main(["worth-it", "--zones", "GB", "--energy-kwh", "10", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "worth"
        assert out["best_case_savings_g_per_run"] == 800.0  # (160-80)*10

    @mock.patch("carbon_curve.build_profile_samples", return_value=None)
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_not_worth(self, bp, _samples, capsys):
        bp.return_value = {0: 100.0, 1: 101.0, 2: 99.0}  # flat
        rc = cli.main(["worth-it", "--zones", "GB", "--json"])
        assert rc == cli.EXIT_DIRTY
        assert json.loads(capsys.readouterr().out)["status"] == "not_worth"

    @mock.patch("carbon_curve.build_profile_samples")
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_significant_spread_is_worth(self, bp, samples, capsys):
        bp.return_value = {2: 60.0, 14: 200.0}  # big spread
        # Tight within-hour clusters far apart -> clearly significant
        samples.return_value = [(2, 58), (2, 62), (2, 60), (14, 198), (14, 202), (14, 200)]
        rc = cli.main(["worth-it", "--zones", "GB", "--json"])
        out = json.loads(capsys.readouterr().out)
        assert rc == cli.EXIT_GREEN
        assert out["status"] == "worth" and out["significant"] is True

    @mock.patch("carbon_curve.build_profile_samples")
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_spread_from_noise_is_not_worth(self, bp, samples, capsys):
        # The means look spread out, but each hour's samples are so scattered the
        # gap is just noise -> the ANOVA test vetoes the spread heuristic
        bp.return_value = {2: 60.0, 14: 200.0}
        samples.return_value = [
            (2, -200),
            (2, 320),
            (2, 60),
            (14, 0),
            (14, 400),
            (14, 200),
        ]
        rc = cli.main(["worth-it", "--zones", "GB", "--json"])
        out = json.loads(capsys.readouterr().out)
        assert rc == cli.EXIT_DIRTY
        assert out["status"] == "not_worth" and out["significant"] is False

    @mock.patch("carbon_curve.build_profile", return_value=None)
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_unknown(self, _bp, capsys):
        rc = cli.main(["worth-it", "--zones", "FR"])
        assert rc == cli.EXIT_NODATA


class TestReport:
    @pytest.fixture(autouse=True)
    def _sci_env(self, monkeypatch):
        # The report pushes its flags into these env vars, so undo that after each test
        for k in (
            "JOB_ENERGY_KWH",
            "JOB_POWER_WATTS",
            "JOB_DURATION_MINUTES",
            "PUE",
            "EMBODIED_GRAMS",
        ):
            monkeypatch.delenv(k, raising=False)

    @mock.patch("cli.evaluate")
    def test_report_json(self, ev, capsys):
        ev.return_value = {"status": "green", "zone": "GB", "intensity": 100}
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

    @mock.patch("cli.evaluate")
    def test_report_no_data(self, ev, capsys):
        ev.return_value = {"status": "error", "skipped": 1}
        rc = cli.main(["report", "--zones", "GB"])
        assert rc == cli.EXIT_NODATA


class TestUsage:
    def test_bad_duration_returns_usage(self, capsys):
        rc = cli.main(["wait-for-green", "--max-wait", "notaduration"])
        assert rc == cli.EXIT_USAGE


_CURVE = dict.fromkeys(range(24), 100.0) | {3: 50.0}
_NO_SAMPLES = {"carbon_curve.build_profile_samples": mock.Mock(return_value=None)}


def _fake(value):
    return mock.Mock(return_value=value)


@pytest.mark.parametrize(
    "argv,patches,expected",
    [
        (
            ["scale", "--zones", "GB", "--max-replicas", "10", "--json"],
            {"cli.check_grid.check_multiple_zones": _fake(("GB", 80, None, []))},
            '"replicas": 10',
        ),
        (
            [
                *["split", "--zones", "GB,FR", "--shards", "6", "--energy-kwh", "1"],
                "--capacity",
                '{"FR":4,"GB":1}',
            ],
            {"cli.check_grid.check_multiple_zones": _measured([("GB", 200), ("FR", 50)])},
            "unplaced: 1 shards",
        ),
        (
            ["split", "--zones", "GB", "--shards", "4", "--json"],
            {"cli.check_grid.check_multiple_zones": _measured([])},
            '"status": "error"',
        ),
        (
            ["marginal-estimate", "--zones", "CISO"],
            {
                "providers.eia.fuel_mix_series": _fake(
                    [(100.0, 2400), (130.0, 2400 + 30 * 490), (190.0, 2400 + 90 * 490)]
                )
            },
            "Estimated marginal for CISO",
        ),
        (
            ["marginal-estimate", "--zones", "GB", "--json"],
            {"providers.eia.fuel_mix_series": _fake([])},
            '"status": "unavailable"',
        ),
        (
            ["suggest-region", "--zones", "CISO,PJM", "--energy-kwh", "10"],
            {"cli.check_grid.check_multiple_zones": _measured([("CISO", 90), ("PJM", 380)])},
            "Run in CISO",
        ),
        (
            ["suggest-cron", "--zones", "GB", "--energy-kwh", "10"],
            {"carbon_curve.build_profile": _fake({11: 85.0, 12: 82.0, 19: 145.0})},
            "Suggested schedule: 0 12 * * *",
        ),
        (
            ["suggest-cron", "--zones", "GB"],
            {
                "carbon_curve.build_profile": _fake(None),
                "cli.check_grid.queue_find_optimal_window": _fake((None, None, None)),
                "cli.check_grid.suggest_green_cron": _fake(("0 2 * * *", "daily at 2am (wind)")),
            },
            "[heuristic]",
        ),
        (
            ["plan", "--zones", "CISO,PJM", "--energy-kwh", "10"],
            {"carbon_curve.build_profile": _fake(_CURVE)},
            "Run your job in CISO at 03:00 UTC",
        ),
        (
            ["curve", "--zones", "GB"],
            {"carbon_curve.build_profile": _fake({12: 82.0, 19: 145.0}), **_NO_SAMPLES},
            "Hour-of-day carbon curve for GB",
        ),
        (
            ["worth-it", "--zones", "GB", "--energy-kwh", "10"],
            {"carbon_curve.build_profile": _fake({12: 80.0, 19: 160.0}), **_NO_SAMPLES},
            "Worth shifting: GB",
        ),
        (
            ["best-window", "--zones", "FR"],
            {"cli.check_grid.queue_find_optimal_window": _fake(("FR", "2026-06-17T03:00:00Z", 60))},
            "Cleanest window: FR at",
        ),
    ],
)
def test_output_names_the_result(argv, patches, expected, capsys):
    """Each command's text (or JSON) rendering carries the result it computed."""
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            mock.patch(
                "cli.check_grid.parse_zones_input", lambda s: [{"zone": z} for z in s.split(",")]
            )
        )
        for target, value in patches.items():
            stack.enter_context(mock.patch(target, value))
        cli.main(argv)
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize(
    "command,expected",
    [
        ("audit", "Carbon audit of"),
        ("schedule-cost", "Scheduled-workflow emissions for"),
        ("advise", "Carbon plan for GB"),
    ],
)
@mock.patch("carbon_curve.build_profile", return_value=_CURVE)
@mock.patch("cli.check_grid.parse_zones_input", _zones)
def test_repo_scan_text_output(_bp, command, expected, capsys, tmp_path):
    (tmp_path / "a.yml").write_text("    - cron: '0 20 * * *'\n")
    cli.main([command, "--zones", "GB", "--dir", str(tmp_path), "--energy-kwh", "10"])
    assert expected in capsys.readouterr().out


class TestSetupWizardMain:
    """The carbon-dispatch-setup entry point: zone selection, summary and exit code."""

    @staticmethod
    def _run(monkeypatch, argv, status):
        def fake_zone(zone, **_kw):
            state = status(zone)
            return {
                "zone": zone,
                "provider": "P",
                "status": state,
                "intensity": 100,
                "error": state,
            }

        monkeypatch.setattr(sys, "argv", ["setup_wizard", *argv])
        monkeypatch.setattr(setup_wizard, "test_zone", fake_zone)
        with pytest.raises(SystemExit) as exc:
            setup_wizard.main()
        return exc.value.code

    @pytest.mark.parametrize("argv", [[], ["--zone", "GB"], ["--auto-green"], ["--auto-cleanest"]])
    def test_all_ok_exits_0(self, argv, capsys, monkeypatch):
        keys = ["--eia-api-key", "k", "--entsoe-token", "t", "--electricity-maps-token", "e"]
        assert self._run(monkeypatch, [*argv, *keys], lambda _zone: "ok") == 0
        assert "All zones working" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "routing,marker",
        [("gate", "grid_clean"), ("runner", "runner_provider"), ("deploy", "AWS_DEFAULT_REGION")],
    )
    def test_error_exits_1_with_routing_snippet(self, routing, marker, capsys, monkeypatch):
        status = {"GB": "ok", "DE": "skipped", "XX": "error"}.__getitem__
        rc = self._run(monkeypatch, ["--zones", "GB,DE,XX", "--routing", routing], status)
        out = capsys.readouterr().out
        assert rc == 1
        assert "1 ok, 1 skipped, 1 errors" in out and marker in out
