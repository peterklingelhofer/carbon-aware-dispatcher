"""Tests for the weekly carbon digest."""

from datetime import date
from unittest import mock

import digest
import ledger


def _ledger_with(history):
    d = ledger.empty_ledger()
    d["history"] = history
    return d


class TestSummarizePeriod:
    def test_sums_within_window(self):
        today = date(2026, 6, 15)
        data = _ledger_with(
            [
                {"date": "2026-06-14", "saved_g": 100, "emitted_g": 20, "runs": 2},
                {"date": "2026-06-15", "saved_g": 50, "emitted_g": 10, "runs": 1},
                {"date": "2026-06-01", "saved_g": 999, "emitted_g": 999, "runs": 9},  # outside 7d
            ]
        )
        s = digest.summarize_period(data, 7, today)
        assert s["saved_g"] == 150
        assert s["emitted_g"] == 30
        assert s["runs"] == 3
        assert len(s["series"]) == 7
        assert s["series"][-1] == 50  # today is last

    def test_empty_ledger(self):
        s = digest.summarize_period(ledger.empty_ledger(), 7, date(2026, 6, 15))
        assert s["saved_g"] == 0
        assert s["runs"] == 0
        assert s["series"] == [0.0] * 7


class TestSparkline:
    def test_all_zero(self):
        assert digest.sparkline([0, 0, 0]) == digest.SPARK[0] * 3

    def test_scales_to_max(self):
        line = digest.sparkline([0, 5, 10])
        assert line[0] == digest.SPARK[0]
        assert line[-1] == digest.SPARK[-1]

    def test_empty(self):
        assert digest.sparkline([]) == ""


class TestRenderIssueBody:
    def test_contains_marker_and_windows(self):
        today = date(2026, 6, 15)
        week = {"saved_g": 150, "emitted_g": 30, "runs": 3, "series": [0, 0, 0, 0, 100, 50, 0]}
        month = {"saved_g": 1200, "emitted_g": 300, "runs": 20, "series": []}
        body = digest.render_issue_body(week, month, "1.2 kg over 20 builds", None, today)
        assert digest.MARKER in body
        assert "Last 7 days" in body
        assert "Last 30 days" in body
        assert "Lifetime" in body

    def test_budget_line(self):
        today = date(2026, 6, 15)
        z = {"saved_g": 0, "emitted_g": 0, "runs": 0, "series": []}
        body = digest.render_issue_body(
            z, z, "", {"used_pct": 90, "state": "warning", "remaining": 100}, today
        )
        assert "Carbon budget" in body
        assert "warning" in body


class TestBudgetStatus:
    def test_none_without_budget(self):
        assert digest._budget_status({}, ledger.empty_ledger(), date(2026, 6, 15)) is None

    def test_computes_state(self):
        data = _ledger_with([{"date": "2026-06-10", "saved_g": 0, "emitted_g": 900, "runs": 1}])
        st = digest._budget_status({"MONTHLY_BUDGET_GRAMS": "1000"}, data, date(2026, 6, 15))
        assert st["state"] == "warning"
        assert st["remaining"] == 100


class TestPostIssue:
    def test_no_token(self):
        assert digest.post_issue("o/r", "", "t", "b") is False

    @mock.patch("digest.base.request")
    def test_creates_when_absent(self, mock_request):
        # first call: list issues (none match) -> []  ; second: create -> ok
        mock_request.side_effect = [[], {"number": 5}]
        assert digest.post_issue("o/r", "tok", "title", "body") is True
        assert mock_request.call_count == 2

    @mock.patch("digest.base.request")
    def test_updates_when_present(self, mock_request):
        mock_request.side_effect = [
            [{"number": 7, "body": f"x {digest.MARKER} y"}],
            {"number": 7},
        ]
        assert digest.post_issue("o/r", "tok", "title", "body") is True
        # second call should be a PATCH to issue 7
        patch_call = mock_request.call_args_list[1]
        assert "/issues/7" in patch_call.args[0]
