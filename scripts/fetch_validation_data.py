"""Fetch the raw grid data that docs/VALIDATION.md is computed from.

Two sources, both re-fetchable by anyone:

  - GB national carbon intensity (api.carbonintensity.org.uk, no key). Every
    half-hour carries BOTH the forecast National Grid published ahead of time
    and the settled actual, which is exactly the ground truth a forecast-skill
    study needs. This is the only free feed the project uses that grades itself.
  - EIA hourly fuel mix (api.eia.gov, free key). Stored as the raw per-fuel
    generation rows rather than a pre-computed intensity, so the average-vs-
    marginal analysis can be recomputed if the emission factors ever move.

Snapshots land in data/validation/ and are committed, so the numbers in
VALIDATION.md are reproducible without re-running the network.
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

UK_API_BASE = "https://api.carbonintensity.org.uk"
EIA_API_BASE = "https://api.eia.gov/v2"
# The UK API rejects a span longer than this in one request.
UK_MAX_SPAN_DAYS = 14
# EIA caps a single page at 5000 rows.
EIA_PAGE = 5000
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUT_DIR = os.path.join(DATA_DIR, "validation")
TIMEOUT = 60


def _get(url, params=None):
    for attempt in range(4):
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(2**attempt)
                continue
            raise SystemExit(f"HTTP {resp.status_code} for {url}: {resp.text[:200]}")
        except requests.RequestException as exc:
            if attempt == 3:
                raise SystemExit(f"request failed for {url}: {exc}") from exc
            time.sleep(2**attempt)
    raise SystemExit(f"gave up on {url}")


def fetch_gb(days, end):
    """GB national half-hourly (from, forecast, actual), oldest first."""
    rows = {}
    start = end - timedelta(days=days)
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=UK_MAX_SPAN_DAYS), end)
        url = (
            f"{UK_API_BASE}/intensity/"
            f"{cursor.strftime('%Y-%m-%dT%H:%MZ')}/{chunk_end.strftime('%Y-%m-%dT%H:%MZ')}"
        )
        print(f"  GB {cursor:%Y-%m-%d} -> {chunk_end:%Y-%m-%d}")
        for period in _get(url).get("data", []):
            intensity = period.get("intensity") or {}
            rows[period.get("from")] = (
                intensity.get("forecast"),
                intensity.get("actual"),
            )
        cursor = chunk_end
    return [(k, v[0], v[1]) for k, v in sorted(rows.items()) if k]


def fetch_eia(zone, days, end, api_key):
    """EIA hourly per-fuel generation rows (period, fueltype, mwh), oldest first."""
    start = end - timedelta(days=days)
    rows = []
    offset = 0
    while True:
        params = [
            ("api_key", api_key),
            ("frequency", "hourly"),
            ("data[0]", "value"),
            ("facets[respondent][]", zone),
            ("start", start.strftime("%Y-%m-%dT%H")),
            ("end", end.strftime("%Y-%m-%dT%H")),
            ("sort[0][column]", "period"),
            ("sort[0][direction]", "asc"),
            ("length", str(EIA_PAGE)),
            ("offset", str(offset)),
        ]
        payload = _get(f"{EIA_API_BASE}/electricity/rto/fuel-type-data/data", params=params)
        page = payload.get("response", {}).get("data", [])
        total = int(payload.get("response", {}).get("total", 0))
        rows.extend(page)
        print(f"  EIA {zone}: {len(rows)}/{total}")
        offset += EIA_PAGE
        if not page or offset >= total:
            break
    return [
        (r.get("period"), r.get("fueltype"), r.get("value"))
        for r in rows
        if r.get("value") is not None
    ]


def _write(path, header, rows):
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows -> {os.path.relpath(path, DATA_DIR)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gb-days", type=int, default=90)
    parser.add_argument("--eia-days", type=int, default=60)
    parser.add_argument("--eia-zones", default="CISO,PJM")
    parser.add_argument("--skip-gb", action="store_true")
    parser.add_argument("--skip-eia", action="store_true")
    args = parser.parse_args(argv)

    os.makedirs(OUT_DIR, exist_ok=True)
    # Truncate to the hour so a re-run on the same day produces the same window
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    if not args.skip_gb:
        print(f"GB national, {args.gb_days}d ending {end:%Y-%m-%dT%H}Z")
        _write(
            os.path.join(OUT_DIR, "gb-national-intensity.csv"),
            ["from", "forecast", "actual"],
            fetch_gb(days=args.gb_days, end=end),
        )

    if not args.skip_eia:
        api_key = os.environ.get("EIA_API_KEY", "")
        if not api_key:
            print("EIA_API_KEY unset; skipping EIA fetch", file=sys.stderr)
            return 0
        for zone in [z.strip() for z in args.eia_zones.split(",") if z.strip()]:
            print(f"EIA {zone}, {args.eia_days}d ending {end:%Y-%m-%dT%H}Z")
            _write(
                os.path.join(OUT_DIR, f"eia-{zone.lower()}-fuel-mix.csv"),
                ["period", "fueltype", "mwh"],
                fetch_eia(zone=zone, days=args.eia_days, end=end, api_key=api_key),
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
