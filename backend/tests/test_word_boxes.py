import pytest

from services import word_boxes as wb


def B(words):
    return [(i * 10.0, 0.0, i * 10.0 + 9, 10.0, w, 0, 0, i) for i, w in enumerate(words)]


def test_exact_one_to_one():
    r = wb.map_words_to_boxes(["The", "cat", "sat."], B(["The", "cat", "sat."]))
    assert r == [(0.0, 0.0, 9.0, 10.0), (10.0, 0.0, 19.0, 10.0), (20.0, 0.0, 29.0, 10.0)]


def test_doctor_and_numbers():
    r = wb.map_words_to_boxes(["doctor", "Smith", "has", "twelve", "cats"], B(["Dr.", "Smith", "has", "12", "cats"]))
    assert all(x is not None for x in r)


def test_hyphen_line_split():
    boxes = [(0, 0, 50, 10, "an", 0, 0, 0), (60, 0, 100, 10, "exam-", 0, 0, 1), (0, 12, 30, 22, "ple", 0, 1, 2)]
    r = wb.map_words_to_boxes(["an", "example"], boxes)
    assert r[0] is not None and r[1] == (60, 0, 100, 10)


def test_unmapped_is_none_not_shifted():
    r = wb.map_words_to_boxes(["alpha", "zzzzqq", "gamma"], B(["alpha", "beta", "gamma"]))
    assert r[0] is not None and r[2] is not None and r[1] is None
    assert wb.map_words_to_boxes(["nothing"], []) == [None]


def test_normalize():
    assert wb.normalize_word("Dr.") == "doctor" and wb.normalize_word("21") == "twentyone"


def test_coverage_on_generated_pdf():
    fitz = pytest.importorskip("pymupdf")
    text = ("Dr. Smith walked 12 miles through the well-known valley. He said it was a remarkable "
            "afternoon, and the extraordinarily long sentence continued across lines until it finally ended. ") * 3
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(50, 50, 300, 700), text, fontsize=11)
    boxes = [w for w in page.get_text("words")]
    spoken = ["doctor" if t == "Dr." else ("twelve" if t == "12" else t) for t in text.split()]
    r = wb.map_words_to_boxes(spoken, boxes)
    assert sum(x is not None for x in r) / len(r) >= 0.95
