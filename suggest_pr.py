"""Open a pull request that shifts a workflow's schedule to the grid's cleanest hour.

The highest-leverage action this tool can take: turn a recommendation into a
one-click change. Given a target workflow file, it moves the hour of any simple
daily ``cron:`` to the cleanest hour and opens a PR. Cadence and minute are
preserved; non-daily or complex schedules are left untouched (and reported), so
it never silently changes how often a job runs. Best-effort and never raises: any
API failure degrades to a warning.
"""

import base64
import re

from providers import base

API = "https://api.github.com"
BRANCH = "carbon-aware/cron"
MARKER = "<!-- carbon-aware-dispatcher:suggest -->"
CRON_RE = re.compile(r"cron:\s*['\"]([^'\"]+)['\"]")


def swap_cron_hour(cron_expr, new_hour):
    """Move a simple daily cron to new_hour, preserving the minute.

    Returns the rewritten expression, or None when it is not a fixed single-hour
    schedule (so we never change a job's cadence behind the user's back).
    """
    fields = cron_expr.split()
    if len(fields) != 5 or not fields[1].isdigit():
        return None
    fields[1] = str(int(new_hour))
    rewritten = " ".join(fields)
    return rewritten if rewritten != cron_expr else None


def rewrite_crons(text, new_hour):
    """Rewrite simple daily cron hours in a workflow file.

    Returns (new_text, changes) where changes is a list of (old, new) pairs.
    """
    changes = []
    out = []
    for line in text.splitlines(keepends=True):
        match = CRON_RE.search(line)
        if match:
            new_cron = swap_cron_hour(match.group(1), new_hour)
            if new_cron:
                line = line.replace(match.group(1), new_cron, 1)
                changes.append((match.group(1), new_cron))
        out.append(line)
    return "".join(out), changes


def _headers(token):
    return base.github_headers(token)


def _savings_line(profile, energy_kwh, changes, new_hour):
    """A concrete savings sentence for the PR body, or '' when not computable."""
    if not profile or not energy_kwh:
        return ""
    try:
        old_hour = int(changes[0][0].split()[1])
    except (ValueError, IndexError):
        return ""
    if old_hour not in profile or new_hour not in profile:
        return ""
    per_run = max(0.0, (profile[old_hour] - profile[new_hour]) * energy_kwh)
    if per_run <= 0:
        return ""
    return (
        f"\n\nEstimated saving: **~{per_run:.0f} g CO2/run** "
        f"(~{per_run * 365 / 1000:.1f} kg/yr at daily cadence), from the historical curve."
    )


def open_cron_pr(
    repo,
    token,
    path,
    new_hour,
    base_branch="main",
    source="history",
    zone="",
    profile=None,
    energy_kwh=None,
):
    """Rewrite the target workflow's cron hour and open a PR. Returns True on success."""
    if new_hour is None:
        print("::warning::suggest mode: no cleanest-hour suggestion available; skipping")
        return False
    if not (repo and token and path):
        print("::warning::suggest mode needs github_token, a repo, and suggest_target; skipping")
        return False

    cfile = base.request(
        f"{API}/repos/{repo}/contents/{path}?ref={base_branch}",
        headers=_headers(token),
        parse="json",
    )
    if not cfile or "content" not in cfile:
        print(f"::warning::suggest mode: could not read {path}; skipping")
        return False
    try:
        text = base64.b64decode(cfile["content"]).decode()
    except (ValueError, TypeError):
        print(f"::warning::suggest mode: could not decode {path}; skipping")
        return False

    new_text, changes = rewrite_crons(text, new_hour)
    if not changes:
        print(f"suggest mode: no simple daily cron to shift in {path} (already optimal or complex)")
        return False

    head = base.request(
        f"{API}/repos/{repo}/git/ref/heads/{base_branch}", headers=_headers(token), parse="json"
    )
    if not head:
        print("::warning::suggest mode: could not read base branch; skipping")
        return False
    base_sha = (head.get("object") or {}).get("sha")

    # Create the working branch; ignore failure (it may already exist from a prior run)
    base.request(
        f"{API}/repos/{repo}/git/refs",
        method="POST",
        headers=_headers(token),
        json_body={"ref": f"refs/heads/{BRANCH}", "sha": base_sha},
        parse="json",
    )

    # Use the file's sha on the branch (equals base when freshly created)
    branch_file = base.request(
        f"{API}/repos/{repo}/contents/{path}?ref={BRANCH}", headers=_headers(token), parse="json"
    )
    file_sha = (branch_file or cfile).get("sha")

    summary = ", ".join(f"`{old}` -> `{new}`" for old, new in changes)
    put = base.request(
        f"{API}/repos/{repo}/contents/{path}",
        method="PUT",
        headers=_headers(token),
        json_body={
            "message": f"chore: shift {path} schedule to the grid's cleanest hour",
            "content": base64.b64encode(new_text.encode()).decode(),
            "sha": file_sha,
            "branch": BRANCH,
        },
        parse="json",
    )
    if not put:
        print("::warning::suggest mode: could not commit the cron change; skipping")
        return False

    body = (
        f"{MARKER}\n\n"
        f"Shift `{path}` to the grid's cleanest hour for **{zone or 'the configured zone'}** "
        f"(via {source}).\n\n{summary}"
        f"{_savings_line(profile, energy_kwh, changes, new_hour)}\n\n"
        "Cadence and minute are preserved; only the hour moves. "
        "Merging runs this job when the grid is typically cleanest.\n\n"
        "<sub>via carbon-aware-dispatcher</sub>"
    )
    pr = base.request(
        f"{API}/repos/{repo}/pulls",
        method="POST",
        headers=_headers(token),
        json_body={
            "title": "Shift schedule to the grid's cleanest hour",
            "head": BRANCH,
            "base": base_branch,
            "body": body,
        },
        parse="json",
    )
    if not pr:
        # A PR for this branch may already be open — not an error
        print("suggest mode: committed to branch; a PR may already be open")
        return True
    print(f"suggest mode: opened PR to shift {path} schedule.")
    return True
