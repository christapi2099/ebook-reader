import pytest
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
EBOOKS_DIR = Path.home() / "Documents" / "EBooks"

@pytest.fixture
def sample_pdf_path():
    return EBOOKS_DIR / "cleancodebook.pdf"

@pytest.fixture
def sample_epub_path():
    return EBOOKS_DIR / "cleancodebook.epub"

@pytest.fixture
def logic_pdf_path():
    return EBOOKS_DIR / "Logic text v 2.0.pdf"

@pytest.fixture
def hardthing_pdf_path():
    return EBOOKS_DIR / "Ben_Horowitz_The_Hard_Thing_About_Hard_Things.pdf"
