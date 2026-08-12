import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.file_processor import _detect_type, _file_size_str


def test_detect_more_extensions():
    assert _detect_type(Path("video.mp4")) == "video"
    assert _detect_type(Path("audio.mp3")) == "audio"
    assert _detect_type(Path("sheet.csv")) == "csv"
    assert _detect_type(Path("book.xlsx")) == "excel"
    assert _detect_type(Path("data.json")) == "json"
    assert _detect_type(Path("slides.pptx")) == "pptx"
    assert _detect_type(Path("archive.rar")) == "archive"
    assert _detect_type(Path("code.html")) == "code"


def test_detect_uppercase_extension():
    assert _detect_type(Path("PHOTO.PNG")) == "image"


def test_file_size_str_small(tmp_path):
    f = tmp_path / "small.txt"
    f.write_text("hello")
    assert _file_size_str(f) == "5.0 B"


def test_file_size_str_kb(tmp_path):
    f = tmp_path / "kb.txt"
    f.write_bytes(b"0" * 2048)
    assert _file_size_str(f) == "2.0 KB"


def test_file_size_str_mb(tmp_path):
    f = tmp_path / "mb.txt"
    f.write_bytes(b"0" * (2 * 1024 * 1024))
    assert _file_size_str(f) == "2.0 MB"