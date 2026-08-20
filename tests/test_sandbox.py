from core.sandbox import run_code, validate_code


def test_safe_code_passes_validation():
    safe, violations = validate_code("print('hello')")
    assert safe is True
    assert violations == []


def test_unsafe_import_blocked():
    safe, violations = validate_code("import os")
    assert safe is False
    assert any("Forbidden import" in v for v in violations)


def test_run_code_success():
    result = run_code("print('hello')")
    assert result["success"] is True
    assert "hello" in result["stdout"]


def test_run_code_with_args():
    code = "import sys,json; print(json.loads(sys.argv[1])['x'])"
    result = run_code(code, args=['{"x": 42}'])
    assert result["success"] is True
    assert "42" in result["stdout"]


def test_run_code_failure():
    result = run_code("raise ValueError('boom')")
    assert result["success"] is False
    assert "boom" in result["stderr"]