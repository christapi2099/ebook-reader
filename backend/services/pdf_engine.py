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

            for block_list in blocks.values():
                if not block_list:
                    continue
                words_sorted = sorted(block_list, key=lambda w: (w[1], w[0]))
                block_text_parts = [w[4] for w in words_sorted]
                block_text = " ".join(block_text_parts)
                if not block_text.strip():
                    continue

                spacy_doc = self.nlp(block_text)
                for sent in spacy_doc.sents:
                    sent_text = sent.text.strip()
                    if not sent_text:
                        continue
                    sent_start = sent.start_char
                    sent_end = sent.end_char
                    sent_words = []
                    current_pos = 0
                    for w in words_sorted:
                        word_text = w[4]
                        word_len = len(word_text)
                        if current_pos + word_len > sent_start and current_pos < sent_end:
                            sent_words.append(w)
                        current_pos += word_len + 1
                    if len(sent_words) < 3:
                        continue
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
