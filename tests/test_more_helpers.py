import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.file_controller import _resolve_path, _format_size


def test_resolve_documents():
    assert _resolve_path("documents") == Path.home() / "Documents"


def test_resolve_pictures():
    assert _resolve_path("pictures") == Path.home() / "Pictures"


def test_resolve_music():
    assert _resolve_path("music") == Path.home() / "Music"


def test_resolve_videos():
    assert _resolve_path("videos") == Path.home() / "Videos"


def test_resolve_home_with_file():
    assert _resolve_path("home/notes.txt") == Path.home() / "notes.txt"


def test_format_size_zero():
    assert _format_size(0) == "0.0 B"


def test_format_size_1023():
    assert _format_size(1023) == "1023.0 B"


def test_format_size_1024():
    assert _format_size(1024) == "1.0 KB"


def test_format_size_1536():
    assert _format_size(1536) == "1.5 KB"


def test_format_size_tb():
    assert _format_size(2 * 1024 ** 4) == "2.0 TB"