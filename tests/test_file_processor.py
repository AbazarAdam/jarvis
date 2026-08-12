import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.file_processor import _detect_type


def test_detect_image():
    assert _detect_type(Path("photo.png")) == "image"
    assert _detect_type(Path("picture.jpg")) == "image"


def test_detect_pdf():
    assert _detect_type(Path("document.pdf")) == "pdf"


def test_detect_word():
    assert _detect_type(Path("report.docx")) == "docx"


def test_detect_text():
    assert _detect_type(Path("notes.txt")) == "text"


def test_detect_code():
    assert _detect_type(Path("script.py")) == "code"


def test_detect_archive():
    assert _detect_type(Path("backup.zip")) == "archive"


def test_detect_unknown():
    assert _detect_type(Path("random.xyz")) == "unknown"