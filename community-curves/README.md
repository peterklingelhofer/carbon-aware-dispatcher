# Community curve commons

This folder is the shared pool of hour-of-day carbon curves. Many zones have no
free historical API, but the curve self-accumulates per zone in each user's
ledger. Pooling those exports gives everyone a diurnal profile for zones nobody
could measure alone — coverage compounds with adoption.

## Contribute your curve

1. Export the curve your runs have accumulated:

   ```bash
   carbon-aware export-curves --output community-curves/<your-handle>.json
   ```

   (Set `LEDGER=gist:<id>` or `file:<path>` first so it knows where your ledger
   is.) The file holds only per-zone aggregates (`sum`/`count`) by hour of day
   and by day of week — no timestamps, repo names, or anything identifying.

2. Open a pull request adding that file. A
   [validation check](../.github/workflows/validate-community-curves.yml) runs on
   the PR — it rejects malformed files, hours outside 0-23, non-positive counts,
   implausible intensities (above 2000 gCO2/kWh), and sparse single-sample dumps,
   so bad data never reaches the pool. You can run it yourself first:

   ```bash
   carbon-aware validate-curves community-curves/<your-handle>.json
   ```

3. On merge, the
   [`publish-community-curve`](../.github/workflows/publish-community-curve.yml)
   workflow re-validates, merges every file here into a single pooled
   `community-curve.json` at the repo root, and commits it.

## Use the pool

Point `COMMUNITY_CURVE` at the published pool — a local file or the raw URL:

```bash
export COMMUNITY_CURVE="https://raw.githubusercontent.com/peterklingelhofer/carbon-aware-dispatcher/main/community-curve.json"
carbon-aware worth-it --zones FR        # now has a profile even with no local history
```

`suggest-cron`, `worth-it`, and `curve` all fall back to this pool when a zone
has no free historical API and no local ledger curve.
