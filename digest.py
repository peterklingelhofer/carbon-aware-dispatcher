"""Weekly carbon digest.

Reads the cumulative ledger and posts (or updates in place) a single GitHub
issue summarizing recent impact: builds run, CO2 saved, emissions, and budget
status, with a tiny sparkline of daily savings. Driven by the action's
``mode: digest`` input on a schedule. Like the rest of the action it never
raises: any failure degrades to a warning so a digest run can't break CI.
"""

from datetime import datetime, timedelta, timezone

import ledger
from providers import base

MARKER = "<!-- carbon-aware-dispatcher:digest -->"
API = "https://api.github.com"
PROJECT_URL = "https://github.com/peterklingelhofer/carbon-aware-dispatcher"
ISSUE_TITLE = "Carbon-Aware Dispatcher: impact digest"
SPARK = "▁▂▃▄▅▆▇█"


def _headers(token):
    return base.github_headers(token)


def _recent_days(today, days):
    """Return YYYY-MM-DD strings for the last `days` days ending at today."""
    return [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]


def summarize_period(data, days, today):
    """Summarize the last `days` days of the ledger (pure).

    Returns a dict with saved_g, emitted_g, runs, and a per-day saved series
    aligned oldest-to-newest.
    """
    wanted = _recent_days(today, days)
    by_day = {h.get("date"): h for h in (data.get("history") or [])}
    series = []
    saved = emitted = runs = 0.0
    for day in wanted:
        entry = by_day.get(day)
        day_saved = float(entry.get("saved_g", 0)) if entry else 0.0
        series.append(day_saved)
        saved += day_saved
        if entry:
            emitted += float(entry.get("emitted_g", 0))
            runs += int(entry.get("runs", 0))
    return {
        "saved_g": round(saved, 1),
        "emitted_g": round(emitted, 1),
        "runs": int(runs),
        "series": series,
    }


def sparkline(series):
    """Render a numeric series as a unicode sparkline."""
    if not series or max(series) <= 0:
        return SPARK[0] * len(series)
    hi = max(series)
    out = []
    for v in series:
        idx = int(v / hi * (len(SPARK) - 1)) if hi else 0
        out.append(SPARK[idx])
    return "".join(out)


def render_issue_body(week, month, lifetime_msg, budget, today):
    """Build the digest issue markdown (pure)."""
    lines = [
        MARKER,
        "",
        f"### Carbon impact digest: {today.strftime('%Y-%m-%d')}",
        "",
        "| Window | Builds | CO2 saved | CO2 emitted |",
        "|---|---|---|---|",
        f"| Last 7 days | {week['runs']} | {ledger.format_total(week['saved_g'])} "
        f"| {ledger.format_total(week['emitted_g'])} |",
        f"| Last 30 days | {month['runs']} | {ledger.format_total(month['saved_g'])} "
        f"| {ledger.format_total(month['emitted_g'])} |",
        "",
        f"Daily savings (7d): `{sparkline(week['series'])}`",
    ]
    if lifetime_msg:
        lines += ["", f"**Lifetime:** {lifetime_msg}"]
    if budget:
        lines += [
            "",
            f"**Carbon budget:** {budget.get('used_pct', 0):.0f}% used "
            f"({budget.get('state', '')}), {ledger.format_total(budget.get('remaining', 0))} left",
        ]
    lines += ["", f"<sub>via [carbon-aware-dispatcher]({PROJECT_URL})</sub>"]
    return "\n".join(lines)


def _find_existing_issue(repo, token):
    url = f"{API}/repos/{repo}/issues?state=open&per_page=100"
    issues = base.request(url, headers=_headers(token), parse="json")
    if not issues:
        return None
    for issue in issues:
        if MARKER in (issue.get("body") or ""):
            return issue.get("number")
    return None


def post_issue(repo, token, title, body):
    """Create or update the sticky digest issue. Returns True on success."""
    if not token or not repo:
        print("::warning::digest needs github_token and a repository; skipping")
        return False
    existing = _find_existing_issue(repo, token)
    if existing:
        url = f"{API}/repos/{repo}/issues/{existing}"
        result = base.request(
            url, method="PATCH", headers=_headers(token), json_body={"body": body}, parse="json"
        )
    else:
        url = f"{API}/repos/{repo}/issues"
        result = base.request(
            url,
            method="POST",
            headers=_headers(token),
            json_body={"title": title, "body": body},
            parse="json",
        )
    if result is None:
        print("::warning::Failed to post carbon digest issue")
        return False
    print(f"Posted carbon digest to {repo}.")
    return True


def _load_ledger_data(config, gist_token):
    """Read the ledger contents for digesting (gist or file), or None."""
    backend, location = ledger.parse_config(config)
    if not backend or not location:
        print("::warning::digest mode needs the ledger input; nothing to summarize")
        return None
    if backend == "file":
        return ledger._load_file(location)
    data, _ = ledger._gist_read(location, gist_token)
    return data


def run(env):
    """Entry point for digest mode. env is a mapping (os.environ)."""
    data = _load_ledger_data(env.get("LEDGER", ""), env.get("GIST_TOKEN", ""))
    if data is None:
        return False

    today = datetime.now(timezone.utc).date()
    week = summarize_period(data, 7, today)
    month = summarize_period(data, 30, today)

    totals = data.get("totals") or {}
    lifetime_grams = float(totals.get("co2_saved_grams", 0))
    runs = int(totals.get("runs", 0))
    lifetime_msg = f"{ledger.format_total(lifetime_grams)} over {runs} builds" if runs else ""

    budget = _budget_status(env, data, today)
    body = render_issue_body(week, month, lifetime_msg, budget, today)
    return post_issue(env.get("TARGET_REPO", ""), env.get("GITHUB_TOKEN", ""), ISSUE_TITLE, body)


def _budget_status(env, data, today):
    """Compute budget status for the digest, or None when no budget is set."""
    raw = env.get("MONTHLY_BUDGET_GRAMS", "")
    try:
        budget = float(raw) if raw else 0.0
    except ValueError:
        return None
    if budget <= 0:
        return None
    mtd = ledger.month_to_date_emitted(data, today.strftime("%Y-%m"))
    used_pct = round(mtd / budget * 100, 1)
    return {
        "used_pct": used_pct,
        "remaining": round(max(0.0, budget - mtd), 1),
        "state": "exceeded" if mtd >= budget else ("warning" if used_pct >= 80 else "ok"),
    }
