"""Text engine for extracting sentences from plain text."""
from services.base_engine import BaseEngine, SentenceRecord


class TextEngine(BaseEngine):
    """Engine for extracting sentences from plain text."""

    def extract_sentences(self, text: str, min_words: int = 2) -> list[SentenceRecord]:
        """Extract sentences from plain text.

        Args:
            text: The plain text content to split into sentences.
            min_words: Minimum words required for a valid sentence (default 2 for text input).

        Returns:
            List of SentenceRecord objects with index and text.
            All coordinates are zero (like EPUB books).
        """
        raw = self._split_sentences(text, min_words=min_words)
        return [SentenceRecord(index=i, text=s) for i, s in enumerate(raw)]
