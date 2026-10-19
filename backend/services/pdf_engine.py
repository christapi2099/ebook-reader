import fitz
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import os

from services.base_engine import BaseEngine, SentenceRecord as BaseSentenceRecord


def sentence_bbox(sent_words, block_bbox):
    if not sent_words:
        return block_bbox
    return (
        min(w[0] for w in sent_words),
        min(w[1] for w in sent_words),
        max(w[2] for w in sent_words),
        max(w[3] for w in sent_words),
    )


def _starts_new_line(prev, nxt) -> bool:
    return nxt[1] > prev[1] + (prev[3] - prev[1]) / 2


def join_line_hyphens(words_sorted):
    """Block text and each word's (start, end) character span.

    A word ending in "-" whose continuation starts a lowercase word on the next line
    is one word split by the line break ("measure-" / "ments"), so the text joins
    them. Both fragments keep their own word entry and share the joined token's span.
    Known limit: a real compound split at the line end ("well-" / "known") joins too,
    because a PDF cannot say which hyphens are soft.
    """
    tokens: List[Tuple[str, List[int]]] = []
    i = 0
    while i < len(words_sorted):
        t = words_sorted[i][4]
        nxt = words_sorted[i + 1] if i + 1 < len(words_sorted) else None
        if (nxt is not None and len(t) > 1 and t.endswith("-")
                and _starts_new_line(words_sorted[i], nxt) and nxt[4][:1].islower()):
            tokens.append((t[:-1] + nxt[4], [i, i + 1]))
            i += 2
        else:
            tokens.append((t, [i]))
            i += 1
    text = ""
    spans: List[Tuple[int, int]] = [(0, 0)] * len(words_sorted)
    for n, (tok, idxs) in enumerate(tokens):
        if n:
            text += " "
        start = len(text)
        text += tok
        for k in idxs:
            spans[k] = (start, len(text))
    return text, spans


class PDFEngine(BaseEngine):
    def __init__(self):
        super().__init__()  # Initialize spaCy via BaseEngine

    def extract_sentences(self, pdf_path: str) -> List[BaseSentenceRecord]:
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        doc = fitz.open(pdf_path)
        all_sentences = []
        global_index = 0

        # Build chapter boundaries from TOC
        chapters = self._build_chapters(doc)

        for page_num, page in enumerate(doc):
            words = page.get_text("words")
            blocks: Dict[int, List[Tuple[float, float, float, float, str]]] = {}
            for word_info in words:
                x0, y0, x1, y1, word, block_no, line_no, word_no = word_info
                if block_no not in blocks:
                    blocks[block_no] = []
                blocks[block_no].append((x0, y0, x1, y1, word))

            # Sort blocks by reading order: top-to-bottom, then left-to-right
            def block_center(block_list):
                avg_y = sum(w[1] for w in block_list) / len(block_list)
                avg_x = sum(w[0] for w in block_list) / len(block_list)
                return (avg_y, avg_x)

            sorted_blocks = sorted(blocks.values(), key=lambda b: block_center(b) if b else (0, 0))

            for block_list in sorted_blocks:
                if not block_list:
                    continue
                words_sorted = sorted(block_list, key=lambda w: (w[1], w[0]))

                # Faux-bold/emphasis is sometimes rendered as the same glyph
                # run painted twice at (near-)identical coordinates, which
                # PyMuPDF reports as two separate words — collapse those
                # before they reach TTS as "the the green green".
                deduped: List[Tuple[float, float, float, float, str]] = []
                for w in words_sorted:
                    if deduped:
                        pw = deduped[-1]
                        if w[4] == pw[4] and abs(w[0] - pw[0]) < 1.0 and abs(w[1] - pw[1]) < 1.0:
                            continue
                    deduped.append(w)
                words_sorted = deduped
                block_text, word_char_positions = join_line_hyphens(words_sorted)
                if not block_text.strip():
                    continue

                # Full block bbox — used as fallback for short/unmatched sentences
                block_x0 = min(w[0] for w in words_sorted)
                block_y0 = min(w[1] for w in words_sorted)
                block_x1 = max(w[2] for w in words_sorted)
                block_y1 = max(w[3] for w in words_sorted)

                spacy_doc = self.nlp(block_text)
                for sent in spacy_doc.sents:
                    sent_text = sent.text.strip()
                    if not sent_text:
                        continue

                    sent_start = sent.start_char
                    sent_end = sent.end_char

                    # Match words whose char range falls inside the sentence
                    sent_words = []
                    for i, w in enumerate(words_sorted):
                        w_start, w_end = word_char_positions[i]
                        if w_start >= sent_start and w_end <= sent_end:
                            sent_words.append(w)

                    # Skip sentences with zero matched words
                    if len(sent_words) == 0:
                        continue

                    x0_min, y0_min, x1_max, y1_max = sentence_bbox(sent_words, (block_x0, block_y0, block_x1, block_y1))

                    chapter_idx, chapter_title = self._page_chapter(chapters, page_num)

                    all_sentences.append(BaseSentenceRecord(
                        index=global_index,
                        text=sent_text,
                        page=page_num,
                        x0=x0_min,
                        y0=y0_min,
                        x1=x1_max,
                        y1=y1_max,
                        words=[{'x0': float(w[0]), 'y0': float(w[1]), 'x1': float(w[2]), 'y1': float(w[3])} for w in sent_words],
                        chapter=chapter_idx,
                        chapter_title=chapter_title,
                    ))
                    global_index += 1
        doc.close()
        return all_sentences

    def _build_chapters(self, doc) -> List[Tuple[str, int]]:
        toc = doc.get_toc()
        if toc:
            chapters: List[Tuple[str, int]] = []
            for level, title, page_1based in toc:
                if level == 1:
                    chapters.append((title, page_1based - 1))
            if chapters:
                return chapters
        return []

    def _page_chapter(self, chapters: List[Tuple[str, int]], page: int) -> Tuple[int, Optional[str]]:
        SENTINEL_PAGE = 1_000_000
        if not chapters:
            return (0, None)
        for i, (title, start_page) in enumerate(chapters):
            next_start = chapters[i + 1][1] if i + 1 < len(chapters) else SENTINEL_PAGE
            if start_page <= page < next_start:
                return (i + 1, title)
        return (0, None)

    def page_count(self, pdf_path: str) -> int:
        doc = fitz.open(pdf_path)
        count = len(doc)
        doc.close()
        return count

    def is_image_page(self, pdf_path: str, page_num: int) -> bool:
        doc = fitz.open(pdf_path)
        page = doc[page_num]
        text = page.get_text("text")
        doc.close()
        return len(text.strip()) < 20
