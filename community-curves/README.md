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
   is.) The file holds only per-zone, per-hour aggregates (`sum`/`count`) — no
   timestamps, repo names, or anything identifying.

2. Open a pull request adding that file. On merge, the
   [`publish-community-curve`](../.github/workflows/publish-community-curve.yml)
   workflow merges every file here into a single pooled `community-curve.json`
   at the repo root and commits it.

## Use the pool

Point `COMMUNITY_CURVE` at the published pool — a local file or the raw URL:

```bash
export COMMUNITY_CURVE="https://raw.githubusercontent.com/peterklingelhofer/carbon-aware-dispatcher/main/community-curve.json"
carbon-aware worth-it --zones FR        # now has a profile even with no local history
```

`suggest-cron`, `worth-it`, and `curve` all fall back to this pool when a zone
has no free historical API and no local ledger curve.
