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

    @mock.patch("carbon_curve.build_weekday_profile")
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", _zones)
    def test_weekly_picks_day(self, bp, wbp, capsys):
        bp.return_value = {h: 100.0 for h in range(24)} | {12: 60.0}  # cleanest hour 12
        wbp.return_value = {0: 200.0, 5: 80.0, 6: 90.0}  # cleanest day Sat (py 5)
        rc = cli.main(["suggest-cron", "--zones", "GB", "--weekly", "--json"])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert out["cron"] == "0 12 * * 6"  # Sat=cron dow 6, hour 12
        assert "weekly on Sat" in out["description"]

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


class TestPlan:
    @mock.patch("carbon_curve.build_profile")
    @mock.patch("cli.check_grid.parse_zones_input", lambda s: [{"zone": "CISO"}, {"zone": "PJM"}])
    def test_picks_best_zone_and_hour(self, bp, capsys):
        profiles = {
            "CISO": {h: 300.0 for h in range(24)} | {3: 80.0},
            "PJM": {h: 400.0 for h in range(24)} | {5: 350.0},
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
        bp.return_value = {h: 100.0 for h in range(24)} | {3: 50.0}  # cleanest hour 3
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
        bp.return_value = {h: 100.0 for h in range(24)} | {3: 50.0}
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
        bp.return_value = {h: 100.0 for h in range(24)}  # mean 100 gCO2/kWh
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
        bp.return_value = {h: 100.0 for h in range(24)} | {3: 10.0}  # spread big, cleanest 3
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
        bp.return_value = {h: 100.0 for h in range(24)} | {3: 10.0}
        (tmp_path / "a.yml").write_text("    - cron: '0 * * * *'\n")  # hourly, not shiftable
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
        import json

        import ledger

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
        bp.return_value = {h: 100.0 for h in range(24)} | {3: 10.0}  # cleanest hour 3, very clean
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
        bp.return_value = {h: 100.0 for h in range(24)} | {3: 10.0}
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
        import json

        import ledger

        data = ledger.empty_ledger()
        for hour in range(6):
            data = ledger.merge_curve_sample(data, "FR", hour, 100 + hour)
        p = tmp_path / "led.json"
        p.write_text(json.dumps(data))
        return f"file:{p}"

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
    def _curve_file(self, tmp_path, name, zone, base):
        import ledger

        data = ledger.empty_ledger()
        for hour in range(6):
            data = ledger.merge_curve_sample(data, zone, hour, base + hour)
        p = tmp_path / name
        p.write_text(json.dumps({"curve": data["curve"]}))
        return str(p)

    def test_merges_to_stdout(self, capsys, tmp_path):
        a = self._curve_file(tmp_path, "a.json", "FR", 100)
        b = self._curve_file(tmp_path, "b.json", "DE", 300)
        rc = cli.main(["merge-curves", a, b])
        assert rc == cli.EXIT_GREEN
        out = json.loads(capsys.readouterr().out)
        assert set(out["curve"]) == {"FR", "DE"}

    def test_writes_file(self, tmp_path):
        a = self._curve_file(tmp_path, "a.json", "FR", 100)
        dest = tmp_path / "pool.json"
        rc = cli.main(["merge-curves", a, "--output", str(dest)])
        assert rc == cli.EXIT_GREEN
        assert "FR" in json.loads(dest.read_text())["curve"]

    def test_errors_when_no_readable_files(self, tmp_path):
        rc = cli.main(["merge-curves", str(tmp_path / "missing.json")])
        assert rc == cli.EXIT_NODATA


class TestValidateCurves:
    def _curve_file(self, tmp_path, name, zone="FR", base=100):
        import ledger

        data = ledger.empty_ledger()
        for hour in range(6):
            data = ledger.merge_curve_sample(data, zone, hour, base + hour)
        p = tmp_path / name
        p.write_text(json.dumps({"curve": data["curve"]}))
        return str(p)

    def test_valid_file_passes(self, tmp_path):
        good = self._curve_file(tmp_path, "good.json")
        assert cli.main(["validate-curves", good]) == cli.EXIT_GREEN

    def test_invalid_file_fails(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"curve": {"FR": {"3": {"sum": 99999.0, "n": 1}}}}))
        assert cli.main(["validate-curves", str(p)]) == cli.EXIT_DIRTY

    def test_unreadable_file_fails(self, tmp_path):
        assert cli.main(["validate-curves", str(tmp_path / "missing.json")]) == cli.EXIT_DIRTY

    def test_mixed_batch_fails_if_any_bad(self, tmp_path):
        good = self._curve_file(tmp_path, "good.json")
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert cli.main(["validate-curves", good, str(bad)]) == cli.EXIT_DIRTY


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
