import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.memory_manager import load_memory


def test_load_memory_missing_file(tmp_path, monkeypatch):
    fake_file = tmp_path / "nonexistent.json"
    monkeypatch.setattr("memory.memory_manager.MEMORY_PATH", fake_file)
    data = load_memory()
    assert isinstance(data, dict)


def test_load_memory_valid_file(tmp_path, monkeypatch):
    fake_file = tmp_path / "long_term.json"
    fake_file.write_text('{"identity": {"name": {"value": "Test"}}}', encoding="utf-8")
    monkeypatch.setattr("memory.memory_manager.MEMORY_PATH", fake_file)
    data = load_memory()
    assert data["identity"]["name"]["value"] == "Test"


def test_load_memory_invalid_json(tmp_path, monkeypatch):
    fake_file = tmp_path / "bad.json"
    fake_file.write_text("{invalid json", encoding="utf-8")
    monkeypatch.setattr("memory.memory_manager.MEMORY_PATH", fake_file)
    data = load_memory()
    assert data == {}