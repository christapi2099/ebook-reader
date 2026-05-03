import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
from typing import List
import os

from services.base_engine import BaseEngine, SentenceRecord as BaseSentenceRecord


class EPUBEngine(BaseEngine):
    """Extract sentences from EPUB files using spaCy sentence detection."""

    def __init__(self):
        super().__init__()  # Initialize spaCy via BaseEngine

    def extract_sentences(self, epub_path: str) -> List[BaseSentenceRecord]:
        if not os.path.exists(epub_path):
            raise FileNotFoundError(f"EPUB file not found: {epub_path}")

        book = epub.read_epub(epub_path)
        items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
        sentences = []
        global_index = 0

        for item in items:
            content = item.get_content()
            soup = BeautifulSoup(content, features='xml')
            paragraphs = soup.find_all('p')
            chapter_text = ' '.join(p.get_text(strip=True, separator=' ') for p in paragraphs if p.get_text(strip=True))

            if not chapter_text.strip():
                continue

            doc = self.nlp(chapter_text)
            for sent in doc.sents:
                sent_text = sent.text.strip()
                word_count = len([token for token in sent if not token.is_punct])
                if word_count >= 3:
                    # EPUB sentences have zero coordinates (no spatial position info)
                    sentences.append(BaseSentenceRecord(
                        index=global_index,
                        text=sent_text,
                        page=0,
                        x0=0.0,
                        y0=0.0,
                        x1=0.0,
                        y1=0.0,
                    ))
                    global_index += 1

        return sentences
