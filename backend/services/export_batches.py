"""Plan the sentence batches a Modal export is fanned out over.

Batching exists because ``KModel.forward_with_tokens`` handles batch size 1
only, so the way to use more than one GPU is to send *more text per call* — not
to send many inputs to one call. One batch is one Modal container.

The grouping rule is the chapter, because a chapter is the unit a reader
recognises and the unit the export dialog can report progress in. A chapter
longer than ``max_sentences`` is split into several batches of at most that
many, in sentence order, so a 900-sentence chapter still fans out instead of
serialising on one container.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

DEFAULT_MAX_SENTENCES = 150


class _Unset:
    """Sentinel so ``chapter=None`` (pasted text) is a real chapter value."""


_UNSET = _Unset()


@dataclass(frozen=True)
class ExportBatch:
    """One Modal call's worth of work."""

    batch_index: int
    chapter: int | None
    chapter_title: str | None
    sentences: list[dict[str, Any]] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        """The wire shape ``ModalKokoroClient.synthesize_batches`` sends."""
        return {"batch_index": self.batch_index, "sentences": list(self.sentences)}

    @property
    def sentence_indices(self) -> list[int]:
        return [int(s["index"]) for s in self.sentences]


def plan_batches(
    rows: Mapping[int, Mapping[str, Any]],
    max_sentences: int = DEFAULT_MAX_SENTENCES,
) -> list[ExportBatch]:
    """Group unfiltered sentences into chapter batches, keeping sentence order.

    ``rows`` is the ``{index: row}`` mapping the routers already build. Filtered
    sentences and blank text are dropped here rather than at synthesis time, so
    a batch's sentence list is exactly what will be spoken and the export's
    progress arithmetic matches the audio it produces.

    Pasted text has no chapter (``None``) and becomes one run of batches.
    """
    if max_sentences < 1:
        raise ValueError("max_sentences must be at least 1")

    batches: list[ExportBatch] = []
    pending: list[dict[str, Any]] = []
    pending_chapter: Any = _UNSET
    pending_title: str | None = None

    def flush() -> None:
        nonlocal pending, pending_chapter, pending_title
        if not pending:
            return
        chapter = None if pending_chapter is _UNSET else pending_chapter
        batches.append(
            ExportBatch(
                batch_index=len(batches),
                chapter=chapter,
                chapter_title=pending_title,
                sentences=pending,
            )
        )
        pending = []

    for index in sorted(rows):
        row = rows[index]
        if row.get("filtered"):
            continue
        text = (row.get("text") or "").strip()
        if not text:
            continue

        chapter = row.get("chapter")
        if chapter != pending_chapter:
            flush()
            pending_chapter = chapter
            pending_title = row.get("chapter_title")

        pending.append({"index": int(index), "text": text})
        if len(pending) >= max_sentences:
            flush()

    flush()
    return batches

