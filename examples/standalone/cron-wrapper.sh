#!/usr/bin/env bash
# Carbon-aware cron / systemd-timer wrapper
#
# Gate any deferrable job on grid carbon intensity from plain cron. Because the
# CLI uses exit codes, you compose it with && and need no glue code.
#
# Install (run nightly, but only execute when the grid is clean within 6h):
#   0 1 * * *  /path/to/cron-wrapper.sh >> /var/log/green-batch.log 2>&1
#
# Requires either the installed console script (`pipx install carbon-aware-dispatcher`)
# or the container (swap the CARBON_AWARE command below for a `docker run ...`).

set -euo pipefail

CARBON_AWARE="${CARBON_AWARE:-carbon-aware}"
ZONES="${ZONES:-auto:green}"
MAX_CARBON="${MAX_CARBON:-200}"
MAX_WAIT="${MAX_WAIT:-6h}"

if "$CARBON_AWARE" wait-for-green --zones "$ZONES" --max-carbon "$MAX_CARBON" --max-wait "$MAX_WAIT"; then
  echo "Grid is clean, running batch job"
  # Replace with your actual deferrable work:
  ./run-batch-job.sh
else
  echo "No green window within $MAX_WAIT, skipping this run to avoid dirty-grid compute"
  exit 0
fi
