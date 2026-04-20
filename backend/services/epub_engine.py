import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import spacy
from dataclasses import dataclass
from typing import List
import os

@dataclass
class SentenceRecord:
    index: int
    text: str
    chapter: str

class EPUBEngine:
    def __init__(self):
        self.nlp = spacy.load("en_core_web_sm")

    def extract_sentences(self, epub_path: str) -> List[SentenceRecord]:
        if not os.path.exists(epub_path):
            raise FileNotFoundError(f"EPUB file not found: {epub_path}")

        book = epub.read_epub(epub_path)
        items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
        sentences = []
        global_index = 0

        for item in items:
            chapter_name = self._get_chapter_name(item)
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
                    sentences.append(SentenceRecord(index=global_index, text=sent_text, chapter=chapter_name))
                    global_index += 1

        return sentences

    def _get_chapter_name(self, item):
        if item.title and item.title.strip():
            return item.title.strip()
        return os.path.splitext(os.path.basename(item.file_name))[0] if item.file_name else "Unknown Chapter"
