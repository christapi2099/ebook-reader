"""``backend/.env`` has to be loaded before anything reads the environment.

``db.database``, ``services.modal_remote`` and ``services.engine_manager`` read
some variables once, at import time. ``main.py`` used to import them first and
load ``.env`` afterwards, so ``.env`` values for those variables were silently
ignored while ``.env.example`` documented them as settable.

The check runs in a subprocess because the suite's own ``conftest`` neuters
``dotenv.load_dotenv`` and has usually imported ``main`` already. The child
replaces ``load_dotenv`` with a recorder, so the developer's real ``.env`` is
never read, and reports which modules were already imported when it was called.
"""

import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

_PROBE = """
import json, sys
from pathlib import Path

import dotenv

seen = {}

def record(*args, **kwargs):
    seen["loaded"] = sorted(m for m in sys.modules if m in WATCHED)
    return False

WATCHED = set(json.loads(sys.argv[1]))
dotenv.load_dotenv = record
import env_file
env_file.ENV_PATH = Path(sys.argv[2])  # an empty stand-in, never the real file
import main  # noqa: F401
print(json.dumps(seen))
"""

#: Modules that read the environment at import time.
_IMPORT_TIME_READERS = ["db.database", "services.modal_remote", "services.engine_manager"]


def test_env_file_is_loaded_before_import_time_readers(tmp_path):
    fake_env = tmp_path / ".env"
    fake_env.write_text("", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-c", _PROBE, json.dumps(_IMPORT_TIME_READERS), str(fake_env)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr

    seen = json.loads(result.stdout.strip().splitlines()[-1])
    assert "loaded" in seen, "importing main never called load_dotenv"
    assert seen["loaded"] == [], (
        f"{seen['loaded']} were imported before backend/.env was loaded, so .env "
        "cannot set the variables they read at import time"
    )
