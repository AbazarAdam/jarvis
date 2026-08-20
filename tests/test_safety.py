from pathlib import Path

from core.safety import classify_path, require_confirmation


def test_read_allowed_in_user_area():
    allowed, risk, reason = classify_path(Path.home() / "Desktop", "read")
    assert allowed is True
    assert risk == 0


def test_write_to_system32_blocked():
    allowed, risk, reason = classify_path("C:/Windows/System32/test.txt", "write")
    assert allowed is False
    assert risk == 4


def test_write_to_desktop_allowed():
    allowed, risk, reason = classify_path(Path.home() / "Desktop" / "test.txt", "write")
    assert allowed is True


def test_confirmation_required_for_high_risk():
    assert require_confirmation(4, None) is False
    assert require_confirmation(4, "yes") is True


def test_confirmation_not_required_for_low_risk():
    assert require_confirmation(0, None) is True