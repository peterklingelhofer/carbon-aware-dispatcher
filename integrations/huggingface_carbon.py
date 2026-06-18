"""Carbon-aware Hugging Face training: pause/resume on grid cleanliness.

Transformers fine-tuning and pretraining are long, deferrable, energy-heavy
loads. CarbonAwareTrainerCallback gates at each epoch boundary while the grid is
dirty and resumes when it is clean, so a run consumes clean energy without
manual babysitting.

The transformers import is lazy/optional, so this module imports fine without
transformers installed (the callback subclasses object as a fallback).
"""

from integrations.gate import grid_is_clean, wait_until_clean

__all__ = ["grid_is_clean", "wait_until_clean", "CarbonAwareTrainerCallback"]


try:  # pragma: no cover - import shim depends on transformers being installed
    from transformers import TrainerCallback as _Base
except Exception:  # pragma: no cover
    _Base = object


class CarbonAwareTrainerCallback(_Base):
    """Pause Hugging Face Trainer at epoch boundaries until the grid is clean.

    Usage:
        from integrations.huggingface_carbon import CarbonAwareTrainerCallback
        trainer = Trainer(..., callbacks=[CarbonAwareTrainerCallback(zones="auto:green")])

    Tokens for paid zones go in the tokens dict (eia/emaps/entsoe).
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

    def on_epoch_begin(self, args=None, state=None, control=None, **kwargs):
        self.gate()
        return control
