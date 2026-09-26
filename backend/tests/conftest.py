"""Shared fixtures and isolation guards for the backend test suite.

Why this file is defensive
--------------------------
The suite used to be able to reach the developer's real data:

* ``db.database.engine`` is a module global that several tests assigned to and
  only some of them restored, so test *N* could be running against an engine a
  previous test built.
* ``TestClient(app)`` runs the app lifespan, which called the real
  ``create_engine_and_tables()`` (opening the 800 MB ``backend/ebook_reader.db``
  and running ALTER TABLE migrations against it) and ``_init_kokoro()``, which
  downloads and loads the 82M-parameter Kokoro model.
* Routers resolve ``uploads/``, ``voices/`` and ``exports/`` relative to the
  process working directory, so uploading in a test wrote real files into the
  repository.

Everything below exists so that a test cannot do any of that. The guards are
autouse, so they cannot be forgotten.

Layout of this file
-------------------
1. Constants and path helpers.
2. Working-directory sandbox (runs at import time, before test modules).
3. Autouse isolation guard.
4. Fake Kokoro pipelines and document engines.
5. Database and API client fixtures.
6. WebSocket fixtures and the message collector.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator
from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from services import export_encoding
from services.base_engine import SentenceRecord

# --------------------------------------------------------------------------- #
# 1. Constants
# --------------------------------------------------------------------------- #

SAMPLE_RATE = 24000
CHUNK_SAMPLES = SAMPLE_RATE // 10  # tts_engine streams 100 ms of audio per chunk
DEFAULT_FAKE_SAMPLES = 2400  # 100 ms of audio at speed 1.0
DEFAULT_TEST_BOOK = "test-book"

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
EBOOKS_DIR = Path.home() / "Documents" / "EBooks"

# Working-directory-relative directories owned by the routers.
RUNTIME_DIRS = (
    ("routers.documents", "UPLOAD_DIR", "uploads"),
    ("routers.voices", "VOICES_DIR", "voices"),
    ("routers.mp3", "EXPORTS_DIR", "exports"),
)
KOKORO_HOLDERS = ("routers.tts", "routers.voices", "routers.mp3")


# --------------------------------------------------------------------------- #
# 2. Working-directory sandbox
# --------------------------------------------------------------------------- #
# ``main`` mounts ``StaticFiles(directory="uploads")`` at *import* time, so an
# ``uploads`` directory has to exist in the working directory before any test
# module is imported. Redirecting the working directory (instead of requiring
# every invocation to ``cd backend``) also means a test that writes through a
# relative path cannot land in the repository, whatever it does.

def _make_working_sandbox() -> Path:
    """Return a writable scratch directory holding empty runtime dirs."""
    candidates: list[Path] = []
    try:
        candidates.append(Path(tempfile.mkdtemp(prefix="ebook-reader-tests-")))
    except OSError:  # pragma: no cover - platform dependent
        pass
    candidates.append(BACKEND_DIR / ".test-sandbox")

    for candidate in candidates:
        try:
            probe = candidate / ".writable"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
        except OSError:  # pragma: no cover - platform dependent
            continue
        for name in ("uploads", "voices", "exports"):
            (candidate / name).mkdir(exist_ok=True)
        return candidate
    raise RuntimeError("no writable directory available for the test sandbox")


_SANDBOX = _make_working_sandbox()
_ORIGINAL_CWD = Path.cwd()
os.chdir(_SANDBOX)


def _block_dotenv_file() -> None:
    """Keep ``backend/.env`` out of the suite's environment.

    ``main.py`` calls ``_load_env_file()`` at *import* time, and that file is the
    developer's own configuration. Importing ``main`` -- which half the suite
    does at collection -- therefore used to copy its contents into ``os.environ``
    for the whole process: measured here, ``KOKORO_BACKEND=local``, a live
    ``MODAL_KOKORO_HEALTH_URL`` and the real ``MODAL_TOKEN_ID`` /
    ``MODAL_TOKEN_SECRET`` all appeared the moment ``import main`` ran.

    Two things then depended on the developer's machine rather than on the
    commit. Tests that read ``os.environ`` (``RemoteConfig.from_env(os.environ)``
    in the capabilities payload, ``probe_remote``) saw Modal "configured" on a
    developer box and "not configured" in a container. And module-level constants
    such as ``engine_manager.WARMUP_WATCH_SECONDS`` and
    ``modal_remote.WARM_MODEL_WINDOW`` are read once, at import, so which value
    won depended on *import order* -- whether ``services.engine_manager`` or
    ``main`` was imported first.

    Neutering ``dotenv.load_dotenv`` rather than deleting specific keys is
    deliberate: it removes the file as an input entirely instead of guessing
    which of its keys matter today. Real environment variables exported by the
    operator still apply, because those are a deliberate choice rather than a
    file the test suite happened to find.
    """
    try:
        import dotenv
    except ImportError:  # pragma: no cover - python-dotenv is a project dependency
        return
    dotenv.load_dotenv = lambda *args, **kwargs: False


_block_dotenv_file()

#: Every name present before any test module was imported. A variable that
#: appears *after* this point was introduced by an import side effect (the
#: ``load_dotenv`` above is the one that used to do it), not by the operator's
#: deliberate configuration. ``tests/test_suite_hermeticity.py`` asserts against
#: it; comparing against ``os.environ`` wholesale would instead fail whenever
#: somebody exported a variable on purpose.
ENV_KEYS_AT_IMPORT = frozenset(os.environ)


@pytest.fixture(scope="session", autouse=True)
def _workspace_sandbox() -> Iterator[Path]:
    """Remove the working-directory sandbox after the last test."""
    yield _SANDBOX
    os.chdir(_ORIGINAL_CWD)
    shutil.rmtree(_SANDBOX, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 3. Blank-slate helpers and the autouse isolation guard
# --------------------------------------------------------------------------- #

def build_engine(db_url: str, **connect_args: Any):
    """Create an engine that has every ``db.models`` table.

    Importing ``db.models`` is the point: the tables are registered on
    ``SQLModel.metadata`` as an import side effect, and ``create_all`` on a
    metadata that has never seen them creates an empty database.
    """
    import db.models  # noqa: F401  (registers tables on SQLModel.metadata)

    engine = create_engine(db_url, **connect_args)
    SQLModel.metadata.create_all(engine)
    return engine


def memory_engine():
    """An in-memory SQLite engine shared by all sessions and threads."""
    return build_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def fake_kokoro_callable(
    samples_per_result: int = DEFAULT_FAKE_SAMPLES,
    *,
    speed_aware: bool = True,
) -> Callable[..., Iterator[tuple]]:
    """Build a stand-in for ``KPipeline.__call__``.

    Mirrors the real contract: called as ``kokoro(text, voice=..., speed=...)``,
    returns an iterable of ``(graphemes, phonemes, audio_ndarray)`` tuples with
    ``float32`` audio at ``SAMPLE_RATE``. When ``speed_aware`` the sample count
    scales as ``1/speed``, because the real engine produces shorter audio for
    faster speeds.
    """

    def kokoro(text: str, voice: str = "af_heart", speed: float = 1.0):
        rate = float(speed) or 1.0
        samples = int(round(samples_per_result / rate)) if speed_aware else samples_per_result
        yield (None, None, np.ones(max(1, samples), dtype=np.float32))

    return kokoro


@pytest.fixture
def fake_kokoro() -> Callable[..., Iterator[tuple]]:
    """A fake Kokoro pipeline: 100 ms of audio per sentence at speed 1.0."""
    return fake_kokoro_callable()


@pytest.fixture
def fake_kokoro_factory() -> Callable[..., Callable[..., Iterator[tuple]]]:
    """Build a fake Kokoro with a controlled audio length."""
    return fake_kokoro_callable


class _G2PToken:
    """Minimal misaki token: the engine only reads ``text`` and ``phonemes``."""

    def __init__(self, text: str, phonemes: str):
        self.text = text
        self.phonemes = phonemes


class FakeG2P:
    """Stand-in for ``misaki.en.G2P``.

    The real one constructs a spaCy pipeline, which triggers catalogue entry-point
    scanning and an import of ``spacy_curated_transformers`` and ``transformers``
    -- measured at tens of seconds on this machine, on whichever thread calls it.
    ``TTSEngine._proportional_timestamps`` calls it from the event loop, so a cold
    call freezes the whole server (and any test driving it) for that long.
    This fake keeps the code path, its weighting and its arithmetic under test
    without the dependency.
    """

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, text: str) -> tuple[str, list[_G2PToken]]:
        self.calls.append(text)
        tokens = [_G2PToken(word, "p" * len(word)) for word in text.split()]
        return " ".join(token.phonemes for token in tokens), tokens


@pytest.fixture
def fake_g2p() -> FakeG2P:
    """The fake misaki G2P used by the isolation guard."""
    return FakeG2P()


def _patch_if_imported(monkeypatch, module_name: str, attr: str, value: Any) -> None:
    """Patch ``module_name.attr`` when the module is already imported."""
    module = sys.modules.get(module_name)
    if module is not None and hasattr(module, attr):
        monkeypatch.setattr(module, attr, value, raising=False)


#: Environment the Hugging Face stack reads. Redirected into the sandbox so that
#: a code path which bypasses every patch below still cannot write to the
#: developer's (in this sandbox: read-only) ``~/.cache/huggingface``.
#: ``HF_HUB_OFFLINE`` is deliberate: an accidental real load then fails at once
#: instead of stalling on a download.
HF_ENV = (
    "HF_HOME",
    "HF_HUB_CACHE",
    "HUGGINGFACE_HUB_CACHE",
    "TRANSFORMERS_CACHE",
    "HF_HUB_OFFLINE",
    "HF_HUB_DISABLE_TELEMETRY",
    "HF_HUB_DISABLE_PROGRESS_BARS",
)

_MISSING = object()


@pytest.fixture(scope="session", autouse=True)
def _hermetic_kokoro(_workspace_sandbox: Path) -> Iterator[None]:
    """Make it impossible for any test to build, download or publish a real Kokoro.

    Session-scoped on purpose. ``isolate_from_real_resources`` can only patch
    modules that already exist, so it cannot promise anything about a module
    imported later, a module re-imported by another test, or a test that
    deliberately captures and calls the real ``main._init_kokoro`` -- which
    tests/test_system_capabilities.py does. This fixture closes all of those:

    * ``main._init_kokoro`` is replaced outright.
    * ``services.engine_manager.manager._local_builder`` -- the seam
      ``EngineManager._build`` really calls -- is replaced, so even a genuine
      ``startup()`` cannot reach ``KPipeline``.
    * the Hugging Face cache directories are pointed into the sandbox, and
      ``HF_HUB_OFFLINE`` is set.

    The last point makes this a correctness fix rather than a speed-up. This
    sandbox's ``~/.cache/huggingface/hub`` is read-only, so a real ``KPipeline``
    build failed with ``[Errno 30] Read-only file system`` and the failure was
    then *published* through ``_apply_kokoro``, leaving
    ``routers.tts._kokoro is None`` for every later test in the session. The
    visible symptom was ~27 failures in audio-dependent files that passed when
    run on their own -- tests/test_system_capabilities.py runs before them all
    and was doing exactly that.
    """
    import main
    import services.engine_manager as engine_manager

    fake = fake_kokoro_callable()
    saved_attrs: list[tuple[Any, str, Any]] = []
    saved_env = {name: os.environ.get(name) for name in HF_ENV}

    hf_home = _SANDBOX / "hf-home"
    hf_cache = _SANDBOX / "hf-cache"
    hf_home.mkdir(exist_ok=True)
    hf_cache.mkdir(exist_ok=True)

    def _replace(obj: Any, attr: str, value: Any) -> None:
        saved_attrs.append((obj, attr, getattr(obj, attr, _MISSING)))
        setattr(obj, attr, value)

    _replace(main, "_init_kokoro", lambda: fake)
    _replace(engine_manager.manager, "_local_builder", lambda device: (fake, None))

    # huggingface_hub resolves these once, at import time, so the environment
    # alone is not enough if it has already been imported.
    hub_constants = sys.modules.get("huggingface_hub.constants")
    if hub_constants is not None:
        for attr, value in (("HF_HOME", str(hf_home)), ("HF_HUB_CACHE", str(hf_cache))):
            if hasattr(hub_constants, attr):
                _replace(hub_constants, attr, value)

    os.environ.update(
        {
            "HF_HOME": str(hf_home),
            "HF_HUB_CACHE": str(hf_cache),
            "HUGGINGFACE_HUB_CACHE": str(hf_cache),
            "TRANSFORMERS_CACHE": str(hf_cache),
            "HF_HUB_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_DISABLE_PROGRESS_BARS": "1",
        }
    )

    try:
        # Fail loudly and at once if the app would still start a real engine.
        assert main._init_kokoro() is fake, "conftest could not neutralize main._init_kokoro"
        yield
    finally:
        for obj, attr, old in reversed(saved_attrs):
            if old is _MISSING:
                delattr(obj, attr)
            else:
                setattr(obj, attr, old)
        for name, old in saved_env.items():
            if old is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old


@pytest.fixture(autouse=True)
def isolate_from_real_resources(
    tmp_path: Path,
    monkeypatch,
    fake_kokoro: Callable[..., Iterator[tuple]],
    fake_g2p: FakeG2P,
) -> None:
    """Keep every test off the real database, the real Kokoro and the real dirs.

    Applied automatically to every test in the suite:

    * ``db.database.engine`` becomes a throwaway in-memory engine, so a test that
      forgets to restore the global cannot leak into the next test, and
      ``get_session()`` can never open ``backend/ebook_reader.db``.
    * ``db.database._DEFAULT_DB`` points into ``tmp_path``, so even a bare
      ``create_engine_and_tables()`` builds a scratch file.
    * Once ``main`` is imported, its lifespan hooks are replaced: startup builds
      the in-memory engine and installs the fake Kokoro instead of downloading
      the real model.
    * ``uploads/``, ``voices/`` and ``exports/`` are redirected under
      ``tmp_path``, and the routers' cached Kokoro handles are reset to the fake.
    * ``services.tts_engine._g2p`` is pre-seeded with a fake, so the first
      word-timestamp estimation cannot drag spaCy and transformers into the
      process on the event loop.
    * ``services.engine_manager.manager`` is put back to "no engine built". Its
      ``_current`` is process-global and leaky: the WebSocket handler prefers it
      over ``routers.tts._kokoro`` (``live = engine_manager.manager.current()``),
      so an engine built by an earlier test silently overrode the pipeline the
      next test installed.
    """
    import db.database as database
    import services.engine_manager as engine_manager
    from services import kokoro_runtime

    monkeypatch.setattr(database, "engine", memory_engine(), raising=False)
    monkeypatch.setattr(database, "_DEFAULT_DB", str(tmp_path / "unused.db"), raising=False)

    manager = engine_manager.manager
    for attr, value in (
        ("_current", None),
        ("_active", None),
        ("_switching", False),
        ("_phase", getattr(engine_manager, "PHASE_READY", "ready")),
    ):
        if hasattr(manager, attr):
            monkeypatch.setattr(manager, attr, value, raising=False)
    kokoro_runtime.runtime.reset()

    for module_name, attr, dirname in RUNTIME_DIRS:
        _patch_if_imported(monkeypatch, module_name, attr, tmp_path / dirname)
    for module_name in KOKORO_HOLDERS:
        _patch_if_imported(monkeypatch, module_name, "_kokoro", fake_kokoro)
    _patch_if_imported(monkeypatch, "services.tts_engine", "_g2p", fake_g2p)

    if "main" in sys.modules:
        _patch_if_imported(monkeypatch, "main", "_init_kokoro", lambda: fake_kokoro)
        _patch_if_imported(
            monkeypatch,
            "main",
            "create_engine_and_tables",
            lambda db_url=None: database.engine,
        )


# --------------------------------------------------------------------------- #
# 3b. Optional external tools and the network
# --------------------------------------------------------------------------- #
# These three guards exist for the same reason as the ones above: a test's
# verdict must not depend on the machine it happens to run on. They cover the
# three inputs the suite used to read straight from the host -- whether `ffmpeg`
# is on PATH, whether a socket can reach the internet, and whether the spaCy
# model package is installed (whose fallback downloads it).

#: The genuine probe, kept for the tests that deliberately want ffmpeg.
REAL_FFMPEG_AVAILABLE = export_encoding.ffmpeg_available


@pytest.fixture
def real_ffmpeg(monkeypatch) -> None:
    """Opt back in to the real ``ffmpeg`` availability probe.

    Request this from a test (or an autouse fixture in a class) that genuinely
    runs ffmpeg. Everything else sees ``ffmpeg_available() is False``, so the
    suite's verdict is identical on a machine with ffmpeg and one without.
    """
    monkeypatch.setattr(export_encoding, "ffmpeg_available", REAL_FFMPEG_AVAILABLE)


@pytest.fixture(autouse=True)
def _hermetic_ffmpeg(monkeypatch, request) -> None:
    """Make ``ffmpeg`` on PATH irrelevant unless a test asks for it.

    The export router gates M4B/Opus/MP3 export on ``shutil.which("ffmpeg")``, so
    without this pin a POST /mp3/export would answer 200 on the developer's
    machine and 503 on a fresh CI container: the same commit, two verdicts. The
    pin turns that into an immediate, local failure instead -- an export that
    really needs ffmpeg raises ``EncodingError`` here, which is what the one
    class that opts back in via ``real_ffmpeg`` is for.

    Tests that need a *successful* export ask for WAV, the format
    ``services.export_encoding`` writes through soundfile with no external
    process.
    """
    if "real_ffmpeg" in request.fixturenames:
        return
    monkeypatch.setattr(export_encoding, "ffmpeg_available", lambda: False)


class NetworkAccessError(RuntimeError):
    """A test tried to open a connection to something other than loopback."""


def _is_loopback(address: Any) -> bool:
    if not isinstance(address, tuple) or not address:
        return False  # AF_UNIX and friends: local by construction
    host = address[0]
    if host in ("localhost", "::1", ""):
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _no_network(monkeypatch) -> None:
    """Refuse outbound connections, so no test can pass *because* it reached one.

    ``backend/.env`` points at a live Modal deployment and ``~/.modal.toml`` may
    exist, so "the tests fake the transport" is a claim about today's test bodies
    rather than a property of the suite. This makes it a property: a connect to
    anything that is not loopback raises here, with the address in the message,
    instead of quietly making the run depend on the network being up.

    Loopback is left alone because it costs nothing to allow and something as
    mundane as a library's local helper would otherwise fail for the wrong
    reason. Unix sockets are local by construction and are allowed too.
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_connect(self, address):  # pragma: no cover - exercised only on failure
        if not _is_loopback(address):
            raise NetworkAccessError(
                f"the test suite must not reach the network; refused a connection "
                f"to {address!r}. Fake the transport instead."
            )
        return real_connect(self, address)

    def guarded_connect_ex(self, address):  # pragma: no cover - see above
        if not _is_loopback(address):
            raise NetworkAccessError(
                f"the test suite must not reach the network; refused a connection "
                f"to {address!r}. Fake the transport instead."
            )
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


@pytest.fixture(autouse=True)
def _no_spacy_download(monkeypatch) -> None:
    """Forbid the ``spacy download`` fallback in ``BaseEngine.__init__``.

    When ``en_core_web_sm`` is missing, ``services/base_engine.py`` shells out to
    ``python -m spacy download`` -- a network call with a 180 s bound, on the
    request path, from whichever test first constructs a ``BaseEngine``. A test
    that runs on a machine without the model would therefore either hang for
    three minutes or pass because the network *was* up. It now fails at once with
    the install command in the message.
    """
    base_engine = importlib.import_module("services.base_engine")

    class _NoDownload:
        """Everything ``base_engine`` uses ``subprocess`` for is the download."""

        @staticmethod
        def run(*args, **kwargs):
            raise ModuleNotFoundError(
                "spaCy model en_core_web_sm is not installed and the test suite "
                "will not download it. Install it offline with: "
                f"{sys.executable} -m spacy download en_core_web_sm"
            )

    monkeypatch.setattr(base_engine, "subprocess", _NoDownload, raising=False)


# --------------------------------------------------------------------------- #
# 4. Corpus paths (real files under ~/Documents/EBooks)
# --------------------------------------------------------------------------- #

@pytest.fixture
def sample_pdf_path() -> Path:
    """PDF used by the PDF-engine tests. Requires ~/Documents/EBooks."""
    return EBOOKS_DIR / "cleancodebook.pdf"


@pytest.fixture
def sample_epub_path() -> Path:
    """EPUB used by the EPUB-engine tests. Requires ~/Documents/EBooks."""
    return EBOOKS_DIR / "cleancodebook.epub"


@pytest.fixture
def logic_pdf_path() -> Path:
    return EBOOKS_DIR / "Logic text v 2.0.pdf"


@pytest.fixture
def hardthing_pdf_path() -> Path:
    return EBOOKS_DIR / "Ben_Horowitz_The_Hard_Thing_About_Hard_Things.pdf"


# --------------------------------------------------------------------------- #
# 5. Fakes for the document engines
# --------------------------------------------------------------------------- #

@pytest.fixture
def fake_sentences() -> list[SentenceRecord]:
    """Sentence records the mocked PDF engine reports for an upload."""
    return [
        SentenceRecord(
            index=i,
            text=f"This is sentence number {i} with enough words.",
            page=0,
            x0=10.0,
            y0=float(i * 20),
            x1=400.0,
            y1=float(i * 20 + 15),
        )
        for i in range(5)
    ]


@pytest.fixture
def mock_engines(fake_sentences: list[SentenceRecord]) -> Iterator[SimpleNamespace]:
    """Replace the PDF/EPUB engines the upload endpoint builds.

    Without this an upload test would ask a real parser to make sense of the
    bytes the test posted.
    """
    documents = sys.modules.get("routers.documents")
    if documents is None:  # pragma: no cover - documents is imported by main
        import routers.documents as documents  # type: ignore[no-redef]

    with patch.object(documents, "PDFEngine") as pdf_engine, patch.object(
        documents, "EPUBEngine"
    ) as epub_engine:
        pdf_engine.return_value.extract_sentences.return_value = fake_sentences
        pdf_engine.return_value.page_count.return_value = 10
        epub_engine.return_value.extract_sentences.return_value = []
        yield SimpleNamespace(pdf=pdf_engine, epub=epub_engine)


# --------------------------------------------------------------------------- #
# 6. Database fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def db_engine(tmp_path):
    """A fresh database with every table, isolated per test.

    **File-backed, not ``:memory:``, and that is load-bearing.** An in-memory
    SQLite database can only be shared between threads through a single
    connection (``memory_engine``'s ``StaticPool``), and this suite genuinely has
    concurrent sessions on it: FastAPI runs synchronous endpoints on a worker
    thread while the export path runs its body on another. Two sessions
    interleaving ``BEGIN``/``COMMIT``/``ROLLBACK`` on one connection is not safe,
    and it produced a rare, load-dependent corruption — a committed ``Book`` row
    was intermittently invisible to a reader (surfacing as a book title of
    "Unknown") and a status response arrived with no ``status`` key at all.

    A file gives every session its own connection, which is the case SQLite's
    locking is actually designed for. ``memory_engine`` is kept for the
    single-threaded callers that only ever use one session.
    """
    return build_engine(f"sqlite:///{tmp_path / 'test.db'}")


@pytest.fixture
def db_session(db_engine) -> Iterator[Session]:
    """A session on ``db_engine`` for arranging data before a request."""
    with Session(db_engine) as session:
        yield session


def default_sentences() -> list[dict]:
    """The three-sentence book most WebSocket tests play through."""
    return [
        {"index": 0, "text": "First."},
        {"index": 1, "text": "Second."},
        {"index": 2, "text": "Third."},
    ]


def _sentence_fields(row: dict) -> dict:
    fields = {
        "page": 1,
        "x0": 0.0,
        "y0": 0.0,
        "x1": 100.0,
        "y1": 20.0,
        "filtered": False,
    }
    fields.update(row)
    return fields


@pytest.fixture
def seed_book() -> Callable[..., Any]:
    """Factory inserting a Book and its Sentences into a given engine.

    ``seed_book(engine)`` writes the default three-sentence book; pass
    ``sentences=[{...}]`` for anything else. Each entry needs ``index`` and
    ``text`` and may override any other ``Sentence`` column (``filtered``,
    ``page``, ...). Extra keyword arguments become ``Book`` columns.
    """
    from db.models import Book, Sentence

    def _seed(engine, book_id: str = DEFAULT_TEST_BOOK, sentences=None, **book_fields):
        rows = default_sentences() if sentences is None else sentences
        fields = {
            "title": "Test Book",
            "author": "Test Author",
            "file_path": "/test/path.pdf",
            "file_type": "pdf",
            "page_count": 1,
            "cover_page": 0,
            "created_at": datetime.now(UTC),
        }
        fields.update(book_fields)
        with Session(engine) as session:
            session.add(Book(id=book_id, **fields))
            for row in rows:
                session.add(Sentence(book_id=book_id, **_sentence_fields(row)))
            session.commit()
        return engine

    return _seed


# --------------------------------------------------------------------------- #
# 7. API client fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def client(db_engine, mock_engines, fake_kokoro, monkeypatch) -> Iterator[TestClient]:
    """A TestClient for the real app, wired to ``db_engine``.

    The app's database *is* ``db_engine``: the session dependency is overridden
    and ``db.database.engine`` is repointed, so handlers that bypass the
    dependency (the TTS and MP3 routers read ``_db.engine`` at call time) see the
    same data. ``main``'s lifespan hooks are replaced too, so entering the client
    cannot bootstrap the real database or load the real Kokoro model.

    ``app.dependency_overrides`` is cleared before *and* after the test, and the
    teardown sits in a ``finally`` so a failing test cannot leave an override
    behind for the next one.
    """
    import db.database as database
    from db.database import get_session
    import main

    monkeypatch.setattr(main, "_init_kokoro", lambda: fake_kokoro)
    monkeypatch.setattr(main, "create_engine_and_tables", lambda db_url=None: db_engine)
    monkeypatch.setattr(database, "engine", db_engine, raising=False)

    def override_session():
        with Session(db_engine) as session:
            yield session

    main.app.dependency_overrides.clear()
    main.app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(main.app, raise_server_exceptions=True) as test_client:
            yield test_client
    finally:
        main.app.dependency_overrides.clear()


@pytest.fixture
def upload_book(client: TestClient) -> Callable[..., dict]:
    """Upload a file through the API and return the parsed response body."""

    def _upload(
        filename: str = "test.pdf",
        content: bytes = b"fakepdf",
        content_type: str = "application/pdf",
        expect_status: int = 200,
    ) -> dict:
        response = client.post(
            "/documents/upload",
            files={"file": (filename, content, content_type)},
        )
        assert response.status_code == expect_status, response.text
        return response.json()

    return _upload


# --------------------------------------------------------------------------- #
# 8. WebSocket fixtures
# --------------------------------------------------------------------------- #

@contextmanager
def app_client_for(engine, kokoro, monkeypatch) -> Iterator[TestClient]:
    """Enter a TestClient with ``engine`` and ``kokoro`` injected into the app.

    The injection has to happen before the client context is entered, because
    that is when the lifespan runs and would otherwise overwrite both.
    """
    import db.database as database
    import main

    monkeypatch.setattr(main, "create_engine_and_tables", lambda db_url=None: engine)
    monkeypatch.setattr(main, "_init_kokoro", lambda: kokoro)
    monkeypatch.setattr(database, "engine", engine, raising=False)

    with TestClient(main.app, raise_server_exceptions=True) as test_client:
        yield test_client


@pytest.fixture
def ws_engine(tmp_path, monkeypatch):
    """A file-backed engine that ``db.database.engine`` points at.

    File-backed rather than in-memory because the WebSocket handler opens a
    fresh session per sentence. ``monkeypatch`` guarantees the global is put back
    when the test ends -- the old suite assigned it directly and leaked it.
    """
    import db.database as database

    engine = build_engine(
        f"sqlite:///{tmp_path / 'ws.db'}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(database, "engine", engine, raising=False)
    return engine


@pytest.fixture
def ws_client_factory(ws_engine, seed_book, fake_kokoro, monkeypatch) -> Callable[..., Any]:
    """Factory returning a seeded TestClient for the TTS WebSocket.

    ``kokoro`` overrides the pipeline this client's app will use. Pass it here
    rather than patching ``routers.tts._kokoro`` afterwards: the app's lifespan
    installs its own pipeline when the client is entered, and would overwrite an
    earlier patch.
    """

    @contextmanager
    def _client(sentences=None, book_id: str = DEFAULT_TEST_BOOK, kokoro=None):
        seed_book(ws_engine, book_id=book_id, sentences=sentences)
        with app_client_for(ws_engine, kokoro or fake_kokoro, monkeypatch) as test_client:
            yield test_client

    return _client


@pytest.fixture
def ws_client(ws_client_factory) -> Iterator[TestClient]:
    """A TestClient whose ``test-book`` has three plain sentences."""
    with ws_client_factory() as test_client:
        yield test_client


def collect_ws_messages(
    ws,
    *,
    until: tuple[str, ...] = ("complete",),
    quiet: float = 1.5,
    timeout: float = 20.0,
    limit: int = 500,
    expect_silence: bool = False,
) -> list[dict]:
    """Read WebSocket messages until the socket goes quiet or ``until`` fires.

    Returns one dict per message: ``{"channel": "text" | "bytes" | "disconnect" |
    "closed", "data": ...}``. Reading happens on a daemon thread because
    ``WebSocketTestSession.receive()`` has no timeout; the old tests worked
    around that with ``except Exception: break`` blocks that also swallowed real
    failures. A text message whose ``type`` is in ``until`` ends the read
    immediately.

    Two timing rules, because they mean different things:

    * Before the *first* message arrives, wait up to ``timeout``. A cold test
      process pays a couple of seconds of lazy imports before the handler emits
      anything, and treating that as "the stream ended" truncated real streams.
    * After that, stop once ``quiet`` seconds pass with no new message.

    ``expect_silence=True`` applies the ``quiet`` rule from the start, for tests
    that assert nothing arrives (a paused stream).
    """
    stop_types = set(until)
    messages: list[dict] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set() and len(messages) < limit:
            try:
                raw = ws.receive()
            except Exception as exc:  # portal closed: the stream is over
                messages.append({"channel": "closed", "data": exc})
                return
            if raw.get("type") == "websocket.disconnect":
                messages.append({"channel": "disconnect", "data": raw})
                return
            if raw.get("bytes") is not None:
                messages.append({"channel": "bytes", "data": raw["bytes"]})
                continue
            payload = json.loads(raw["text"])
            messages.append({"channel": "text", "data": payload})
            if payload.get("type") in stop_types:
                return

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    started = time.monotonic()
    deadline = started + timeout
    quiet_deadline = started + quiet
    seen = 0
    while time.monotonic() < deadline:
        if len(messages) != seen:
            seen = len(messages)
            quiet_deadline = time.monotonic() + quiet
        elif (expect_silence or seen) and time.monotonic() >= quiet_deadline:
            break
        if not thread.is_alive():
            break
        time.sleep(0.01)

    stop.set()
    thread.join(timeout=1.0)
    return messages


@pytest.fixture
def ws_read() -> Callable[..., list[dict]]:
    """The WebSocket message collector, exposed as a fixture."""
    return collect_ws_messages
