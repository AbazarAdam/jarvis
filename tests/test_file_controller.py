import sys
from pathlib import Path

# Make the project root importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.file_controller import _resolve_path


def test_resolve_desktop():
    path = _resolve_path("desktop")
    assert path == Path.home() / "Desktop"


def test_resolve_desktop_with_filename():
    path = _resolve_path("desktop/test.txt")
    assert path == Path.home() / "Desktop" / "test.txt"


def test_resolve_downloads():
    path = _resolve_path("downloads")
    assert path == Path.home() / "Downloads"


def test_resolve_absolute_windows_path():
    raw = r"C:\Users\abaze\Desktop\file.txt"
    path = _resolve_path(raw)
    assert str(path) == raw


def test_resolve_home():
    path = _resolve_path("home")
    assert path == Path.home()