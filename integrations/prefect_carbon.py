"""Carbon-aware Prefect: a gate task that holds a flow until the grid is clean.

Prefect orchestrates the same deferrable batch loads (ETL, retrains, syncs) that
benefit most from clean-window scheduling. carbon_gate blocks until any target
zone is at or below max_carbon. Drop it at the top of a flow so downstream tasks
run on clean energy.

The Prefect import is lazy/optional: carbon_gate is a plain function (easy to
test and to call from any flow), and carbon_gate_task wraps it as a Prefect task
when Prefect is installed.
"""

from integrations.gate import carbon_gate, grid_is_clean, wait_until_clean

__all__ = ["carbon_gate", "carbon_gate_task", "grid_is_clean", "wait_until_clean"]


def _make_task():  # pragma: no cover - exercised only with Prefect installed
    try:
        from prefect import task
    except Exception:
        return None
    return task(name="carbon_gate")(carbon_gate)


carbon_gate_task = _make_task()
