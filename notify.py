"""Outbound webhook notifications for carbon events.

Posts a short message to a Slack, Discord, or generic JSON webhook when an
actionable carbon event happens: the grid goes clean and a build dispatches, a
build is deferred, or the monthly carbon budget is blown. Opt-in via the
notify_webhook input; a no-op otherwise. Never raises: a failed notification
degrades to a warning so it cannot break CI.

The payload shape is auto-detected from the URL:
  - hooks.slack.com        -> {"text": ...}
  - discord.com / discordapp.com -> {"content": ...}
  - anything else          -> a structured JSON event
"""

from providers import base

# Events the caller can subscribe to via notify_on (comma-separated).
VALID_EVENTS = {"green", "dirty", "exceeded", "always"}


def parse_events(raw):
    """Parse the notify_on config into a set of event names.

    Defaults to {"green", "exceeded"} (the actionable events) when empty.
    Unknown tokens are ignored.
    """
    if not raw:
        return {"green", "exceeded"}
    events = {e.strip().lower() for e in raw.split(",") if e.strip()}
    return {e for e in events if e in VALID_EVENTS} or {"green", "exceeded"}


def should_notify(events, is_green, budget_exceeded):
    """Decide whether the current run matches any subscribed event."""
    if "always" in events:
        return True
    if budget_exceeded and "exceeded" in events:
        return True
    if is_green and "green" in events:
        return True
    if (not is_green) and "dirty" in events:
        return True
    return False


def build_message(zone, intensity, is_green, tier, budget, dry_run=False):
    """Build the human-readable notification text (pure)."""
    if dry_run:
        verdict = "would dispatch (grid clean)" if is_green else "would defer (grid dirty)"
    else:
        verdict = "grid clean, dispatching" if is_green else "grid dirty, deferring"
    parts = [f"Carbon-Aware Dispatcher: {verdict}"]
    parts.append(f"zone {zone}")
    if intensity is not None:
        parts.append(f"{intensity} gCO2eq/kWh")
    if tier and tier != "unknown":
        parts.append(f"tier {tier}")
    if budget and budget.get("exceeded"):
        parts.append(f"carbon budget EXCEEDED ({budget.get('used_pct', 0):.0f}% used)")
    return " | ".join(parts)


def format_payload(url, text):
    """Shape the payload for the destination implied by the webhook URL."""
    host = url.lower()
    if "hooks.slack.com" in host:
        return {"text": text}
    if "discord.com" in host or "discordapp.com" in host:
        return {"content": text}
    return {"event": "carbon-aware-dispatcher", "message": text}


def send(url, zone, intensity, is_green, tier, budget, dry_run=False):
    """Post the notification. Returns True on success, False otherwise.

    Never raises: any failure prints a warning and returns False.
    """
    if not url:
        return False
    text = build_message(zone, intensity, is_green, tier, budget, dry_run)
    payload = format_payload(url, text)
    resp = base.request(url, method="POST", json_body=payload, parse="text")
    if resp is None:
        print("::warning::Carbon notification webhook failed; continuing")
        return False
    return True
