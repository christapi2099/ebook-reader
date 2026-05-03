import re
import enum
from typing import Optional


class FilterReason(enum.Enum):
    COPYRIGHT = 'copyright'
    TOC_ENTRY = 'toc_entry'
    CHAPTER_HEADING = 'chapter_heading'
    SECTION_HEADING = 'section_heading'
    PAGE_NUMBER = 'page_number'
    PUBLISHER_INFO = 'publisher_info'
    EXERCISE_MARKER = 'exercise_marker'
    ALL_CAPS_MARKER = 'all_caps_marker'


class TextFilter:
    def __init__(self):
        self._patterns = {
            FilterReason.COPYRIGHT: [
                re.compile(r'©'),
                re.compile(r'copyright', re.IGNORECASE),
                re.compile(r'all rights reserved', re.IGNORECASE),
                re.compile(r'ISBN', re.IGNORECASE),
                re.compile(r'Creative Commons', re.IGNORECASE),
                re.compile(r'licensed under', re.IGNORECASE),
                re.compile(r'\(\d{3}\)\s*\d{3}-\d{4}'),
                re.compile(r'^https?://'),
                re.compile(r'For more information, visit', re.IGNORECASE),
                re.compile(r'printed in', re.IGNORECASE),
                re.compile(r'\b(Suite|Department)\s+\d+', re.IGNORECASE),
            ],
            FilterReason.TOC_ENTRY: [
                re.compile(r'\.{4,}'),
                re.compile(r'^\s*(Table of [Cc]ontents|CONTENTS)\s*$'),
                re.compile(r'^[xivlcdmIVXLCDM]+\s+Contents\s*$'),
            ],
            FilterReason.CHAPTER_HEADING: [
                re.compile(r'^\s*Chapter\s+\d+', re.IGNORECASE),
                re.compile(r'^\s*Appendix\s+[A-Z]', re.IGNORECASE),
                re.compile(r'^\s*\d+\.\d+\s+\w'),
                re.compile(r'^\s*(Preface|Introduction|Dedication|Bibliography|Index|Foreword|Afterword)\s*$', re.IGNORECASE),
            ],
            FilterReason.PAGE_NUMBER: [
                re.compile(r'^\s*\d+\s*$'),
                re.compile(r'^\s*[ivxlcdmIVXLCDM]+\s*$'),
                re.compile(r'^\s*Page\s+\d+\s*$', re.IGNORECASE),
            ],
            FilterReason.PUBLISHER_INFO: [
                re.compile(r'•'),
                re.compile(r'^\s*For (sales|information)', re.IGNORECASE),
            ],
            FilterReason.EXERCISE_MARKER: [
                re.compile(r'^\s*Exercise\s+\d+', re.IGNORECASE),
                re.compile(r'^\s*\d+\.\s+[A-Z]'),
            ],
        }

    def should_filter(self, text: str) -> bool:
        return self.filter_reason(text) is not None

    def filter_reason(self, text: str) -> Optional[FilterReason]:
        if not text or text.isspace():
            return None

        if self._check_copyright(text):
            return FilterReason.COPYRIGHT
        if self._check_patterns(text, FilterReason.TOC_ENTRY):
            return FilterReason.TOC_ENTRY
        if self._check_patterns(text, FilterReason.CHAPTER_HEADING):
            return FilterReason.CHAPTER_HEADING
        if self._check_patterns(text, FilterReason.PAGE_NUMBER):
            return FilterReason.PAGE_NUMBER
        if self._check_patterns(text, FilterReason.PUBLISHER_INFO):
            return FilterReason.PUBLISHER_INFO
        if self._check_patterns(text, FilterReason.EXERCISE_MARKER):
            return FilterReason.EXERCISE_MARKER
        if self._check_all_caps(text):
            return FilterReason.ALL_CAPS_MARKER

        return None

    def _check_copyright(self, text: str) -> bool:
        if '@' in text and len(text) < 80:
            return True
        for pattern in self._patterns[FilterReason.COPYRIGHT]:
            if pattern.search(text):
                return True
        return False

    def _check_patterns(self, text: str, reason: FilterReason) -> bool:
        for pattern in self._patterns[reason]:
            if pattern.search(text):
                return True
        return False

    def _check_all_caps(self, text: str) -> bool:
        letters_only = ''.join(c for c in text if c.isalpha() or c.isspace())
        words = letters_only.split()
        if not words:
            return False
        has_letters = any(c.isalpha() for c in text)
        if not has_letters:
            return False
        all_uppercase = all(word.isupper() for word in words if word)
        if all_uppercase and len(text) < 60 and len(words) >= 2:
            return True
        if all_uppercase and len(words) == 1 and len(text.strip()) >= 4:
            return True
        return False
