# Carbon-Aware Dispatcher CLI container
#
# A tiny image exposing the `carbon-aware` CLI so any scheduler (Kubernetes
# CronJob, Nomad, Airflow KubernetesPodOperator, plain `docker run`) can gate or
# time deferrable work on grid carbon intensity
#
#   docker build -t carbon-aware .
#   docker run --rm carbon-aware check --zones GB,CISO --max-carbon 200
#
# Exit code 0 = green, 1 = dirty/timeout, 2 = no data, 3 = usage error. Compose
# with && in an initContainer or a wrapper job

FROM python:3.12-slim

WORKDIR /app

# Reuse grid readings for 5 min by default: a composed gate (check && run, or
# repeated CronJob pods sharing a mounted cache) avoids re-fetching. Set to 0 to
# disable
ENV CARBON_CACHE_TTL=300

# Install the package itself, so the image ships exactly the files and
# dependencies pyproject.toml declares
RUN --mount=type=bind,target=/src,rw pip install --no-cache-dir /src

ENTRYPOINT ["carbon-aware"]
CMD ["check"]
