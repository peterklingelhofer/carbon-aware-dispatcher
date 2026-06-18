"""Carbon-aware ML training: pause/resume on grid cleanliness.

A training run lasts hours to days, so shifting it onto clean-grid windows is one
of the highest-impact real-world uses of carbon-aware scheduling (far more than
CI). This module provides a framework-agnostic gate and a PyTorch Lightning
Callback that pauses at epoch boundaries while the grid is dirty, then resumes
when it is clean again.

The Lightning import is lazy/optional, so this module imports fine without torch
installed (the callback simply subclasses object as a fallback).
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

    is_clean is injectable for testing; by default it calls grid_is_clean.
    """
    tokens = tokens or {}
    check = is_clean or (lambda: grid_is_clean(zones, max_carbon, **tokens))
    waited = 0
    while True:
        if check():
            return True
        if waited >= max_wait_s:
            return False
        nap = min(poll_s, max_wait_s - waited) or poll_s
        sleep(nap)
        waited += nap


try:  # pragma: no cover - import shim depends on which Lightning is installed
    from lightning.pytorch.callbacks import Callback as _Base
except Exception:  # pragma: no cover
    try:
        from pytorch_lightning.callbacks import Callback as _Base
    except Exception:
        _Base = object


class CarbonAwareCallback(_Base):
    """Pause training while the grid is dirty; resume when clean.

    Usage:
        from integrations.lightning_carbon import CarbonAwareCallback
        trainer = Trainer(callbacks=[CarbonAwareCallback(zones="auto:green")])

    Gates at the start of each training epoch, so a long run consumes clean
    energy without manual intervention. Tokens for paid zones may be passed via
    the tokens dict (eia/emaps/entsoe).
    """

    def __init__(
        self, zones="auto:green", max_carbon=200.0, max_wait_s=6 * 3600, poll_s=900, tokens=None
    ):
        self.zones = zones
        self.max_carbon = max_carbon
        self.max_wait_s = max_wait_s
        self.poll_s = poll_s
        self.tokens = tokens or {}

    def gate(self):
        """Block until the grid is clean (or the max wait elapses)."""
        return wait_until_clean(
            self.zones, self.max_carbon, self.max_wait_s, self.poll_s, tokens=self.tokens
        )

    def on_train_epoch_start(self, trainer=None, pl_module=None):
        self.gate()
