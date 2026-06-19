"""Forecast self-calibration: grade our own green-window predictions over time.

A free service that measures how good its own forecasts are is rare, and the
record builds trust (and a bias correction). Each run records the forecast it
made (predicted time + intensity). A later run, once that time has passed,
resolves the prediction against the actual reading and accrues the error.
accuracy_report() summarizes mean error and bias; bias_correction() returns the
systematic offset to subtract from future heuristic forecasts. All functions are
pure; the CLI handles persistence and the network.
"""

from datetime import datetime, timezone

# Keep the log bounded so a long-running scheduler never grows it without limit.
MAX_RECORDS = 500
# A prediction for time T is resolved against the actual reading taken within
# this many minutes after T (matched to a typical calibration cadence).
DEFAULT_RESOLVE_WINDOW_MIN = 90
# Don't suggest a bias correction until enough predictions have resolved.
MIN_RESOLVED_FOR_CORRECTION = 5


def empty_log():
    return {"predictions": []}


def _parse(ts):
    """Parse an ISO timestamp to an aware UTC datetime, or None."""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def record_prediction(doc, zone, predicted_at, predicted_intensity, made_at):
    """Append a forecast to the log (pure: returns a new doc, capped to MAX_RECORDS)."""
    preds = list((doc or empty_log()).get("predictions", []))
    preds.append(
        {
            "zone": zone,
            "predicted_at": predicted_at,
            "predicted_intensity": predicted_intensity,
            "made_at": made_at,
            "actual": None,
            "error": None,
        }
    )
    return {"predictions": preds[-MAX_RECORDS:]}


def resolve_due(doc, now, actual_intensity, zone=None, window_min=DEFAULT_RESOLVE_WINDOW_MIN):
    """Resolve predictions whose target time has just arrived, using the actual reading.

    A prediction for time T is resolved with an actual reading taken between T and
    T + window_min. Predictions for other zones, or far from now, are left alone.
    error = predicted - actual (positive means the forecast ran high). Returns
    (new_doc, n_resolved).
    """
    if actual_intensity is None:
        return doc, 0
    preds = [dict(p) for p in doc.get("predictions", [])]
    resolved = 0
    for p in preds:
        if p.get("actual") is not None or p.get("predicted_intensity") is None:
            continue
        if zone is not None and p.get("zone") != zone:
            continue
        target = _parse(p.get("predicted_at"))
        if target is None:
            continue
        delta_min = (now - target).total_seconds() / 60.0
        if 0 <= delta_min <= window_min:
            p["actual"] = actual_intensity
            p["error"] = round(p["predicted_intensity"] - actual_intensity, 1)
            resolved += 1
    return {"predictions": preds}, resolved


def _resolved_errors(doc):
    return [p["error"] for p in doc.get("predictions", []) if p.get("error") is not None]


def accuracy_report(doc):
    """Summarize resolved predictions: n, mae, bias (signed), rmse."""
    errs = _resolved_errors(doc)
    n = len(errs)
    if n == 0:
        return {"n": 0, "mae": None, "bias": None, "rmse": None}
    mae = sum(abs(e) for e in errs) / n
    bias = sum(errs) / n
    rmse = (sum(e * e for e in errs) / n) ** 0.5
    return {"n": n, "mae": round(mae, 1), "bias": round(bias, 1), "rmse": round(rmse, 1)}


def bias_correction(doc, min_n=MIN_RESOLVED_FOR_CORRECTION):
    """Systematic offset to subtract from future forecasts, or None if too few.

    A positive value means forecasts run high (predict dirtier than reality), so
    callers subtract it. Returns None until at least min_n predictions resolve.
    """
    errs = _resolved_errors(doc)
    if len(errs) < min_n:
        return None
    return round(sum(errs) / len(errs), 1)
