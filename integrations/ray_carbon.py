"""Carbon-aware Ray: hold a job until the grid is clean, then run it.

Ray drives large distributed training and batch workloads — exactly the
deferrable, energy-heavy loads worth shifting onto clean windows. Gate on the
driver before submitting work: carbon_gate blocks until a target zone is clean,
and run_when_clean waits and then invokes a callable (e.g. a function that
submits Ray tasks and collects results).

No Ray import is needed here: the gate runs on the driver before any Ray task is
scheduled, so this module stays dependency-free and easy to test.
"""

from integrations.gate import grid_is_clean, wait_until_clean

__all__ = ["grid_is_clean", "wait_until_clean", "carbon_gate", "run_when_clean"]


def carbon_gate(zones="auto:green", max_carbon=200.0, max_wait_s=6 * 3600, poll_s=900, tokens=None):
    """Block until the grid is clean (or the max wait elapses). Returns True if clean."""
    return wait_until_clean(zones, max_carbon, max_wait_s, poll_s, tokens=tokens)


def run_when_clean(func, *args, zones="auto:green", max_carbon=200.0, gate=None, **kwargs):
    """Wait until the grid is clean, then call func(*args, **kwargs) and return it.

    Raises RuntimeError if no clean window opens before the gate's deadline, so a
    scheduler can retry rather than run the heavy job dirty. gate is injectable
    for testing; by default it calls carbon_gate.
    """
    check = gate or (lambda: carbon_gate(zones, max_carbon))
    if not check():
        raise RuntimeError("no clean grid window before deadline; not running")
    return func(*args, **kwargs)
