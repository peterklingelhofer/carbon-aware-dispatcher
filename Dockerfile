# Carbon-Aware Dispatcher CLI container
#
# A tiny image exposing the `carbon-aware` CLI so any scheduler (Kubernetes
# CronJob, Nomad, Airflow KubernetesPodOperator, plain `docker run`) can gate or
# time deferrable work on grid carbon intensity.
#
#   docker build -t carbon-aware .
#   docker run --rm carbon-aware check --zones GB,CISO --max-carbon 200
#
# Exit code 0 = green, 1 = dirty/timeout, 2 = no data. Compose with && in an
# initContainer or a wrapper job.

FROM python:3.12-slim

WORKDIR /app

# Only the runtime dependency; the engine itself is pure-Python
RUN pip install --no-cache-dir "requests>=2.32,<3"

COPY check_grid.py cli.py ledger.py notify.py digest.py pr_comment.py ./
COPY providers ./providers

ENTRYPOINT ["python", "/app/cli.py"]
CMD ["check"]
