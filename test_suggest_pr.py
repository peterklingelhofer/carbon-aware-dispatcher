"""Tests for the suggest-mode cron PR opener."""

import base64
from unittest import mock

import suggest_pr


class TestSwapCronHour:
    def test_simple_daily(self):
        assert suggest_pr.swap_cron_hour("0 14 * * *", 3) == "0 3 * * *"

    def test_preserves_minute(self):
        assert suggest_pr.swap_cron_hour("30 9 * * *", 23) == "30 23 * * *"

    def test_none_when_already_that_hour(self):
        assert suggest_pr.swap_cron_hour("0 3 * * *", 3) is None

    def test_none_for_non_single_hour(self):
        assert suggest_pr.swap_cron_hour("*/15 * * * *", 3) is None
        assert suggest_pr.swap_cron_hour("0 */6 * * *", 3) is None

    def test_none_for_malformed(self):
        assert suggest_pr.swap_cron_hour("0 14 * *", 3) is None


class TestRewriteCrons:
    def test_rewrites_daily_cron_line(self):
        text = "on:\n  schedule:\n    - cron: '0 14 * * *'\n"
        new, changes = suggest_pr.rewrite_crons(text, 3)
        assert "0 3 * * *" in new
        assert changes == [("0 14 * * *", "0 3 * * *")]

    def test_leaves_complex_cron(self):
        text = "    - cron: '*/15 * * * *'\n"
        new, changes = suggest_pr.rewrite_crons(text, 3)
        assert new == text and changes == []

    def test_no_cron(self):
        text = "name: build\n"
        assert suggest_pr.rewrite_crons(text, 3) == (text, [])


class TestOpenCronPr:
    def test_skips_without_hour(self, capsys):
        assert suggest_pr.open_cron_pr("o/r", "tok", "wf.yml", None) is False

    def test_skips_without_inputs(self):
        assert suggest_pr.open_cron_pr("", "tok", "wf.yml", 3) is False

    @mock.patch("suggest_pr.base.request")
    def test_no_change_when_no_daily_cron(self, req, capsys):
        content = base64.b64encode(b"    - cron: '*/15 * * * *'\n").decode()
        req.return_value = {"content": content, "sha": "abc"}
        assert suggest_pr.open_cron_pr("o/r", "tok", "wf.yml", 3) is False
        # only the file read happened; no branch/commit/PR
        assert req.call_count == 1

    @mock.patch("suggest_pr.base.request")
    def test_full_flow_opens_pr(self, req):
        content = base64.b64encode(b"    - cron: '0 14 * * *'\n").decode()
        req.side_effect = [
            {"content": content, "sha": "filesha"},  # read base file
            {"object": {"sha": "basesha"}},  # base branch head
            {"ref": "refs/heads/carbon-aware/cron"},  # create branch
            {"sha": "filesha"},  # file on branch
            {"commit": {"sha": "new"}},  # PUT commit
            {"number": 7, "html_url": "u"},  # PR
        ]
        assert suggest_pr.open_cron_pr("o/r", "tok", "wf.yml", 3, zone="GB") is True
        # the PUT carries the rewritten content
        put_call = req.call_args_list[4]
        put_body = put_call.kwargs["json_body"]
        assert base64.b64decode(put_body["content"]).decode() == "    - cron: '0 3 * * *'\n"
        assert put_body["branch"] == suggest_pr.BRANCH
