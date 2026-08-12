import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.computer_control import _safe_screenshot_path, _KNOWN_FOLDERS


def test_no_request_returns_timestamped_desktop():
    path = _safe_screenshot_path(None)
    assert path.parent == Path.home() / "Desktop"
    assert path.name.startswith("Screenshot_")
    assert path.suffix == ".png"


def test_desktop_folder_known():
    path = _safe_screenshot_path("desktop")
    assert path.parent == Path.home() / "Desktop"
    assert path.name.startswith("Screenshot_")


def test_downloads_folder_known():
    path = _safe_screenshot_path("downloads")
    assert path.parent == Path.home() / "Downloads"


def test_filename_only_saves_to_desktop():
    path = _safe_screenshot_path("my_shot.png")
    assert path == Path.home() / "Desktop" / "my_shot.png"


def test_desktop_with_filename():
    path = _safe_screenshot_path("desktop/report.png")
    assert path == Path.home() / "Desktop" / "report.png"


def test_relative_path_inside_home():
    path = _safe_screenshot_path("Pictures/jarvis.png")
    assert path == Path.home() / "Pictures" / "jarvis.png"