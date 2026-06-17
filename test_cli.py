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
