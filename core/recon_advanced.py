"""
core/recon_advanced.py — Advanced recon for JARVIS.

Adds subdomain takeover checks and common cloud asset discovery.

Read-only network checks. No destructive actions.
"""

from __future__ import annotations

import socket
from typing import Optional

import requests

from core.proxy_manager import get_requests_proxies


# Known dangling CNAME fingerprints -> service name
TAKEOVER_SIGNATURES = {
    "github.io": "GitHub Pages",
    "github.com": "GitHub Pages",
    "amazonaws.com": "AWS S3",
    "s3.amazonaws.com": "AWS S3",
    "azurewebsites.net": "Azure App Service",
    "cloudapp.azure.com": "Azure Cloud App",
    "trafficmanager.net": "Azure Traffic Manager",
    "herokuapp.com": "Heroku",
    "vercel-dns.com": "Vercel",
    "netlify.app": "Netlify",
    "ghost.io": "Ghost",
    "shopify.com": "Shopify",
    "readme.io": "Readme",
    "bitbucket.io": "Bitbucket",
    "surge.sh": "Surge",
    "zendesk.com": "Zendesk",
    "helpscoutdocs.com": "Help Scout",
    "fastly.net": "Fastly",
    "pantheonsite.io": "Pantheon",
}

# Common cloud bucket/asset patterns
CLOUD_ASSET_PATTERNS = [
    "https://{domain}.s3.amazonaws.com",
    "https://s3.amazonaws.com/{domain}",
    "https://{domain}.s3.us-east-1.amazonaws.com",
    "https://{domain}.blob.core.windows.net",
    "https://{domain}.file.core.windows.net",
    "https://{domain}.azurewebsites.net",
    "https://{domain}.cloudapp.azure.com",
    "https://{domain}.firebaseio.com",
    "https://{domain}.appspot.com",
    "https://{domain}.gitlab.io",
    "https://{domain}.github.io",
]


def _dns_cname(domain: str) -> Optional[str]:
    """Resolve a CNAME for a domain, returning None if not found."""
    try:
        answers = socket.gethostbyname_ex(domain)
    except Exception:
        return None

    # gethostbyname_ex returns CNAME in aliases when present.
    for alias in answers[1]:
        alias_lower = alias.lower().rstrip(".")
        if alias_lower and alias_lower != domain.lower().rstrip("."):
            return alias_lower
    return None


def _dns_resolves(domain: str) -> bool:
    try:
        socket.gethostbyname(domain)
        return True
    except Exception:
        return False


def check_subdomain_takeover(subdomains: list[str]) -> list[dict]:
    """
    Check a list of subdomains for possible takeover.

    A candidate is reported if it has a CNAME pointing to a known service
    but the CNAME target no longer resolves.
    """
    findings = []

    for sub in subdomains or []:
        domain = sub.strip().lower().rstrip(".")
        if not domain:
            continue

        cname = _dns_cname(domain)
        if not cname:
            continue

        service = None
        for signature, svc_name in TAKEOVER_SIGNATURES.items():
            if signature in cname:
                service = svc_name
                break

        if not service:
            continue

        # If the target CNAME does not resolve, it may be unclaimed.
        if not _dns_resolves(cname.rstrip(".")):
            findings.append({
                "subdomain": domain,
                "cname": cname,
                "service": service,
                "status": "possible_takeover",
            })
        else:
            findings.append({
                "subdomain": domain,
                "cname": cname,
                "service": service,
                "status": "resolves",
            })

    return findings


def check_cloud_assets(domain: str) -> list[dict]:
    """
    Probe common cloud asset URLs for existence.

    Returns a list of assets that respond with non-404 status.
    """
    results = []

    for pattern in CLOUD_ASSET_PATTERNS:
        url = pattern.format(domain=domain)
        try:
            resp = requests.get(
                url,
                timeout=8,
                allow_redirects=False,
                headers={"User-Agent": "Mozilla/5.0"},
                proxies=get_requests_proxies(),
            )
            if resp.status_code in (200, 301, 302, 307, 308, 401, 403):
                results.append({
                    "url": url,
                    "status_code": resp.status_code,
                    "server": resp.headers.get("Server", ""),
                })
        except Exception:
            continue

    return results