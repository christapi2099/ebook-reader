"""Unicode normalization and character cleanup for TTS input text.

Preserves word count and sentence boundaries — zero sync risk with Kokoro.
"""
import re
import unicodedata


def normalize_text(text: str) -> str:
    if not text:
        return text

    # Ellipsis → "." (must precede NFKC which decomposes … → ...)
    text = text.replace("\u2026", ".")

    # NFKC normalization (compatibility decomposition + composition)
    text = unicodedata.normalize("NFKC", text)

    # Smart quotes → straight quotes
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")

    # Em-dash / en-dash → ", " (TTS pause cue)
    text = text.replace("\u2014", '", "').replace("\u2013", '", "')

    # Guillemets → standard quotes
    text = text.replace("\u00ab", '"').replace("\u00bb", '"')

    # Zero-width characters stripped
    text = text.replace("\u200b", "").replace("\ufeff", "")

    # Collapse excess whitespace (including non-breaking spaces)
    text = text.replace("\u00a0", " ")
    text = re.sub(r" {2,}", " ", text)
    text = text.strip()

    return text
