import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.cyber_recon import (
    _parse_nmap_text,
    _parse_nikto,
    _enumerate_subdomains,
)


def test_parse_nmap_empty_output():
    result = _parse_nmap_text("No scan report")
    assert result == []


def test_parse_nmap_single_host_with_open_port():
    output = """
Nmap scan report for example.com (1.2.3.4)
PORT      STATE SERVICE
22/tcp    open  ssh
80/tcp    open  http
"""
    result = _parse_nmap_text(output)
    assert len(result) == 1
    assert result[0]["ip"] == "(1.2.3.4)"
    assert result[0]["open_ports"] == ["22/tcp ssh", "80/tcp http"]


def test_parse_nmap_multiple_hosts():
    output = """
Nmap scan report for host1 (1.1.1.1)
80/tcp open http
Nmap scan report for host2 (2.2.2.2)
443/tcp open https
"""
    result = _parse_nmap_text(output)
    assert len(result) == 2
    assert result[1]["ip"] == "(2.2.2.2)"


def test_parse_nikto_empty():
    assert _parse_nikto("") == []


def test_parse_nikto_filters_errors():
    raw = """
+ Finding 1
ERROR: Something went wrong
+ Finding 2
"""
    result = _parse_nikto(raw)
    assert result == ["+ Finding 1", "+ Finding 2"]


def test_parse_nikto_unique_only():
    raw = """
+ Duplicate
+ Duplicate
+ Unique
"""
    result = _parse_nikto(raw)
    assert result == ["+ Duplicate", "+ Unique"]


def test_enumerate_subdomains_no_network():
    # Without internet, this should return an empty list gracefully
    result = _enumerate_subdomains("nonexistent.invalid")
    assert result == []