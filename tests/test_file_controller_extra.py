import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.file_controller import _format_size, _get_desktop, _get_downloads


def test_format_size_bytes():
    assert _format_size(512) == "512.0 B"


def test_format_size_kb():
    assert _format_size(2048) == "2.0 KB"


def test_format_size_mb():
    assert _format_size(3 * 1024 * 1024) == "3.0 MB"


def test_format_size_gb():
    assert _format_size(2 * 1024 * 1024 * 1024) == "2.0 GB"


def test_get_desktop():
    assert _get_desktop() == Path.home() / "Desktop"


def test_get_downloads():
    assert _get_downloads() == Path.home() / "Downloads"


def test_format_size_half_kb():
    assert _format_size(512) == "512.0 B"


def test_format_size_one_mb():
    assert _format_size(1024 * 1024) == "1.0 MB"


def test_format_size_three_gb():
    assert _format_size(3 * 1024 ** 3) == "3.0 GB"