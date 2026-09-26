"""Shared base engine abstraction for sentence extraction."""
from dataclasses import dataclass, field
import logging
import subprocess
import sys
import threading
from typing import Any

import spacy

logger = logging.getLogger(__name__)

MODEL_NAME = "en_core_web_sm"

# The download fallback below runs on the request path, not at install time, so it
# must be bounded: an unbounded version of exactly this call is what made the test
# suite look like it had hung, and it was reaching for a bare `python` that may not
# be the interpreter running us.
MODEL_DOWNLOAD_TIMEOUT_SECONDS = 180

# The pipeline is loaded ONCE per process and shared. `BaseEngine()` is constructed
# per extraction and per test, and `spacy.load` deserialises the whole model every
# time, so this was paid on every construction — a third of `scripts/test.sh fast`,
# and the same cost in production on every book.
#
# Module-level state rather than a class attribute so a test can isolate it the
# ordinary way: `monkeypatch.setattr(base_engine, "_nlp", None)` forces a reload.
_nlp: Any = None
_nlp_lock = threading.Lock()


def _load_pipeline() -> Any:
    """Load ``en_core_web_sm``, falling back to a bounded download, or raise."""
    try:
        return spacy.load(MODEL_NAME)
    except OSError:
        logger.warning(
            "spaCy model %s is not installed; attempting to fetch it", MODEL_NAME
        )

    # `sys.executable`, not "python": the bare name may resolve to a different
    # interpreter than the one running this process, and then the download
    # would install the model somewhere this process cannot import it from.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "spacy", "download", MODEL_NAME],
            capture_output=True,
            timeout=MODEL_DOWNLOAD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Downloading the spaCy model {MODEL_NAME} took longer than "
            f"{MODEL_DOWNLOAD_TIMEOUT_SECONDS}s and was abandoned. Install it "
            f"offline instead: {sys.executable} -m spacy download {MODEL_NAME}"
        ) from None

    if result.returncode != 0:
        raise RuntimeError(
            f"Could not download the spaCy model {MODEL_NAME} (exit "
            f"{result.returncode}): {result.stderr.decode(errors='replace').strip()}"
        )

    # A clear failure beats a bare OSError from deep inside spaCy.
    try:
        return spacy.load(MODEL_NAME)
    except OSError as exc:
        raise RuntimeError(
            f"{MODEL_NAME} still cannot be loaded after downloading it; the "
            f"model may have been installed for a different interpreter ({sys.executable})"
        ) from exc


def _get_nlp() -> Any:
    """Return the process-wide spaCy pipeline, loading it on first use.

    Double-checked locking: the steady-state path takes no lock, and the lock only
    serialises the single construction. The lock is not decoration — the extraction
    routers and the MP3 export path construct engines from different threads, so two
    could otherwise load the model at once.

    Thread safety of *sharing* one pipeline was measured, not assumed, on the
    versions pinned here (spaCy 3.8.16 / thinc 8.3.13 / en_core_web_sm 3.8.0):
    8 threads x 10 rounds x 12 texts = 960 concurrent inferences produced 0
    exceptions and 0 results differing from a sequential baseline. Sharing is safe
    here because nothing in this codebase mutates the pipeline — verified: there is
    no `add_pipe`, `disable_pipe`, `enable_pipe` or `select_pipes` call outside the
    library. A caller that starts mutating `nlp` would break that; don't.
    """
    global _nlp
    if _nlp is None:
        with _nlp_lock:
            if _nlp is None:
                _nlp = _load_pipeline()
    return _nlp


@dataclass
class SentenceRecord:
    index: int
    text: str
    page: int = 0
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    filtered: bool = False
    words: list = field(default_factory=list)
    chapter: int = 0
    chapter_title: str | None = None


class BaseEngine:
    """Base class for sentence extraction engines using spaCy."""

    def __init__(self):
        # A per-instance reference to the one shared pipeline, not a second load.
        # Subclasses and tests read `self.nlp`, and keeping it an instance
        # attribute means either can still substitute its own.
        self.nlp = _get_nlp()

    def _split_sentences(self, text: str, min_words: int = 3) -> list[str]:
        """Split text into sentences using spaCy, filtering short fragments."""
        if not text or text.isspace():
            return []
        
        doc = self.nlp(text)
        sentences = []
        for sent in doc.sents:
            text_sent = sent.text.strip()
            if not text_sent:
                continue
            # Count words (skip punctuation)
            word_count = len([t for t in sent if not t.is_punct])
            if word_count >= min_words:
                sentences.append(text_sent)
        return sentences
