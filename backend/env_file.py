"""Load ``backend/.env`` into the process environment.

This lives in its own module so ``main.py`` can call it *before* importing
anything else. Several modules read the environment once, at import time —
``db.database`` (``DB_PATH``), ``services.modal_remote``
(``MODAL_KOKORO_IDLE_SECONDS``) and ``services.engine_manager``
(``KOKORO_WARMUP_WATCH_SECONDS``) — so a load that runs after those imports
reaches only the variables read lazily, and silently ignores the rest of the
file. ``tests/test_env_file.py`` pins that ordering.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_PATH = Path(__file__).with_name(".env")


def load_env_file() -> None:
    """Load ``backend/.env`` if python-dotenv is installed and the file is there.

    Real environment variables win over the file (``override=False``), and a
    missing file or a missing python-dotenv is not an error: the app is meant to
    run on nothing but its defaults.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.debug("python-dotenv not installed; using the process environment only")
        return
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)
        logger.info("[config] loaded %s", ENV_PATH.name)
    else:
        logger.info("[config] no %s found; using the process environment only", ENV_PATH.name)
