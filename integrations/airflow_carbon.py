"""Carbon-aware Airflow: hold a DAG until the grid is clean.

Batch ETL, model retraining, and report generation are exactly the deferrable
loads Airflow orchestrates, so gating a task on grid cleanliness shifts real
megawatt-hours onto clean windows. CarbonAwareSensor pokes until any target
zone is at or below max_carbon, then lets downstream tasks run.

The Airflow import is lazy/optional, so this module imports fine without Airflow
installed; the sensor then subclasses object and exposes poke() for testing.
"""

from integrations.gate import grid_is_clean

__all__ = ["grid_is_clean", "CarbonAwareSensor"]


try:  # pragma: no cover - import shim depends on the installed Airflow version
    from airflow.sensors.base import BaseSensorOperator as _Base
except Exception:  # pragma: no cover
    _Base = object


class CarbonAwareSensor(_Base):
    """An Airflow sensor that succeeds once the grid is clean enough.

    Usage:
        from integrations.airflow_carbon import CarbonAwareSensor
        gate = CarbonAwareSensor(
            task_id="wait_for_green", zones="auto:green", max_carbon=200,
            mode="reschedule", poke_interval=900, timeout=6 * 3600,
        )
        gate >> heavy_training_task

    Use mode="reschedule" so the worker slot is freed between pokes instead of
    blocking. Tokens for paid zones go in the tokens dict (eia/emaps/entsoe).
    """

    def __init__(self, zones="auto:green", max_carbon=200.0, tokens=None, **kwargs):
        if _Base is not object:
            super().__init__(**kwargs)
        self.zones = zones
        self.max_carbon = max_carbon
        self.tokens = tokens or {}

    def poke(self, context=None):
        """True when the grid is clean enough; Airflow re-pokes until then."""
        return grid_is_clean(self.zones, self.max_carbon, **self.tokens)
