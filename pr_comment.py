"""Sticky pull-request comment.

Posts (or updates in place) a single comment on the PR with the carbon verdict,
so reviewers see whether the build ran on clean energy without opening the
Actions tab. The comment carries a hidden marker, so repeated runs edit the one
comment instead of spamming the thread. Opt-in via the ``pr_comment`` input;
silently skips when not on a PR, when disabled, or when the token lacks
``pull-requests: write``. Never raises: posting a status must not break CI.
"""

import json

from providers import base

MARKER = "<!-- carbon-aware-dispatcher -->"
API = "https://api.github.com"
PROJECT_URL = "https://github.com/peterklingelhofer/carbon-aware-dispatcher"


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def _format_grams(grams):
    return f"{grams / 1000:.1f} kg" if grams > 1000 else f"{grams:.0f} g"


def build_comment(
    *,
    is_green,
    zone,
    intensity,
    max_carbon,
    co2_saved=0,
    equivalent="",
    lifetime="",
    dry_run=False,
):
    """Build the sticky comment markdown body (pure)."""
    if dry_run:
        headline = (
            "Report-only: grid is clean"
            if is_green
            else "Report-only: grid is dirty (build ran anyway)"
        )
    elif is_green:
        headline = "Built on clean energy"
    else:
        headline = "Grid is dirty; build deferred until clean"

    lines = [MARKER, "", f"### {headline}", "", "| | |", "|---|---|", f"| Zone | `{zone}` |"]
    if intensity is not None:
        lines.append(f"| Carbon intensity | {intensity} gCO2eq/kWh |")
    lines.append(f"| Threshold | {max_carbon} gCO2eq/kWh |")
    if co2_saved and co2_saved > 0:
        extra = f" ({equivalent})" if equivalent else ""
        lines.append(f"| CO2 saved this run | {_format_grams(co2_saved)}{extra} |")
    if lifetime:
        lines.append(f"| Lifetime CO2 saved | {lifetime} |")
    lines.append("")
    lines.append(f"<sub>via [carbon-aware-dispatcher]({PROJECT_URL})</sub>")
    return "\n".join(lines)


def pr_number_from_event(event_path):
    """Read the PR number from the GitHub event payload, or None."""
    try:
        with open(event_path) as f:
            event = json.load(f)
    except (OSError, ValueError):
        return None
    number = (event.get("pull_request") or {}).get("number")
    if number is None:
        # issue_comment and similar events nest the number under "issue"
        number = (event.get("issue") or {}).get("number")
    return number


def _find_existing(repo, pr_number, token):
    url = f"{API}/repos/{repo}/issues/{pr_number}/comments?per_page=100"
    comments = base.request(url, headers=_headers(token), parse="json")
    if not comments:
        return None
    for c in comments:
        if MARKER in (c.get("body") or ""):
            return c.get("id")
    return None


def post_comment(repo, token, event_name, event_path, body):
    """Post or update the sticky PR comment. Returns True on success."""
    if event_name not in ("pull_request", "pull_request_target"):
        print(f"Event '{event_name}' is not a pull request; skipping PR comment.")
        return False
    if not token:
        print("::warning::pr_comment needs a github_token with pull-requests:write; skipping")
        return False
    if not repo:
        return False
    pr_number = pr_number_from_event(event_path) if event_path else None
    if not pr_number:
        print("::warning::Could not determine PR number from event; skipping PR comment")
        return False

    existing = _find_existing(repo, pr_number, token)
    if existing:
        url = f"{API}/repos/{repo}/issues/comments/{existing}"
        result = base.request(
            url, method="PATCH", headers=_headers(token), json_body={"body": body}, parse="json"
        )
    else:
        url = f"{API}/repos/{repo}/issues/{pr_number}/comments"
        result = base.request(
            url, method="POST", headers=_headers(token), json_body={"body": body}, parse="json"
        )
    if result is None:
        print("::warning::Failed to post PR comment")
        return False
    print(f"Posted carbon verdict to PR #{pr_number}.")
    return True
