"""Carbon-aware Dagster: gate an op/asset until the grid is clean.

Dagster orchestrates the same deferrable batch loads (asset materializations,
retrains, ETL) that benefit most from clean-window scheduling. carbon_gate blocks
until any target zone is at or below max_carbon. Use grid_is_clean in a sensor to
only launch runs when the grid is already clean.

The Dagster import is lazy/optional: carbon_gate is a plain function (easy to
test and to call from any op or asset), and carbon_gate_op wraps it as a Dagster
op when Dagster is installed.
"""

from integrations.gate import carbon_gate, grid_is_clean, wait_until_clean

__all__ = ["carbon_gate", "carbon_gate_op", "grid_is_clean", "wait_until_clean"]


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
