"""Tests for the cumulative carbon-savings ledger."""

import json
import os
import tempfile
from unittest import mock

import ledger


class TestMergeEntry:
    def test_first_entry(self):
        data = ledger.merge_entry(ledger.empty_ledger(), 100, "2026-06-14")
        assert data["totals"]["co2_saved_grams"] == 100
        assert data["totals"]["runs"] == 1
        assert data["totals"]["first_run"] == "2026-06-14"
        assert data["totals"]["last_run"] == "2026-06-14"
        assert data["history"] == [
            {"date": "2026-06-14", "saved_g": 100, "emitted_g": 0, "runs": 1}
        ]

    def test_accumulates_across_days(self):
        d = ledger.merge_entry(ledger.empty_ledger(), 100, "2026-06-13")
        d = ledger.merge_entry(d, 50, "2026-06-14")
        assert d["totals"]["co2_saved_grams"] == 150
        assert d["totals"]["runs"] == 2
        assert d["totals"]["first_run"] == "2026-06-13"
        assert d["totals"]["last_run"] == "2026-06-14"
        assert len(d["history"]) == 2

    def test_same_day_aggregates_in_one_bucket(self):
        d = ledger.merge_entry(ledger.empty_ledger(), 100, "2026-06-14")
        d = ledger.merge_entry(d, 40, "2026-06-14")
        assert len(d["history"]) == 1
        assert d["history"][0] == {
            "date": "2026-06-14",
            "saved_g": 140,
            "emitted_g": 0,
            "runs": 2,
        }
        assert d["totals"]["runs"] == 2

    def test_negative_savings_clamped_but_counts_run(self):
        d = ledger.merge_entry(ledger.empty_ledger(), -25, "2026-06-14")
        assert d["totals"]["co2_saved_grams"] == 0
        assert d["totals"]["runs"] == 1

    def test_does_not_mutate_input(self):
        original = ledger.empty_ledger()
        ledger.merge_entry(original, 100, "2026-06-14")
        assert original == {"schemaVersion": 1, "totals": {}, "history": []}

    def test_tracks_emitted(self):
        d = ledger.merge_entry(ledger.empty_ledger(), 100, "2026-06-14", emitted_grams=30)
        d = ledger.merge_entry(d, 50, "2026-06-15", emitted_grams=20)
        assert d["totals"]["co2_emitted_grams"] == 50
        assert d["history"][0]["emitted_g"] == 30

    def test_month_to_date_emitted(self):
        d = ledger.empty_ledger()
        d = ledger.merge_entry(d, 0, "2026-05-31", emitted_grams=100)  # prior month
        d = ledger.merge_entry(d, 0, "2026-06-02", emitted_grams=40)
        d = ledger.merge_entry(d, 0, "2026-06-14", emitted_grams=10)
        assert ledger.month_to_date_emitted(d, "2026-06") == 50
        assert ledger.month_to_date_emitted(d, "2026-05") == 100

    def test_month_to_date_emitted_year_boundary(self):
        # The YYYY-MM prefix must not bleed across years
        d = ledger.empty_ledger()
        d = ledger.merge_entry(d, 0, "2025-01-15", emitted_grams=70)  # prior year, same month num
        d = ledger.merge_entry(d, 0, "2026-01-10", emitted_grams=30)
        assert ledger.month_to_date_emitted(d, "2026-01") == 30
        assert ledger.month_to_date_emitted(d, "2025-01") == 70

    def test_curve_sample_accumulates(self):
        d = ledger.empty_ledger()
        d = ledger.merge_curve_sample(d, "FR", 12, 80)
        d = ledger.merge_curve_sample(d, "FR", 12, 100)
        d = ledger.merge_curve_sample(d, "FR", 3, 200)
        assert d["curve"]["FR"]["12"] == {"sum": 180.0, "n": 2}
        assert d["curve"]["FR"]["3"] == {"sum": 200.0, "n": 1}

    def test_curve_sample_noop_on_missing(self):
        d = ledger.empty_ledger()
        assert ledger.merge_curve_sample(d, None, 12, 80) == d
        assert ledger.merge_curve_sample(d, "FR", None, 80) == d
        assert ledger.merge_curve_sample(d, "FR", 12, None) == d

    def test_curve_preserved_across_merge_entry(self):
        d = ledger.merge_curve_sample(ledger.empty_ledger(), "FR", 12, 80)
        d = ledger.merge_entry(d, 100, "2026-06-17", emitted_grams=10)
        assert d["curve"]["FR"]["12"]["n"] == 1  # survived the run merge

    def test_green_runs_tracked(self):
        d = ledger.empty_ledger()
        d = ledger.merge_entry(d, 0, "2026-06-17", is_green=True)
        d = ledger.merge_entry(d, 0, "2026-06-17", is_green=False)
        d = ledger.merge_entry(d, 0, "2026-06-17", is_green=True)
        assert d["totals"]["green_runs"] == 2
        assert d["totals"]["runs"] == 3
        assert d["history"][0]["green"] == 2

    def test_no_green_key_without_is_green(self):
        # Backward compatible: omitting is_green leaves the daily entry shape clean
        d = ledger.merge_entry(ledger.empty_ledger(), 100, "2026-06-17")
        assert "green" not in d["history"][0]
        assert "green_runs" not in d["totals"]

    def test_sla_window(self):
        d = ledger.empty_ledger()
        d = ledger.merge_entry(d, 0, "2026-06-15", is_green=True)
        d = ledger.merge_entry(d, 0, "2026-06-16", is_green=False)
        d = ledger.merge_entry(d, 0, "2026-05-31", is_green=True)  # prior month
        assert ledger.sla_window(d, "2026-06") == (1, 2)  # 1 green of 2 in June
        assert ledger.sla_window(d, "") == (2, 3)  # lifetime: 2 green of 3

    def test_curve_mean(self):
        d = ledger.empty_ledger()
        for hour in range(6):
            d = ledger.merge_curve_sample(d, "FR", hour, 100 + hour * 10)  # 100..150, mean 125
        assert ledger.curve_mean(d, "FR") == 125.0
        assert ledger.curve_mean(ledger.empty_ledger(), "FR") == 0.0

    def test_avoided_emissions_accrue(self, tmp_path):
        path = str(tmp_path / "led.json")
        # Seed a 6-hour curve for FR (mean 125) via direct samples + a save
        data = ledger.empty_ledger()
        for hour in range(6):
            data = ledger.merge_curve_sample(data, "FR", hour, 100 + hour * 10)
        import json

        (tmp_path / "led.json").write_text(json.dumps(data))
        # A run at intensity 25 (vs mean 125) with 10 kWh -> avoided (125-25)*10 = 1000
        summary = ledger.record_savings(
            f"file:{path}", "", 0, "2026-06-17", 0, zone="FR", intensity=25, hour=3, energy_kwh=10
        )
        assert summary["avoided_total"] == 1000.0

    def test_no_avoided_without_curve(self, tmp_path):
        path = str(tmp_path / "led.json")
        summary = ledger.record_savings(
            f"file:{path}", "", 0, "2026-06-17", 0, zone="FR", intensity=25, hour=3, energy_kwh=10
        )
        assert summary["avoided_total"] == 0.0  # no curve history yet

    def test_curve_profile_needs_min_hours(self):
        d = ledger.empty_ledger()
        for hour in range(5):  # only 5 distinct hours < min 6
            d = ledger.merge_curve_sample(d, "FR", hour, 100 + hour)
        assert ledger.curve_profile(d, "FR") == {}
        d = ledger.merge_curve_sample(d, "FR", 5, 105)
        prof = ledger.curve_profile(d, "FR")
        assert len(prof) == 6 and prof[0] == 100.0

    def test_status_badge_payload_colors(self):
        assert ledger.status_badge_payload("GB", 90, "green")["color"] == "brightgreen"
        assert ledger.status_badge_payload("GB", 250, "amber")["color"] == "yellow"
        assert ledger.status_badge_payload("PL", 600, "red")["color"] == "red"
        p = ledger.status_badge_payload("GB", 90, "green")
        assert "GB" in p["message"] and "90" in p["message"]

    def test_history_capped(self):
        d = ledger.empty_ledger()
        for i in range(ledger.HISTORY_CAP + 20):
            # distinct dates so each lands in its own bucket
            d = ledger.merge_entry(d, 1, f"day-{i:04d}")
        assert len(d["history"]) == ledger.HISTORY_CAP
        # oldest entries dropped, newest kept
        assert d["history"][-1]["date"] == f"day-{ledger.HISTORY_CAP + 19:04d}"


class TestFormatAndBadge:
    def test_format_grams(self):
        assert ledger.format_total(850) == "850 g"

    def test_format_kg(self):
        assert ledger.format_total(4200) == "4.2 kg"

    def test_badge_payload(self):
        d = ledger.merge_entry(ledger.empty_ledger(), 4200, "2026-06-14")
        payload = ledger.badge_payload(d)
        assert payload["schemaVersion"] == 1
        assert payload["label"] == "CO2 saved"
        assert "4.2 kg over 1 builds" == payload["message"]
        assert payload["color"] == "brightgreen"

    def test_badge_payload_empty_is_grey(self):
        payload = ledger.badge_payload(ledger.empty_ledger())
        assert payload["color"] == "lightgrey"


class TestParseConfig:
    def test_gist(self):
        assert ledger.parse_config("gist:abc123") == ("gist", "abc123")

    def test_file(self):
        assert ledger.parse_config("file:/tmp/x.json") == ("file", "/tmp/x.json")

    def test_empty(self):
        assert ledger.parse_config("") == (None, None)

    def test_unknown_prefix(self):
        assert ledger.parse_config("s3:bucket") == (None, None)

    def test_whitespace_trimmed(self):
        assert ledger.parse_config("  gist: abc  ") == ("gist", "abc")


class TestFileBackend:
    def test_round_trip(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        os.unlink(path)  # start with no file
        try:
            s1 = ledger.record_savings(f"file:{path}", "", 100, "2026-06-14", emitted_grams=25)
            assert s1["total_grams"] == 100
            assert s1["total_runs"] == 1
            assert s1["badge_url"] is None
            assert s1["message"] == "100 g over 1 builds"
            assert s1["emitted_mtd"] == 25

            s2 = ledger.record_savings(f"file:{path}", "", 50, "2026-06-15")
            assert s2["total_grams"] == 150
            assert s2["total_runs"] == 2

            with open(path) as fh:
                stored = json.load(fh)
            assert stored["totals"]["co2_saved_grams"] == 150
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_corrupt_file_starts_fresh(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{not valid json")
            path = f.name
        try:
            s = ledger.record_savings(f"file:{path}", "", 100, "2026-06-14")
            assert s["total_grams"] == 100
            assert s["total_runs"] == 1
        finally:
            os.unlink(path)

    def test_disabled_returns_none(self):
        assert ledger.record_savings("", "", 100, "2026-06-14") is None


class TestGistBackend:
    def test_missing_token_skips(self):
        assert ledger.record_savings("gist:abc", "", 100, "2026-06-14") is None

    @mock.patch("ledger.base.request")
    def test_reads_merges_writes_and_builds_badge_url(self, mock_request):
        existing = {
            "schemaVersion": 1,
            "totals": {"co2_saved_grams": 200, "runs": 2, "first_run": "2026-06-01"},
            "history": [{"date": "2026-06-01", "saved_g": 200, "runs": 2}],
        }
        read_response = {
            "owner": {"login": "octocat"},
            "files": {ledger.LEDGER_FILENAME: {"content": json.dumps(existing)}},
        }
        # First call (GET read) returns the gist, second (PATCH write) succeeds
        mock_request.side_effect = [read_response, {"id": "abc"}]

        summary = ledger.record_savings("gist:abc", "tok", 100, "2026-06-14")
        assert summary["total_grams"] == 300
        assert summary["total_runs"] == 3
        assert summary["badge_url"] == (
            "https://img.shields.io/endpoint?url="
            "https://gist.githubusercontent.com/octocat/abc/raw/carbon-badge.json"
        )

        # The PATCH body should include both the ledger and badge files
        patch_call = mock_request.call_args_list[1]
        body = patch_call.kwargs["json_body"]
        assert ledger.LEDGER_FILENAME in body["files"]
        assert ledger.BADGE_FILENAME in body["files"]
        badge = json.loads(body["files"][ledger.BADGE_FILENAME]["content"])
        assert badge["message"] == "300 g over 3 builds"

    @mock.patch("ledger.base.request")
    def test_write_failure_returns_none(self, mock_request):
        mock_request.side_effect = [{"owner": {"login": "x"}, "files": {}}, None]
        assert ledger.record_savings("gist:abc", "tok", 100, "2026-06-14") is None

    @mock.patch("ledger.base.request")
    def test_write_status_badge_url(self, mock_request):
        mock_request.return_value = {"owner": {"login": "octocat"}}
        url = ledger.write_status_badge("abc", "tok", "GB", 90, "green")
        assert url == (
            "https://img.shields.io/endpoint?url="
            "https://gist.githubusercontent.com/octocat/abc/raw/carbon-now.json"
        )
        body = mock_request.call_args.kwargs["json_body"]
        assert ledger.STATUS_BADGE_FILENAME in body["files"]

    def test_write_status_badge_no_token(self):
        assert ledger.write_status_badge("abc", "", "GB", 90, "green") is None

    @mock.patch("ledger.base.request")
    def test_write_status_badge_failure(self, mock_request):
        mock_request.return_value = None
        assert ledger.write_status_badge("abc", "tok", "GB", 90, "green") is None

    @mock.patch("ledger.base.request")
    def test_empty_gist_starts_fresh(self, mock_request):
        mock_request.side_effect = [{"owner": {"login": "x"}, "files": {}}, {"id": "abc"}]
        summary = ledger.record_savings("gist:abc", "tok", 75, "2026-06-14")
        assert summary["total_grams"] == 75
        assert summary["total_runs"] == 1
