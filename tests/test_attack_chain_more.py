from actions import attack_chain


def test_correlate_vulnerabilities_with_mocked_nvd(monkeypatch):
    monkeypatch.setattr(
        attack_chain,
        "fetch_nvd_cves",
        lambda keyword, max_results=20: [
            {
                "cve_id": "CVE-2024-9999",
                "description": "Test Apache RCE",
                "cvss_score": 9.8,
                "severity": "CRITICAL",
                "published_date": "2024-01-01T00:00:00.000",
            }
        ],
    )
    monkeypatch.setattr(
        attack_chain,
        "_generate_poc_commands",
        lambda service, product, version, cve_ids: ["curl http://example"],
    )

    result = attack_chain.correlate_vulnerabilities(
        "example.com",
        {"technologies": ["Server: Apache"]},
    )

    assert len(result["attack_chains"]) == 1
    assert result["attack_chains"][0]["product"] == "Apache"
    assert result["correlated_findings"][0].startswith("Apache")