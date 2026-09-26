"""Guarded reads of numeric environment variables.

A number read from the environment at import time is a boot-time hazard. One
typo in a shell export, a systemd unit or a container's environment raises
``ValueError`` while the module is still being imported, so the process dies
before it can log anything useful — and the traceback points at a bare
``float(os.environ.get(...))`` with no hint that the *value* was the problem.
That shape appeared at seven places across three modules, so it lives here once.

The rule follows :mod:`services.audio_cache`, which grew the same helper first:
an absent variable is silent, while a present-but-useless one is logged, because
silently ignoring a deliberate-looking setting is how a limit ends up not
applying. Zero and negative count as useless rather than as literal values —
each of these variables configures a *duration or a bound*, so reading ``0``
literally would disable the very feature it configures instead of configuring
it. ``AUDIO_CACHE_MAX_MB=0`` deleting every cached row is the same argument.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Mapping

logger = logging.getLogger(__name__)


def positive_number(raw: str | None, var: str) -> float | None:
    """Parse a positive numeric env value, or ``None`` to mean "use the default"."""
    value_text = (raw or "").strip()
    if not value_text:
        return None
    try:
        value = float(value_text)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using the default", var, value_text)
        return None
    if not math.isfinite(value) or value <= 0:
        # ``isfinite`` rather than ``isnan``: ``float("1e400")`` is ``inf``, and
        # an infinite warm-up window would disable the very bound it sets.
        logger.warning("%s=%r is not a positive finite number; using the default", var, value_text)
        return None
    return value


def positive_seconds(var: str, default: float, env: Mapping[str, str] | None = None) -> float:
    """A duration in seconds from ``var``, or ``default``."""
    environment = os.environ if env is None else env
    value = positive_number(environment.get(var), var)
    return default if value is None else value


def positive_int(var: str, default: int, env: Mapping[str, str] | None = None) -> int:
    """A whole number from ``var``, or ``default``.

    Accepts ``"6.0"`` as well as ``"6"``: the environment is text, and rejecting
    a value a human would call whole would be pedantry that costs a boot.
    """
    environment = os.environ if env is None else env
    value = positive_number(environment.get(var), var)
    if value is None:
        return default
    try:
        return int(value)
    except (OverflowError, ValueError):
        logger.warning("%s=%r is out of range; using the default %d", var, value, default)
        return default
