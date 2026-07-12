"""Tests for forecast self-calibration."""

from datetime import datetime

import forecast_log


def _dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class TestRecordPrediction:
    def test_appends(self):
        doc = forecast_log.record_prediction(
            forecast_log.empty_log(), "GB", "2026-06-17T03:00Z", 120, "2026-06-16T12:00Z"
        )
        assert len(doc["predictions"]) == 1
        assert doc["predictions"][0]["actual"] is None

    def test_caps_to_max_records(self):
        doc = forecast_log.empty_log()
        for i in range(forecast_log.MAX_RECORDS + 25):
            doc = forecast_log.record_prediction(doc, "GB", "2026-06-17T03:00Z", i, "now")
        assert len(doc["predictions"]) == forecast_log.MAX_RECORDS


class TestResolveDue:
    def test_resolves_within_window_and_signs_error(self):
        doc = forecast_log.record_prediction(
            forecast_log.empty_log(), "GB", "2026-06-17T03:00Z", 120, "2026-06-16T12:00Z"
        )
        now = _dt("2026-06-17T03:30Z")  # 30 min after the target
        doc, n = forecast_log.resolve_due(doc, now, actual_intensity=100, zone="GB")
        assert n == 1
        # predicted 120 vs actual 100 -> forecast ran high by +20
        assert doc["predictions"][0]["error"] == 20.0

    def test_skips_other_zone(self):
        doc = forecast_log.record_prediction(
            forecast_log.empty_log(), "FR", "2026-06-17T03:00Z", 60, "now"
        )
        doc, n = forecast_log.resolve_due(doc, _dt("2026-06-17T03:10Z"), 80, zone="GB")
        assert n == 0

    def test_skips_outside_window(self):
        doc = forecast_log.record_prediction(
            forecast_log.empty_log(), "GB", "2026-06-17T03:00Z", 120, "now"
        )
        # 5 hours later is well past the 90-min resolve window
        doc, n = forecast_log.resolve_due(doc, _dt("2026-06-17T08:00Z"), 100, zone="GB")
        assert n == 0

    def test_does_not_double_resolve(self):
        doc = forecast_log.record_prediction(
            forecast_log.empty_log(), "GB", "2026-06-17T03:00Z", 120, "now"
        )
        now = _dt("2026-06-17T03:30Z")
        doc, _ = forecast_log.resolve_due(doc, now, 100, zone="GB")
        doc, n2 = forecast_log.resolve_due(doc, now, 90, zone="GB")
        assert n2 == 0  # already resolved
        assert doc["predictions"][0]["actual"] == 100


class TestReportAndCorrection:
    def _doc_with_errors(self, errors):
        preds = [
            {"zone": "GB", "predicted_intensity": 100, "actual": 100 - e, "error": float(e)}
            for e in errors
        ]
        return {"predictions": preds}

    def test_report_metrics(self):
        rep = forecast_log.accuracy_report(self._doc_with_errors([10, -10, 20, -20]))
        assert rep["n"] == 4
        assert rep["mae"] == 15.0
        assert rep["bias"] == 0.0

    def test_report_empty(self):
        assert forecast_log.accuracy_report(forecast_log.empty_log())["n"] == 0

    def test_bias_correction_needs_min_n(self):
        assert forecast_log.bias_correction(self._doc_with_errors([30, 30])) is None
        assert forecast_log.bias_correction(self._doc_with_errors([30] * 6)) == 30.0
