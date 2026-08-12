import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.computer_settings import _detect_action


def test_detect_action_volume():
    result = _detect_action("Set volume to 50%")
    assert result.get("action") in ("set_volume", "volume_set")


def test_detect_action_brightness():
    result = _detect_action("Brightness up")
    assert result.get("action") in ("brightness_up", "brightness_set")