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

# Reuse grid readings for 5 min by default: a composed gate (check && run, or
# repeated CronJob pods sharing a mounted cache) avoids re-fetching. Set to 0 to
# disable.
ENV CARBON_CACHE_TTL=300

# Only the runtime dependency; the engine itself is pure-Python
RUN pip install --no-cache-dir "requests>=2.32,<3"

# Every first-party module the CLI can reach at runtime (several are imported
# lazily: carbon_curve/suggest_pr power curve/worth-it/suggest-cron/audit/score).
COPY check_grid.py cli.py ledger.py notify.py digest.py pr_comment.py \
     carbon_curve.py suggest_pr.py marginal.py forecast_log.py ./
COPY providers ./providers

ENTRYPOINT ["python", "/app/cli.py"]
CMD ["check"]
