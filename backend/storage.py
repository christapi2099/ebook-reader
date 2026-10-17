"""Filesystem locations that deployments override through the environment."""
import os
from pathlib import Path

# RENDERS_DIR follows the DB_PATH pattern: an env var lets Docker mount a volume,
# and the default sits next to the backend code.
RENDERS_DIR = Path(os.environ.get("RENDERS_DIR") or Path(__file__).parent / "renders")
