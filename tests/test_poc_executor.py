from core.poc_executor import command_is_safe, _extract_evidence


def test_curl_command_is_safe():
    safe, reason = command_is_safe("curl -I https://example.com")
    assert safe is True


def test_rm_command_is_not_safe():
    safe, reason = command_is_safe("rm -rf C:/")
    assert safe is False


def test_unknown_executable_is_not_safe():
    safe, reason = command_is_safe("python -c 'print(1)'")
    assert safe is False


def test_pipe_is_not_safe():
    safe, reason = command_is_safe("curl -I https://example.com | rm -rf C:/")
    assert safe is False


def test_extract_evidence_http_ok():
    assert _extract_evidence("HTTP/1.1 200 OK", "") == "200 OK"


def test_extract_evidence_root_pattern():
    assert "root:x:0:0" in _extract_evidence("root:x:0:0", "")