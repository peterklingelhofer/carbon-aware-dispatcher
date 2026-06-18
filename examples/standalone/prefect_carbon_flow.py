"""Example: gate a Prefect flow on grid cleanliness.

The gate task blocks until a target zone is clean, then the heavy task runs.

    pip install prefect
"""

from prefect import flow, task

from integrations.prefect_carbon import carbon_gate


@task
def wait_for_green():
    return carbon_gate(zones="auto:green", max_carbon=200)


@task
def retrain():
    print("grid is clean, running the heavy job now")


@flow
def carbon_aware_pipeline():
    wait_for_green()
    retrain()


if __name__ == "__main__":
    carbon_aware_pipeline()
