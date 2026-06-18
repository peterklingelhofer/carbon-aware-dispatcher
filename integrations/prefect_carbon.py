"""Carbon-aware Prefect: a gate task that holds a flow until the grid is clean.

Prefect orchestrates the same deferrable batch loads (ETL, retrains, syncs) that
benefit most from clean-window scheduling. carbon_gate blocks until any target
zone is at or below max_carbon; drop it at the top of a flow so downstream tasks
run on clean energy.

The Prefect import is lazy/optional: carbon_gate is a plain function (easy to
test and to call from any flow), and carbon_gate_task wraps it as a Prefect task
when Prefect is installed.
"""

from integrations.gate import grid_is_clean, wait_until_clean

__all__ = ["grid_is_clean", "wait_until_clean", "carbon_gate", "carbon_gate_task"]


def carbon_gate(zones="auto:green", max_carbon=200.0, max_wait_s=6 * 3600, poll_s=900, tokens=None):
    """Block until the grid is clean (or the max wait elapses). Returns True if clean.

    Call directly inside a flow, or use carbon_gate_task for a Prefect task.
    """
    return wait_until_clean(zones, max_carbon, max_wait_s, poll_s, tokens=tokens)


def _make_task():  # pragma: no cover - exercised only with Prefect installed
    try:
        from prefect import task
    except Exception:
        return None
    return task(name="carbon_gate")(carbon_gate)


carbon_gate_task = _make_task()
