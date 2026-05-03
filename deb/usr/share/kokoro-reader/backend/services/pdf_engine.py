import fitz
import spacy
from dataclasses import dataclass
from typing import List, Dict, Tuple
import os

@dataclass
class SentenceRecord:
    index: int
    text: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float

class PDFEngine:
    def __init__(self):
        self.nlp = spacy.load('en_core_web_sm')

    def extract_sentences(self, pdf_path: str) -> List[SentenceRecord]:
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        doc = fitz.open(pdf_path)
        all_sentences = []
        global_index = 0

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
                block_text_parts = [w[4] for w in words_sorted]
                block_text = " ".join(block_text_parts)
                if not block_text.strip():
                    continue

                # Build precise char offsets for each word in the joined block_text
                word_char_positions: List[Tuple[int, int]] = []  # (start, end) per word
                pos = 0
                for word_text in block_text_parts:
                    word_char_positions.append((pos, pos + len(word_text)))
                    pos += len(word_text) + 1  # +1 for space separator

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

                    # Use full block bbox as fallback for short/title sentences
                    if len(sent_words) < 2:
                        x0_min, y0_min, x1_max, y1_max = block_x0, block_y0, block_x1, block_y1
                    else:
                        x0_min = min(w[0] for w in sent_words)
                        y0_min = min(w[1] for w in sent_words)
                        x1_max = max(w[2] for w in sent_words)
                        y1_max = max(w[3] for w in sent_words)

                    all_sentences.append(SentenceRecord(
                        index=global_index,
                        text=sent_text,
                        page=page_num,
                        x0=x0_min,
                        y0=y0_min,
                        x1=x1_max,
                        y1=y1_max,
                    ))
                    global_index += 1
        doc.close()
        return all_sentences

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
