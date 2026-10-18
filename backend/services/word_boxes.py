"""The ONE fuzzy word matcher, plus word -> PDF-box mapping.

``match_words`` maps one word list onto another (known text vs. recognized /
native / PDF words). It is used by services/aligner.py (mapping engine tokens
onto sentence words) and by ``map_words_to_boxes`` below, so there is a single
implementation to test.
"""
from __future__ import annotations

import difflib
import re
from typing import Optional, Sequence

FUZZY_RATIO = 0.5  # per-word similarity needed to pair words inside a replace block

_ABBREV = {
    "dr": "doctor", "mr": "mister", "mrs": "missus", "ms": "miss", "st": "saint",
    "prof": "professor", "vs": "versus", "etc": "etcetera", "no": "no",
}
_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
         "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def number_to_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (_ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return _ONES[n // 100] + "hundred" + (number_to_words(n % 100) if n % 100 else "")
    if n < 1_000_000:
        return number_to_words(n // 1000) + "thousand" + (number_to_words(n % 1000) if n % 1000 else "")
    return str(n)


def normalize_word(w: str) -> str:
    """Lowercase, drop punctuation, expand abbreviations and integers to spoken form."""
    t = re.sub(r"[^a-z0-9']", "", w.lower().replace("’", "'"))
    t = t.strip("'")
    if t.isdigit() and len(t) <= 6:
        return number_to_words(int(t))
    return _ABBREV.get(t, t)


def match_words(known: Sequence[str], other: Sequence[str]) -> dict[int, int]:
    """known index -> other index. Exact blocks first, then 1:1 fuzzy inside
    equal-sized replace blocks. Unmatched known words are simply absent."""
    k = [normalize_word(w) for w in known]
    o = [normalize_word(w) for w in other]
    sm = difflib.SequenceMatcher(None, k, o, autojunk=False)
    out: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for d in range(i2 - i1):
                out[i1 + d] = j1 + d
        elif tag == "replace" and (i2 - i1) == (j2 - j1):
            for d in range(i2 - i1):
                a, b = k[i1 + d], o[j1 + d]
                if a and b and difflib.SequenceMatcher(None, a, b).ratio() >= FUZZY_RATIO:
                    out[i1 + d] = j1 + d
    return out


Box = tuple[float, float, float, float]


def _candidates(boxes: Sequence[Sequence]) -> list[tuple[str, Box]]:
    """PyMuPDF word tuples (x0,y0,x1,y1,text,...) -> (text, box); a word ending in a
    line-break hyphen is joined with the following word (box = the first fragment)."""
    out: list[tuple[str, Box]] = []
    i = 0
    while i < len(boxes):
        x0, y0, x1, y1, t = boxes[i][:5]
        if len(t) > 1 and t.endswith("-") and i + 1 < len(boxes) and boxes[i + 1][1] > y0 + (y1 - y0) / 2:
            out.append((t[:-1] + boxes[i + 1][4], (x0, y0, x1, y1)))
            i += 2
            continue
        out.append((t, (x0, y0, x1, y1)))
        i += 1
    return out


def map_words_to_boxes(words: Sequence, pymupdf_word_boxes: Sequence[Sequence]) -> list[Optional[Box]]:
    """One entry per word: its PDF box, or None when it has no match. ``words`` are
    strings or objects with ``.word``; pass only the boxes of the word's own sentence
    so an unmapped word can never point at another sentence."""
    texts = [w if isinstance(w, str) else w.word for w in words]
    cands = _candidates(pymupdf_word_boxes)
    m = match_words(texts, [c[0] for c in cands])
    return [cands[m[i]][1] if i in m else None for i in range(len(texts))]
