"""Example: carbon-aware Dagster job and sensor.

The gate op blocks the job until a target zone is clean; the sensor only requests
runs when the grid is already clean, so a heavy materialization lands on clean
energy either way.

    pip install dagster
"""

from dagster import RunRequest, SkipReason, job, op, sensor

from integrations.dagster_carbon import carbon_gate, grid_is_clean


@op
def wait_for_green():
    return carbon_gate(zones="auto:green", max_carbon=200)


@op
def retrain(_gate):
    print("grid is clean — running the heavy job now")


@job
def carbon_aware_job():
    retrain(wait_for_green())


@sensor(job=carbon_aware_job)
def only_when_green(_context):
    if grid_is_clean(zones="auto:green", max_carbon=200):
        return RunRequest(run_key=None)
    return SkipReason("grid is not clean yet")
