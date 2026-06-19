"""Carbon-aware Dagster: gate an op/asset until the grid is clean.

Dagster orchestrates the same deferrable batch loads (asset materializations,
retrains, ETL) that benefit most from clean-window scheduling. carbon_gate blocks
until any target zone is at or below max_carbon; use grid_is_clean in a sensor to
only launch runs when the grid is already clean.

The Dagster import is lazy/optional: carbon_gate is a plain function (easy to
test and to call from any op or asset), and carbon_gate_op wraps it as a Dagster
op when Dagster is installed.
"""

from integrations.gate import grid_is_clean, wait_until_clean

__all__ = ["grid_is_clean", "wait_until_clean", "carbon_gate", "carbon_gate_op"]


def carbon_gate(zones="auto:green", max_carbon=200.0, max_wait_s=6 * 3600, poll_s=900, tokens=None):
    """Block until the grid is clean (or the max wait elapses). Returns True if clean.

    Call directly inside an op or asset, or use carbon_gate_op for a Dagster op.
    """
    return wait_until_clean(zones, max_carbon, max_wait_s, poll_s, tokens=tokens)


def _make_op():  # pragma: no cover - exercised only with Dagster installed
    try:
        from dagster import op
    except Exception:
        return None

    @op(name="carbon_gate")
    def carbon_gate_op(context=None):
        return carbon_gate()

    return carbon_gate_op


carbon_gate_op = _make_op()
