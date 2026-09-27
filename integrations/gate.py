"""Shared carbon gate for the framework integrations.

One tested primitive that every adapter (Lightning, Airflow, Hugging Face,
Prefect, ...) reuses: check whether the grid is clean right now, or block
until it is.
"""

import time

import check_grid


def grid_is_clean(zones="auto:green", max_carbon=200.0, eia="", emaps="", entsoe=""):
    """True if any of the zones is at or below max_carbon right now."""
    zones_cfg = check_grid.parse_zones_input(zones)
    best, *_ = check_grid.check_multiple_zones(zones_cfg, max_carbon, eia, emaps, entsoe)
    return best is not None


def wait_until_clean(
    zones="auto:green",
    max_carbon=200.0,
    max_wait_s=6 * 3600,
    poll_s=900,
    sleep=time.sleep,
    is_clean=None,
    tokens=None,
):
    """Block until the grid is clean or max_wait_s elapses. Returns True if clean.

    is_clean is injectable for testing. By default it calls grid_is_clean.
    """
    tokens = tokens or {}
    check = is_clean or (lambda: grid_is_clean(zones, max_carbon, **tokens))
    waited = 0
    while True:
        if check():
            return True
        if waited >= max_wait_s:
            return False
        nap = min(poll_s, max_wait_s - waited)
        sleep(nap)
        waited += nap


def carbon_gate(zones="auto:green", max_carbon=200.0, max_wait_s=6 * 3600, poll_s=900, tokens=None):
    """Block until the grid is clean (or the max wait elapses). Returns True if clean.

    The same wait without the test hooks, so Prefect and Dagster can wrap it as a
    task or op whose parameters are all user-facing.
    """
    return wait_until_clean(zones, max_carbon, max_wait_s, poll_s, tokens=tokens)
