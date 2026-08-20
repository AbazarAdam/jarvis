from core.skill_synthesizer import _build_prompt, _extract_code


def test_extract_code_removes_markdown():
    code = _extract_code("```python\nprint('hi')\n```")
    assert code == "print('hi')"


def test_build_prompt_forces_print_output():
    prompt = _build_prompt("reverse text")
    assert "MUST end with a TOP-LEVEL print()" in prompt
    assert "print(text[::-1])" in prompt