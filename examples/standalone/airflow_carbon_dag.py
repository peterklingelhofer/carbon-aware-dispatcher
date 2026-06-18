"""Example: gate an Airflow DAG on grid cleanliness.

The heavy task (a nightly retrain, a big ETL backfill) only runs once a target
zone is clean. Use mode="reschedule" so the sensor frees its worker slot while
it waits instead of holding it for hours.

    pip install apache-airflow
    # drop this file in your DAGs folder
"""

from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator

from integrations.airflow_carbon import CarbonAwareSensor


def retrain():
    print("grid is clean, running the heavy job now")


with DAG(
    dag_id="carbon_aware_retrain",
    start_date=datetime(2026, 1, 1),
    schedule="0 0 * * *",
    catchup=False,
) as dag:
    wait_for_green = CarbonAwareSensor(
        task_id="wait_for_green",
        zones="auto:green",
        max_carbon=200,
        mode="reschedule",
        poke_interval=900,
        timeout=6 * 3600,
    )
    heavy = PythonOperator(task_id="retrain", python_callable=retrain)

    wait_for_green >> heavy
