#!/usr/bin/env bash
# Carbon-aware Slurm submit wrapper
#
# HPC training/simulation jobs are large and usually deferrable, prime
# candidates for clean-window scheduling. This wrapper blocks until the grid is
# clean, then submits the batch job with sbatch. Because the CLI uses exit codes,
# it composes with && and needs no glue code.
#
# Use:
#   ./slurm-carbon-submit.sh my-training.sbatch
#
# Or gate from inside the batch script itself by running `carbon-aware
# wait-for-green` as the first line before srun.
#
# Requires either the installed console script (`pipx install carbon-aware-dispatcher`)
# or the container (swap the CARBON_AWARE command below for a `docker run ...`).

set -euo pipefail

CARBON_AWARE="${CARBON_AWARE:-carbon-aware}"
ZONES="${ZONES:-auto:green}"
MAX_CARBON="${MAX_CARBON:-200}"
MAX_WAIT="${MAX_WAIT:-6h}"
SBATCH_SCRIPT="${1:?usage: slurm-carbon-submit.sh <job.sbatch> [sbatch args...]}"
shift || true

if "$CARBON_AWARE" wait-for-green --zones "$ZONES" --max-carbon "$MAX_CARBON" --max-wait "$MAX_WAIT"; then
  echo "Grid is clean, submitting $SBATCH_SCRIPT"
  sbatch "$SBATCH_SCRIPT" "$@"
else
  echo "No green window within $MAX_WAIT, not submitting (retry next schedule)"
  exit 0
fi
