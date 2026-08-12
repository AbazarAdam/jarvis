import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.planner import _fallback_plan


def test_fallback_plan_read_file():
    plan = _fallback_plan("Read the file test.txt from desktop")
    assert plan["steps"][0]["tool"] == "file_controller"
    assert plan["steps"][0]["parameters"]["action"] == "read"


def test_fallback_plan_convert_to_pdf():
    plan = _fallback_plan("Convert test.txt to PDF")
    assert plan["steps"][0]["tool"] == "file_processor"
    assert plan["steps"][0]["parameters"]["action"] == "to_pdf"


def test_fallback_plan_research():
    plan = _fallback_plan("Research the benefits of water and save to file")
    assert plan["steps"][0]["tool"] == "web_search"
    # Second step should be file_controller write
    assert any(step["tool"] == "file_controller" for step in plan["steps"])


def test_fallback_plan_minimize():
    plan = _fallback_plan("Minimize Chrome")
    # Should not be agent_task; likely web_search fallback because no direct mapping
    assert plan["steps"][0]["tool"] in ("web_search", "file_controller", "file_processor")


def test_fallback_plan_empty_goal():
    plan = _fallback_plan("")
    assert "steps" in plan
    assert isinstance(plan["steps"], list)