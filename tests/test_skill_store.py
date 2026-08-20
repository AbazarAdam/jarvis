from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from core.skill_store import SkillStore


@pytest.fixture()
def allowed_tmp(monkeypatch, tmp_path):
    """Allow writes inside the pytest temporary directory for these tests."""
    import core.safety

    original = core.safety.allowed_write_roots

    def patched_roots():
        return original() + [Path(tmp_path)]

    monkeypatch.setattr(core.safety, "allowed_write_roots", patched_roots)
    return tmp_path


def test_create_and_list_skill(allowed_tmp):
    store = SkillStore(allowed_tmp)
    result = store.save_skill(
        name="reverse_text",
        code="print('ok')",
        description="Reverse text",
    )
    assert result["created"] is True

    skills = store.list_skills()
    assert any(s["name"] == "reverse_text" for s in skills)


def test_duplicate_skill_rejected(allowed_tmp):
    store = SkillStore(allowed_tmp)
    desc = "Reverse a string and return it"
    store.save_skill("reverse_one", "print('ok')", desc)
    result = store.save_skill("reverse_two", "print('ok')", desc)
    assert result["created"] is False
    assert result["reason"] == "similar_skill_exists"


def test_record_success_promotes_skill(allowed_tmp):
    store = SkillStore(allowed_tmp)
    store.save_skill("example", "print('ok')", "Example skill")
    store.record_success("example")
    store.record_success("example")
    meta = store.get_skill("example")
    assert meta["status"] == "active"
    assert meta["confidence"] >= 0.75


def test_delete_requires_confirmation(allowed_tmp):
    store = SkillStore(allowed_tmp)
    store.save_skill("delete_me", "print('ok')", "Delete test")
    result = store.delete_skill("delete_me", confirmed="no")
    assert result["deleted"] is False