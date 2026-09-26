"""Tests for the suite's own hermeticity guarantees.

Why a test file for the test harness
------------------------------------
Every guard in ``conftest.py`` is an *assertion about the suite*: no test may
reach the network, read ``backend/.env``, download a spaCy model, or have its
verdict changed by whether ``ffmpeg`` is on PATH. A guard nobody tests is a
comment, and these are the guards whose silent removal would be hardest to
notice -- the suite would simply start going green for the wrong reason, or red
on somebody else's machine.

Each test below fails if its guard is deleted. They are deliberately blunt: they
assert the *effect* (a connection is refused, a variable is not set), not the
presence of a fixture name.

Measured, before these guards existed, on this checkout:

* ``import main`` copied the developer's ``backend/.env`` into ``os.environ`` --
  ``KOKORO_BACKEND=local``, a live ``MODAL_KOKORO_HEALTH_URL`` and the real
  ``MODAL_TOKEN_ID``/``MODAL_TOKEN_SECRET``.
* ``GET /api/system/capabilities`` (exercised by
  ``test_engine_manager.py::test_capabilities_still_works``) then opened a real
  TLS connection to Modal -- observed connecting to ``44.217.9.182:443`` and
  reporting ``reachable: True`` because the deployment is live. The same commit
  in a container with no network reported ``reachable: False``.
"""
import os
import socket
import sys
from pathlib import Path

import pytest

from services import export_encoding

OUT_OF_BAND_ADDRESS = ("1.1.1.1", 443)  # never contacted: the guard refuses first


class TestOutboundConnectionsAreRefused:
    def test_a_non_loopback_connect_is_refused(self):
        with pytest.raises(RuntimeError, match="must not reach the network"):
            socket.create_connection(OUT_OF_BAND_ADDRESS, timeout=1)

    def test_connect_ex_is_refused_too(self):
        """``connect_ex`` returns errno instead of raising, so it is a second door."""
        with pytest.raises(RuntimeError, match="must not reach the network"):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect_ex(OUT_OF_BAND_ADDRESS)

    def test_loopback_is_still_allowed(self):
        """The guard is narrow: only non-loopback destinations are refused."""
        closed_port = socket.socket()
        closed_port.bind(("127.0.0.1", 0))
        port = closed_port.getsockname()[1]
        closed_port.close()

        with pytest.raises(OSError) as excinfo:
            socket.create_connection(("127.0.0.1", port), timeout=1)
        assert "must not reach the network" not in str(excinfo.value), (
            "a loopback connection must be attempted, not refused by the guard"
        )


class TestDeveloperEnvironmentDoesNotLeak:
    def test_load_dotenv_is_a_no_op(self, tmp_path):
        """``backend/.env`` must not be able to reach the process environment."""
        import dotenv

        env_file = tmp_path / ".env"
        env_file.write_text("DSH_HERMETICITY_CANARY=leaked\n", encoding="utf-8")

        dotenv.load_dotenv(env_file, override=True)

        assert "DSH_HERMETICITY_CANARY" not in os.environ, (
            "conftest._block_dotenv_file() is gone: a .env file can now set the "
            "environment the tests run in"
        )

    def test_no_dotenv_key_entered_the_environment_during_the_session(self):
        """The real file, not a canary: ``main`` loads this one on import.

        Compared against the names present when ``conftest`` was imported, so an
        operator who deliberately exported one of them is not punished for it --
        only a variable that *appeared* mid-session is a leak.
        """
        from tests.conftest import ENV_KEYS_AT_IMPORT

        dotenv_path = Path(__file__).resolve().parent.parent / ".env"
        if not dotenv_path.exists():  # pragma: no cover - depends on the checkout
            pytest.skip("this checkout has no backend/.env")
        keys = {
            line.split("=", 1)[0].strip()
            for line in dotenv_path.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.strip().startswith("#")
        }
        assert keys, "backend/.env exists but declares nothing to check"

        leaked = sorted((keys - ENV_KEYS_AT_IMPORT) & set(os.environ))
        assert not leaked, (
            f"{leaked} come from backend/.env and reached the tests' environment "
            "after conftest was imported"
        )


class TestOptionalToolsCannotChangeAVerdict:
    def test_ffmpeg_is_pinned_off_by_default(self):
        """The real probe is available, but only through the ``real_ffmpeg`` fixture."""
        assert export_encoding.ffmpeg_available() is False, (
            "conftest._hermetic_ffmpeg is gone: an export's verdict now depends on "
            "whether ffmpeg is on PATH"
        )

    def test_a_chapter_format_cannot_be_reached_by_accident(self, tmp_path):
        import numpy as np

        with pytest.raises(export_encoding.EncodingError, match="ffmpeg not found"):
            export_encoding.encode(
                np.zeros(2400, dtype=np.float32), tmp_path / "x.mp3", fmt="mp3",
                bitrate_kbps=64,
            )

    def test_the_spacy_download_fallback_is_forbidden(self):
        """A missing model must fail, not shell out to a network download."""
        import services.base_engine as base_engine

        assert hasattr(base_engine, "subprocess"), (
            "conftest._no_spacy_download is gone: BaseEngine() can now download "
            "en_core_web_sm over the network"
        )
        with pytest.raises(Exception, match="will not download it"):
            base_engine.subprocess.run([sys.executable, "-m", "spacy", "download"])


class TestTheRealProbeIsStillReachableWhenAsked:
    """The pins must be opt-out-able, or the tests that need the tool would lie."""

    def test_the_optin_fixture_restores_the_genuine_probe(self, real_ffmpeg):
        from tests.conftest import REAL_FFMPEG_AVAILABLE

        assert export_encoding.ffmpeg_available is REAL_FFMPEG_AVAILABLE, (
            "the `real_ffmpeg` fixture did not restore the real probe"
        )
