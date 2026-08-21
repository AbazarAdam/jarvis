"""
core/threat_intel.py — Live threat intelligence feeds for JARVIS.

Fetches current vulnerability and exploit data from:

    - CISA Known Exploited Vulnerabilities (KEV)
    - GitHub Security Advisories
    - NVD CVE API

All requests respect the configured proxy. Data is returned as simple dicts
for integration with attack_chain.py and security_mode.py.

No local LLM is used.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

import requests

from core.proxy_manager import get_requests_proxies


CISA_KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
GITHUB_ADVISORIES_URL = "https://api.github.com/advisories"
NVD_CVE_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"


def _safe_get(url: str, params: dict | None = None, timeout: int = 20) -> dict | list | None:
    """GET JSON from a live threat feed, returning None on any failure."""
    try:
        resp = requests.get(
            url,
            params=params,
            headers={
                "User-Agent": "JARVIS",
                "Accept": "application/json",
            },
            timeout=timeout,
            proxies=get_requests_proxies(),
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def fetch_cisa_kev(max_results: int = 50) -> list[dict]:
    """
    Fetch the CISA Known Exploited Vulnerabilities catalogue.

    Returns a list of dicts:
        {
            "cve_id": str,
            "vendor": str,
            "product": str,
            "vulnerability_name": str,
            "date_added": str,
            "due_date": str,
            "ransomware_known": bool
        }
    """
    data = _safe_get(CISA_KEV_URL)
    if not isinstance(data, dict):
        return []

    vulnerabilities = data.get("vulnerabilities", [])
    results = []

    for item in vulnerabilities[:max_results]:
        results.append({
            "cve_id": item.get("cveID", ""),
            "vendor": item.get("vendorProject", ""),
            "product": item.get("product", ""),
            "vulnerability_name": item.get("vulnerabilityName", ""),
            "date_added": item.get("dateAdded", ""),
            "due_date": item.get("dueDate", ""),
            "ransomware_known": bool(item.get("knownRansomwareCampaignUse", "")),
        })

    return results


def fetch_github_advisories_by_cve(cve_ids: list[str], max_results: int = 10) -> list[dict]:
    """
    Fetch GitHub Security Advisories for specific CVE IDs.

    Returns a list of dicts:
        {
            "ghsa_id": str,
            "cve_id": str,
            "summary": str,
            "severity": str,
            "published_at": str,
            "url": str
        }
    """
    if not cve_ids:
        return []

    results = []

    for cve_id in cve_ids[:max_results]:
        data = _safe_get(
            GITHUB_ADVISORIES_URL,
            params={"cve_id": cve_id},
            timeout=20,
        )
        if not isinstance(data, list):
            continue

        for advisory in data[:3]:
            results.append({
                "ghsa_id": advisory.get("ghsa_id", ""),
                "cve_id": advisory.get("cve_id", cve_id),
                "summary": advisory.get("summary", ""),
                "severity": advisory.get("severity", ""),
                "published_at": advisory.get("published_at", ""),
                "url": advisory.get("html_url", ""),
            })

    return results


def fetch_nvd_cves(keyword: str, max_results: int = 20) -> list[dict]:
    """
    Fetch current CVEs from the NVD API.

    Returns a list of dicts:
        {
            "cve_id": str,
            "description": str,
            "cvss_score": float,
            "severity": str,
            "published_date": str
        }
    """
    if not keyword:
        return []

    data = _safe_get(
        NVD_CVE_URL,
        params={
            "keywordSearch": keyword,
            "resultsPerPage": max_results,
        },
        timeout=20,
    )
    if not isinstance(data, dict):
        return []

    results = []
    for vuln in data.get("vulnerabilities", []):
        cve = vuln.get("cve", {})
        cve_id = cve.get("id", "")

        descriptions = cve.get("descriptions", [])
        desc_text = ""
        for d in descriptions:
            if d.get("lang") == "en":
                desc_text = d.get("value", "")
                break

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

        results.append({
            "cve_id": cve_id,
            "description": desc_text[:500],
            "cvss_score": score,
            "severity": severity,
            "published_date": cve.get("published", ""),
        })

    return results


def fetch_live_threat_intel(products: list[str], max_cves_per_product: int = 5) -> dict:
    """
    Fetch current threat intel for a list of products.

    Returns:
        {
            "products": {product: [cve dicts]},
            "cisa_kev": [cve dicts],
            "github_advisories": [advisory dicts],
            "correlated_cve_ids": [str],
        }
    """
    cisa_items = fetch_cisa_kev()
    product_cves: dict[str, list[dict]] = {}
    all_cve_ids: list[str] = []

    for product in products:
        cves = fetch_nvd_cves(product, max_cves_per_product)
        product_cves[product] = cves
        all_cve_ids.extend(cve.get("cve_id", "") for cve in cves)

    gh_advisories = fetch_github_advisories_by_cve(all_cve_ids, max_results=10)

    return {
        "products": product_cves,
        "cisa_kev": cisa_items,
        "github_advisories": gh_advisories,
        "correlated_cve_ids": all_cve_ids,
    }