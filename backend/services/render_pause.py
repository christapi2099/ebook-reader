"""A user-set pause for background rendering, so reading gets the whole machine.

The flag is a small file in the renders folder: it survives a restart, needs no
migration, and is read on every check, so a pause takes effect without a restart.
"""
from __future__ import annotations

import storage

PAUSE_FILE = ".render-paused"


def _path():
    return storage.RENDERS_DIR / PAUSE_FILE


def is_paused() -> bool:
    try:
        return _path().is_file()
    except OSError:
        return False


def set_paused(paused: bool) -> bool:
    """Set the flag and return the new state."""
    path = _path()
    if paused:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    else:
        path.unlink(missing_ok=True)
    return paused
