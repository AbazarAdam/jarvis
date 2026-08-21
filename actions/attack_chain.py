"""
actions/attack_chain.py — Real red-team vulnerability correlation engine.

This module upgrades JARVIS from a scanner wrapper into a reasoning red-team
assistant. Instead of only listing open ports and technologies, it:

  1. Extracts discovered services and versions from Nmap / headers / tech detection.
  2. Looks up relevant CVEs from the NVD public API.
  3. Filters CVEs by service and version relevance.
  4. Generates safe, evidence-based PoC commands using the cloud model router.
  5. Produces structured attack chains that security_mode can insert into the
     PDF report and spoken summary.

No local LLM is used. Network requests respect the configured proxy.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

import requests

from core.model_router import ModelRouter
from core.proxy_manager import get_requests_proxies


NVD_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
NVD_TIMEOUT = 15
MAX_CVES_PER_SERVICE = 5



def _classify_evidence(result: dict) -> str:
    """Classify attack evidence as confirmed / probable / not_exploitable."""
    if not result.get("safe"):
        return "not_exploitable"

    stdout = (result.get("stdout") or "").lower()
    evidence = (result.get("evidence") or "").lower()
    combined = f"{stdout}\n{evidence}"

    strong_markers = [
        "root:x:0:0",
        "cve-",
        "vulnerable",
        "sql syntax",
        "syntax error",
        "reflected",
        "xss",
        "directory traversal",
        "path traversal",
    ]

    if any(marker in combined for marker in strong_markers):
        return "confirmed"

    if result.get("returncode") == 0 and evidence:
        return "probable"

    return "not_exploitable"


# ---------------------------------------------------------------------------
# Service/version extraction
# ---------------------------------------------------------------------------
def extract_nmap_services(raw_nmap_output: str) -> list[dict]:
    """
    Extract open ports with version info from Nmap plain-text output.

    Returns list of dicts:
        {"port": "443", "protocol": "tcp", "service": "http", "product": "Apache httpd", "version": "2.4.49"}
    """
    results = []
    if not raw_nmap_output:
        return results

    # Matches lines like:
    # 80/tcp  open  http    Apache httpd 2.4.49
    # 443/tcp open  ssl/http Apache httpd 2.4.49
    pattern = re.compile(
        r"^\s*(?P<port>\d+)/(?P<protocol>tcp|udp)\s+open\s+"
        r"(?P<service>\S+)\s+(?P<product>.*?)(?P<version>\d+(?:\.\d+)+)?\s*$"
    )
    for line in raw_nmap_output.splitlines():
        m = pattern.search(line)
        if not m:
            continue

        port = m.group("port")
        protocol = m.group("protocol")
        service = m.group("service")
        product = (m.group("product") or "").strip()
        version = m.group("version")

        # Skip empty/noise product strings
        if not product or product.lower() in ("unknown", "unrecognized service"):
            continue

        results.append({
            "port": port,
            "protocol": protocol,
            "service": service,
            "product": product,
            "version": version,
        })

    return results


def extract_technologies(technology_lines: list[str]) -> list[dict]:
    """
    Extract product/version tokens from JARVIS technology strings.

    Example:
        "Server: Apache" -> {"service": "http", "product": "Apache", "version": None}
        "X-Powered-By: PHP/7.4.3" -> {"service": "http", "product": "PHP", "version": "7.4.3"}
    """
    results = []
    for line in technology_lines or []:
        line = str(line).strip()
        if ":" not in line:
            # Could be a plain product name like "WordPress"
            if line.lower() in ("wordpress", "joomla", "drupal"):
                results.append({"service": "web", "product": line, "version": None})
            continue

        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()

        if "/" in value:
            product, _, version = value.rpartition("/")
            product = product.strip()
            version = version.strip() or None
        else:
            product = value
            version = None

        if product:
            results.append({
                "service": key.replace("x-powered-by", "http"),
                "product": product,
                "version": version,
            })

    return results


# ---------------------------------------------------------------------------
# NVD CVE lookup
# ---------------------------------------------------------------------------
def _query_nvd_cves(keyword: str, max_results: int = 20) -> list[dict]:
    """
    Query the NVD CVE API for CVEs matching a keyword.

    Returns a list of relevant CVE items with:
        cve_id, description, cvss_score, severity, published_date
    """
    if not keyword:
        return []

    try:
        params = {
            "keywordSearch": keyword,
            "resultsPerPage": max_results,
        }
        headers = {"User-Agent": "JARVIS-Agent"}
        resp = requests.get(
            NVD_API_URL,
            params=params,
            headers=headers,
            timeout=NVD_TIMEOUT,
            proxies=get_requests_proxies(),
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        vulns = data.get("vulnerabilities", [])

        cve_items = []
        for vuln in vulns:
            cve = vuln.get("cve", {})
            cve_id = cve.get("id", "")
            descriptions = cve.get("descriptions", [])
            desc_text = ""
            for d in descriptions:
                if d.get("lang") == "en":
                    desc_text = d.get("value", "")
                    break
            if not desc_text:
                desc_text = descriptions[0].get("value", "") if descriptions else ""

            metrics = cve.get("metrics", {})
            severity = "UNKNOWN"
            score = 0.0
            for metric_key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
                metric_list = metrics.get(metric_key, [])
                if metric_list:
                    base_metric = metric_list[0].get("cvssData", {})
                    severity = base_metric.get("baseSeverity", "UNKNOWN")
                    score = base_metric.get("baseScore", 0.0)
                    break

            published = cve.get("published", "")

            cve_items.append({
                "cve_id": cve_id,
                "description": desc_text[:400],
                "cvss_score": score,
                "severity": severity,
                "published_date": published,
            })

        return cve_items

    except Exception as e:
        print(f"[AttackChain] ⚠️ NVD query failed for '{keyword}': {e}")
        return []


# ---------------------------------------------------------------------------
# PoC generation
# ---------------------------------------------------------------------------
def _generate_poc_commands(
    service: str,
    product: str,
    version: str | None,
    cve_ids: list[str],
) -> list[str]:
    """
    Generate safe, read-only PoC commands for a given service/version.

    Uses the cloud model router. Returns max 3 curl/nuclei/nmap commands.
    """
    if not cve_ids:
        return []

    target = f"{service} {product} {version or ''}".strip()
    prompt = f"""
You are an authorised penetration tester.

Target service: {target}
Relevant CVEs: {', '.join(cve_ids[:3])}

Generate up to 3 SAFE READ-ONLY commands that could help validate whether
this service is vulnerable on a target URL/domain.

Rules:
- Return only commands.
- Prefer curl, nuclei, or nmap scripts.
- Do NOT use destructive exploits.
- One command per line.
"""
    try:
        router = ModelRouter()
        response = router.generate(
            prompt=prompt,
            system="You are an elite penetration tester. Return only safe validation commands.",
            temperature=0.2,
            max_tokens=500,
        )
        if not response.get("success"):
            return []

        lines = []
        for line in response["text"].splitlines():
            line = line.strip()
            if line and (line.startswith("curl") or line.startswith("nuclei") or line.startswith("nmap")):
                lines.append(line)
            if len(lines) >= 3:
                break
        return lines

    except Exception as e:
        print(f"[AttackChain] ⚠️ PoC generation failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Main correlation API
# ---------------------------------------------------------------------------
def correlate_vulnerabilities(target: str, results: dict) -> dict:
    """
    Correlate discovered services and versions with known CVEs.

    Returns:
        {
            "attack_chains": [
                {
                    "service": ...,
                    "product": ...,
                    "version": ...,
                    "port": ...,
                    "cves": [...],
                    "poc_commands": [...]
                }
            ],
            "correlated_findings": [
                "Apache httpd 2.4.49 on port 80: CVE-2021-41773 (Path Traversal) [HIGH]"
            ]
        }
    """
    chains = []
    findings = []

    # 1. Extract from Nmap raw output if available
    raw_nmap = results.get("nmap_raw", "") or results.get("nmap_output", "")
    nmap_services = extract_nmap_services(raw_nmap)

    # 2. Extract from technology lines
    tech_services = extract_technologies(results.get("technologies", []))

    # Merge, avoiding exact duplicates
    seen = set()
    service_list = []
    for svc in nmap_services + tech_services:
        key = (
            svc.get("service", ""),
            svc.get("product", ""),
            svc.get("version", ""),
            svc.get("port", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        service_list.append(svc)

    for svc in service_list:
        product = svc.get("product", "")
        version = svc.get("version")
        service_name = svc.get("service", "")

        if not product:
            continue

        # Build NVD keyword
        keyword = f"{product} {version}" if version else product

        cves = _query_nvd_cves(keyword, max_results=15)

        # Filter to top relevant CVEs
        current_year = datetime.now().year
        relevant_cves = []
        for cve in cves:
            sev = cve.get("severity", "UNKNOWN")
            pub_year = 0
            try:
                pub_year = int(str(cve.get("published_date", "0"))[:4])
            except Exception:
                pub_year = 0

            # When no version is known, only keep recent high/critical CVEs
            if not version:
                if sev not in ("CRITICAL", "HIGH"):
                    continue
                if pub_year and pub_year < current_year - 10:
                    continue

            relevant_cves.append(cve)

        # Sort by severity + CVSS score
        severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "UNKNOWN": 4}
        relevant_cves.sort(
            key=lambda c: (
                severity_order.get(c.get("severity", "UNKNOWN"), 4),
                -float(c.get("cvss_score", 0.0)),
            )
        )
        relevant_cves = relevant_cves[:MAX_CVES_PER_SERVICE]

        if not relevant_cves:
            continue

        cve_ids = [cve["cve_id"] for cve in relevant_cves]
        poc_commands = _generate_poc_commands(service_name, product, version, cve_ids)

        chain = {
            "service": service_name,
            "product": product,
            "version": version,
            "port": svc.get("port"),
            "cves": relevant_cves,
            "poc_commands": poc_commands,
        }
        chains.append(chain)

        findings.extend(
            f"{product}{' ' + version if version else ''} on port {svc.get('port', 'unknown')}: "
            f"{cve['cve_id']} ({cve['description'][:100]}) [{cve['severity']}]"
            for cve in relevant_cves[:3]
        )

    return {
        "attack_chains": chains,
        "correlated_findings": findings,
    }

def execute_attack_chains(chains: list[dict], target: str = "", timeout: int = 20) -> list[dict]:
    """
    Execute safe read-only PoC commands from discovered attack chains.

    If a chain has no PoC commands, a default safe header check is used.
    """
    from core.poc_executor import execute_many

    evidence_items = []

    for chain in chains or []:
        product = chain.get("product", "")
        version = chain.get("version", "")
        cve_ids = [cve.get("cve_id", "") for cve in chain.get("cves", [])[:3]]

        commands = chain.get("poc_commands") or []
        if not commands and target:
            commands = [f"curl -I https://{target}"]

        for command in commands:
            result = execute_many([command], timeout=timeout)[0]
            result["product"] = product
            result["version"] = version
            result["cve_ids"] = cve_ids
            result["verdict"] = _classify_evidence(result)
            evidence_items.append(result)

    return evidence_items