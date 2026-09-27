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


def summarize_period(data, days, today):
    """Summarize the last `days` days of the ledger (pure).

    Returns a dict with saved_g, emitted_g, runs, and a per-day saved series
    aligned oldest-to-newest.
    """
    by_day = {h.get("date"): h for h in (data.get("history") or [])}
    series = []
    saved = emitted = runs = 0.0
    for i in range(days - 1, -1, -1):
        entry = by_day.get((today - timedelta(days=i)).strftime("%Y-%m-%d"))
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
    hi = max(series, default=0)
    if hi <= 0:
        return SPARK[0] * len(series)
    return "".join(SPARK[int(v / hi * (len(SPARK) - 1))] for v in series)


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


def _find_existing_issue(repo, headers):
    url = f"{API}/repos/{repo}/issues?state=open&per_page=100"
    issues = base.request(url, headers=headers, parse="json") or []
    return next((i.get("number") for i in issues if MARKER in (i.get("body") or "")), None)


def post_issue(repo, token, title, body):
    """Create or update the sticky digest issue. Returns True on success."""
    if not token or not repo:
        print("::warning::digest needs github_token and a repository, skipping")
        return False
    headers = base.github_headers(token)
    existing = _find_existing_issue(repo, headers)
    if existing:
        url, method, payload = f"{API}/repos/{repo}/issues/{existing}", "PATCH", {"body": body}
    else:
        url, method, payload = f"{API}/repos/{repo}/issues", "POST", {"title": title, "body": body}
    result = base.request(url, method=method, headers=headers, json_body=payload, parse="json")
    if result is None:
        print("::warning::Failed to post carbon digest issue")
        return False
    print(f"Posted carbon digest to {repo}.")
    return True


def run(env):
    """Entry point for digest mode. env is a mapping (os.environ)."""
    data = ledger.load(env.get("LEDGER", ""), env.get("GIST_TOKEN", ""))
    if data is None:
        print("::warning::digest mode needs the ledger input, nothing to summarize")
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
    return ledger.budget_status(ledger.month_to_date_emitted(data, today.strftime("%Y-%m")), budget)
