"""Cumulative carbon-savings ledger.

Each run records the CO2 it saved to a persistent store, so the impact compounds
into a lifetime total you can watch grow instead of a number that vanishes after
one build. Two backends, selected by the `ledger` input:

  - ``gist:<id>``   persist to a GitHub Gist (needs a gist-scoped token via the
                    ``gist_token`` input). The gist also publishes a shields.io
                    endpoint badge and feeds the Pages dashboard.
  - ``file:<path>`` persist to a local JSON file (handy for self-hosted runners,
                    local testing, or committing the file yourself).

The ledger is intentionally tiny and self-healing: a missing or corrupt store
starts fresh rather than failing the build. Tracking savings must never break
someone's CI, so every IO path degrades to a warning and a skip.
"""

import json

from providers import base

LEDGER_FILENAME = "carbon-ledger.json"
BADGE_FILENAME = "carbon-badge.json"
GIST_API = "https://api.github.com/gists"

# Keep at most a year of daily history so the gist/dashboard stays small.
HISTORY_CAP = 365


def empty_ledger():
    """Return a fresh, empty ledger structure."""
    return {"schemaVersion": 1, "totals": {}, "history": []}


def merge_entry(data, saved_grams, date_str):
    """Fold one run's savings into the ledger, aggregating history by day.

    Pure: returns a new dict, never mutates the input. Negative savings are
    clamped to zero so a dirty-grid run still counts as a build without
    subtracting from the lifetime total.
    """
    saved = max(0.0, float(saved_grams or 0))

    totals = dict(data.get("totals") or {})
    totals["co2_saved_grams"] = round(float(totals.get("co2_saved_grams", 0)) + saved, 1)
    totals["runs"] = int(totals.get("runs", 0)) + 1
    totals.setdefault("first_run", date_str)
    totals["last_run"] = date_str

    history = [dict(h) for h in (data.get("history") or [])]
    if history and history[-1].get("date") == date_str:
        last = history[-1]
        last["saved_g"] = round(float(last.get("saved_g", 0)) + saved, 1)
        last["runs"] = int(last.get("runs", 0)) + 1
    else:
        history.append({"date": date_str, "saved_g": round(saved, 1), "runs": 1})

    return {
        "schemaVersion": 1,
        "totals": totals,
        "history": history[-HISTORY_CAP:],
    }


def format_total(grams):
    """Format a gram total as a compact human string ("4.2 kg" / "850 g")."""
    if grams >= 1000:
        return f"{grams / 1000:.1f} kg"
    return f"{grams:.0f} g"


def badge_payload(data):
    """Build a shields.io endpoint-badge payload from the ledger totals."""
    totals = data.get("totals") or {}
    grams = float(totals.get("co2_saved_grams", 0))
    runs = int(totals.get("runs", 0))
    return {
        "schemaVersion": 1,
        "label": "CO2 saved",
        "message": f"{format_total(grams)} over {runs} builds",
        "color": "brightgreen" if grams > 0 else "lightgrey",
    }


def parse_config(config):
    """Split a ``ledger`` config string into (backend, location).

    Returns (None, None) when the ledger is disabled or the prefix is unknown.
    """
    if not config:
        return None, None
    config = config.strip()
    for prefix in ("gist:", "file:"):
        if config.startswith(prefix):
            return prefix[:-1], config[len(prefix) :].strip()
    return None, None


def _load_file(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return empty_ledger()


def _save_file(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _gist_headers(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def _gist_read(gist_id, token):
    """Return (ledger_data, owner_login). Falls back to an empty ledger."""
    resp = base.request(f"{GIST_API}/{gist_id}", headers=_gist_headers(token), parse="json")
    if not resp:
        return empty_ledger(), None
    owner = (resp.get("owner") or {}).get("login")
    entry = (resp.get("files") or {}).get(LEDGER_FILENAME)
    if not entry or not entry.get("content"):
        return empty_ledger(), owner
    try:
        return json.loads(entry["content"]), owner
    except ValueError:
        return empty_ledger(), owner


def _gist_write(gist_id, token, data):
    body = {
        "files": {
            LEDGER_FILENAME: {"content": json.dumps(data, indent=2)},
            BADGE_FILENAME: {"content": json.dumps(badge_payload(data), indent=2)},
        }
    }
    return base.request(
        f"{GIST_API}/{gist_id}",
        method="PATCH",
        headers=_gist_headers(token),
        json_body=body,
        parse="json",
    )


def _summary(data, badge_url):
    totals = data.get("totals") or {}
    grams = float(totals.get("co2_saved_grams", 0))
    runs = int(totals.get("runs", 0))
    return {
        "total_grams": grams,
        "total_runs": runs,
        "message": f"{format_total(grams)} over {runs} builds",
        "badge_url": badge_url,
    }


def record_savings(config, token, saved_grams, date_str):
    """Append this run's savings to the configured ledger and return a summary.

    Returns a dict with total_grams, total_runs, message, and badge_url (gist
    only), or None when the ledger is disabled or an IO/auth step fails. Never
    raises: failures degrade to a warning so CI is never broken by bookkeeping.
    """
    backend, location = parse_config(config)
    if not backend or not location:
        return None

    if backend == "file":
        data = merge_entry(_load_file(location), saved_grams, date_str)
        try:
            _save_file(location, data)
        except OSError as exc:
            print(f"::warning::Could not write ledger file {location}: {exc}")
            return None
        return _summary(data, None)

    # gist backend
    if not token:
        print(
            "::warning::ledger gist backend needs a gist-scoped token "
            "(gist_token input); skipping ledger update"
        )
        return None
    current, owner = _gist_read(location, token)
    data = merge_entry(current, saved_grams, date_str)
    if _gist_write(location, token, data) is None:
        print("::warning::Could not update ledger gist; skipping ledger update")
        return None
    badge_url = None
    if owner:
        raw = f"https://gist.githubusercontent.com/{owner}/{location}/raw/{BADGE_FILENAME}"
        badge_url = f"https://img.shields.io/endpoint?url={raw}"
    return _summary(data, badge_url)
