"""Tests for the sticky pull-request comment."""

import json
from unittest import mock

import pytest

import pr_comment


def _event_file(tmp_path, payload):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload))
    return str(path)


class TestBuildComment:
    def test_green_includes_marker_and_savings(self):
        body = pr_comment.build_comment(
            is_green=True,
            zone="CISO",
            intensity=89,
            max_carbon=250,
            co2_saved=1500,
            equivalent="~6.0 km not driven",
            lifetime="4.2 kg over 380 builds",
        )
        assert pr_comment.MARKER in body
        assert "Built on clean energy" in body
        assert "`CISO`" in body
        assert "89 gCO2eq/kWh" in body
        assert "1.5 kg (~6.0 km not driven)" in body
        assert "4.2 kg over 380 builds" in body

    def test_dirty_headline(self):
        body = pr_comment.build_comment(is_green=False, zone="PL", intensity=600, max_carbon=250)
        assert "deferred" in body
        # No savings row when nothing was saved
        assert "CO2 saved this run" not in body

    def test_dry_run_headline(self):
        body = pr_comment.build_comment(
            is_green=False, zone="PL", intensity=600, max_carbon=250, dry_run=True
        )
        assert "Report-only" in body

    def test_grams_formatting_under_1kg(self):
        body = pr_comment.build_comment(
            is_green=True, zone="GB", intensity=50, max_carbon=250, co2_saved=500
        )
        assert "500 g" in body

    def test_tier_row_present(self):
        body = pr_comment.build_comment(
            is_green=True, zone="GB", intensity=50, max_carbon=250, tier="green"
        )
        assert "Carbon tier" in body
        assert "green" in body

    def test_tier_omitted_when_unknown(self):
        body = pr_comment.build_comment(
            is_green=True, zone="GB", intensity=50, max_carbon=250, tier="unknown"
        )
        assert "Carbon tier" not in body

    def test_budget_row_present(self):
        body = pr_comment.build_comment(
            is_green=True,
            zone="GB",
            intensity=50,
            max_carbon=250,
            budget={"state": "warning", "used_pct": 82.5},
        )
        assert "Carbon budget" in body
        assert "82" in body
        assert "warning" in body

    def test_budget_row_omitted_when_none(self):
        body = pr_comment.build_comment(
            is_green=True, zone="GB", intensity=50, max_carbon=250, budget=None
        )
        assert "Carbon budget" not in body


class TestPrNumberFromEvent:
    @pytest.mark.parametrize(
        ("payload", "number"),
        [({"pull_request": {"number": 42}}, 42), ({"issue": {"number": 7}}, 7)],  # issue fallback
    )
    def test_reads_number(self, tmp_path, payload, number):
        assert pr_comment.pr_number_from_event(_event_file(tmp_path, payload)) == number

    def test_missing_file(self):
        assert pr_comment.pr_number_from_event("/no/such/file.json") is None


class TestPostComment:
    @pytest.fixture
    def event_path(self, tmp_path):
        return _event_file(tmp_path, {"pull_request": {"number": 42}})

    def test_skips_non_pr_event(self):
        assert pr_comment.post_comment("o/r", "tok", "push", "", "body") is False

    def test_skips_without_token(self):
        assert pr_comment.post_comment("o/r", "", "pull_request", "", "body") is False

    @mock.patch("pr_comment.base.request")
    def test_creates_new_comment_when_none_exists(self, mock_request, event_path):
        # find_existing -> empty list, POST -> created
        mock_request.side_effect = [[], {"id": 1}]
        ok = pr_comment.post_comment("o/r", "tok", "pull_request", event_path, "hello")
        assert ok is True
        post_call = mock_request.call_args_list[1]
        assert post_call.kwargs["method"] == "POST"
        assert "/issues/42/comments" in post_call.args[0]

    @mock.patch("pr_comment.base.request")
    def test_updates_existing_comment(self, mock_request, event_path):
        existing = [{"id": 99, "body": f"old {pr_comment.MARKER}"}]
        mock_request.side_effect = [existing, {"id": 99}]
        ok = pr_comment.post_comment("o/r", "tok", "pull_request", event_path, "updated")
        assert ok is True
        patch_call = mock_request.call_args_list[1]
        assert patch_call.kwargs["method"] == "PATCH"
        assert "/issues/comments/99" in patch_call.args[0]

    @mock.patch("pr_comment.base.request")
    def test_write_failure_returns_false(self, mock_request, event_path):
        mock_request.side_effect = [[], None]
        assert pr_comment.post_comment("o/r", "tok", "pull_request", event_path, "x") is False
