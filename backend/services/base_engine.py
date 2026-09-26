"""Shared base engine abstraction for sentence extraction."""
from dataclasses import dataclass, field
import logging
import subprocess
import sys

import spacy

logger = logging.getLogger(__name__)

# `BaseEngine()` is constructed per test and per extraction, so this fallback runs
# on the request path rather than at install time. It shells out to `spacy
# download`, which hits the network, so it must be bounded: an unbounded version
# of exactly this call is what made the test suite look like it had hung, and it
# was reaching for a bare `python` that may not be the interpreter running us.
MODEL_DOWNLOAD_TIMEOUT_SECONDS = 180


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
        try:
            self.nlp = spacy.load("en_core_web_sm")
            return
        except OSError:
            logger.warning(
                "spaCy model en_core_web_sm is not installed; attempting to fetch it"
            )

        # `sys.executable`, not "python": the bare name may resolve to a different
        # interpreter than the one running this process, and then the download
        # would install the model somewhere this process cannot import it from.
        try:
            result = subprocess.run(
                [sys.executable, "-m", "spacy", "download", "en_core_web_sm"],
                capture_output=True,
                timeout=MODEL_DOWNLOAD_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Downloading the spaCy model en_core_web_sm took longer than "
                f"{MODEL_DOWNLOAD_TIMEOUT_SECONDS}s and was abandoned. Install it "
                f"offline instead: {sys.executable} -m spacy download en_core_web_sm"
            ) from None

        if result.returncode != 0:
            raise RuntimeError(
                f"Could not download the spaCy model en_core_web_sm (exit "
                f"{result.returncode}): {result.stderr.decode(errors='replace').strip()}"
            )

        # A clear failure beats a bare OSError from deep inside spaCy.
        try:
            self.nlp = spacy.load("en_core_web_sm")
        except OSError as exc:
            raise RuntimeError(
                "en_core_web_sm still cannot be loaded after downloading it; the "
                f"model may have been installed for a different interpreter ({sys.executable})"
            ) from exc

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
