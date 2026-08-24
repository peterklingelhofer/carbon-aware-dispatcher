"""Log GB forecasts WITH the lead time they were issued at.

docs/VALIDATION.md section 2 can only score the published GB forecast
lead-time-agnostically, because the API archive keeps one forecast per
settlement period and does not record when it was issued. That is not fixable
retrospectively: it has to be collected going forward.

Each run appends the current 48-hour-ahead forecast to
data/validation/gb-fw48h-log.csv as (issued_at, target, lead_minutes, forecast).
A later run of scripts/run_validation.py can then join that log against the
settled actuals and stratify skill by lead time properly. Appending is
idempotent per (issued_at, target), so re-running within an hour is harmless.
"""

import argparse
import csv
import os
from datetime import datetime, timezone

import requests

UK_API_BASE = "https://api.carbonintensity.org.uk"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "data", "validation", "gb-fw48h-log.csv")
HEADER = ["issued_at", "target", "lead_minutes", "forecast"]
TIMEOUT = 30


def _parse(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)


def fetch(now):
    """The 48h-ahead forecast as of now, as [(issued, target, lead_min, value)]."""
    url = f"{UK_API_BASE}/intensity/{now.strftime('%Y-%m-%dT%H:%MZ')}/fw48h"
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    issued = now.strftime("%Y-%m-%dT%H:%MZ")
    rows = []
    for period in resp.json().get("data", []):
        value = (period.get("intensity") or {}).get("forecast")
        target = period.get("from")
        if value is None or not target:
            continue
        lead = round((_parse(target) - now).total_seconds() / 60)
        rows.append((issued, target, lead, value))
    return rows


def append(rows):
    """Append rows not already present, keyed on (issued_at, target)."""
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    seen = set()
    if os.path.exists(LOG):
        with open(LOG) as fh:
            seen = {(r["issued_at"], r["target"]) for r in csv.DictReader(fh)}
    fresh = [r for r in rows if (r[0], r[1]) not in seen]
    with open(LOG, "a", newline="") as fh:
        writer = csv.writer(fh)
        if not seen:
            writer.writerow(HEADER)
        writer.writerows(fresh)
    return len(fresh)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    # Snap to the half hour the settlement periods are aligned to
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    now = now.replace(minute=0 if now.minute < 30 else 30)
    added = append(fetch(now))
    print(f"appended {added} forecast rows issued {now:%Y-%m-%dT%H:%M}Z")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
