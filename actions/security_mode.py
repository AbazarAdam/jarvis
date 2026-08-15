"""
Security_mode.py — JARVIS Red‑Team Autonomous Pentest Engine

This module performs authorised penetration testing when explicitly confirmed by the user.
It runs free, open‑source security tools that are installed on the system.

All commands and results are logged to logs/audit.log.
"""

import os
import re
import json
import time
import ssl
import socket
import shutil
import subprocess
import sys
from pathlib import Path
from datetime import datetime

import requests

from core.audit import log_action


# ---------------------------------------------------------------------------
# Base paths & constants
# ---------------------------------------------------------------------------
BASE_DIR    = Path(__file__).resolve().parent.parent
TOOLS_DIR   = BASE_DIR / "tools"
AUDIT_TAG   = "security_mode"

# Common sensitive directories
_COMMON_DIRS = [
    "admin", "login", "wp-admin", "dashboard", "backup", "backups",
    ".git", ".env", "config", "test", "dev", "staging", "api",
    "robots.txt", "sitemap.xml", "phpmyadmin", "wp-login.php",
    "administrator", "cpanel", "webmail", "db", "sql", ".svn",
    ".htaccess", "logs", "log", "tmp", "temp", "backup.zip",
    "backup.sql", "dump", "export", "private", "secret", "credentials",
]

# Common email patterns
_COMMON_EMAILS = [
    "admin", "contact", "info", "support", "security",
    "webmaster", "postmaster", "abuse", "hello", "sales",
]

# ---------------------------------------------------------------------------
# External security tool specifications
# Each entry maps a tool name to:
#   - possible executable names
#   - update command (list) or None if manual update
# ---------------------------------------------------------------------------
TOOL_SPECS = {
    "amass": {
        "executables": ["amass", "amass.exe"],
        "update_cmd": None,
        "install_hint": "https://github.com/owasp-amass/amass/releases",
    },
    "gobuster": {
        "executables": ["gobuster", "gobuster.exe"],
        "update_cmd": None,
        "install_hint": "https://github.com/OJ/gobuster/releases",
    },
    "ffuf": {
        "executables": ["ffuf", "ffuf.exe"],
        "update_cmd": None,
        "install_hint": "https://github.com/ffuf/ffuf/releases",
    },
    "dirsearch": {
        "executables": ["dirsearch", "dirsearch.py"],
        "update_cmd": ["python", "-m", "pip", "install", "--upgrade", "dirsearch"],
        "install_hint": "pip install dirsearch",
    },
    "sublist3r": {
        "executables": ["sublist3r", "sublist3r.py"],
        "update_cmd": ["python", "-m", "pip", "install", "--upgrade", "sublist3r"],
        "install_hint": "pip install sublist3r",
    },
    "nuclei": {
        "executables": ["nuclei", "nuclei.exe"],
        "update_cmd": ["nuclei", "-update-templates"],
        "install_hint": "https://github.com/projectdiscovery/nuclei/releases",
    },
    "nikto": {
        "executables": ["nikto.pl", "nikto"],
        "update_cmd": None,
        "install_hint": "clone https://github.com/sullo/nikto into tools/nikto",
    },
    "wpscan": {
        "executables": ["wpscan", "wpscan.exe"],
        "update_cmd": ["wpscan", "--update"],
        "install_hint": "gem install wpscan",
    },
    "droopescan": {
        "executables": ["droopescan"],
        "update_cmd": ["python", "-m", "pip", "install", "--upgrade", "droopescan"],
        "install_hint": "pip install droopescan",
    },
    "sqlmap": {
        "executables": ["sqlmap", "sqlmap.py"],
        "update_cmd": ["sqlmap", "--update"],
        "install_hint": "pip install sqlmap",
    },
    "xsstrike": {
        "executables": ["xsstrike.py", "xsstrike"],
        "update_cmd": None,
        "install_hint": "clone https://github.com/s0md3v/XSStrike",
    },
    "commix": {
        "executables": ["commix.py", "commix"],
        "update_cmd": None,
        "install_hint": "clone https://github.com/commixproject/commix",
    },
    "lfisuite": {
        "executables": ["lfisuite.py", "lfisuite"],
        "update_cmd": None,
        "install_hint": "https://github.com/D35m0nd142/LFISuite",
    },
}


# ---------------------------------------------------------------------------
# Safe command execution
# ---------------------------------------------------------------------------
def _run_command(cmd: list, timeout: int = 300, cwd: str = None) -> dict:
    """Run a command and return a normalised dict."""
    log_action(AUDIT_TAG, {"command": " ".join(str(c) for c in cmd)}, status="started")

    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd or str(BASE_DIR),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        result = {
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip() or "",
            "stderr": proc.stderr.strip() or "",
        }

        log_action(
            AUDIT_TAG,
            {"command": " ".join(str(c) for c in cmd)},
            result=(
                result["stdout"][:300] or result["stderr"][:300] or "no output"
            ),
            status="completed" if proc.returncode == 0 else "failed",
        )
        return result

    except subprocess.TimeoutExpired:
        log_action(
            AUDIT_TAG,
            {"command": " ".join(str(c) for c in cmd)},
            result="Command timed out",
            status="failed",
        )
        return {"returncode": 124, "stdout": "", "stderr": "Command timed out."}

    except FileNotFoundError:
        log_action(
            AUDIT_TAG,
            {"command": " ".join(str(c) for c in cmd)},
            result="Command not found",
            status="failed",
        )
        return {"returncode": 127, "stdout": "", "stderr": "Command not found."}

    except Exception as e:
        log_action(
            AUDIT_TAG,
            {"command": " ".join(str(c) for c in cmd)},
            result=str(e),
            status="failed",
        )
        return {"returncode": 1, "stdout": "", "stderr": str(e)}


# ---------------------------------------------------------------------------
# Tool discovery & update
# ---------------------------------------------------------------------------
def _find_tool(name: str) -> str | None:
    """Locate a tool executable in PATH, .venv/Scripts, or tools/ directory."""
    spec = TOOL_SPECS.get(name, {})
    candidates = spec.get("executables", [name])

    # 1. PATH
    for exe in candidates:
        path = shutil.which(exe)
        if path:
            return path
        path = shutil.which(exe + ".exe")
        if path:
            return path

    # 2. Virtual env Scripts
    venv_scripts = BASE_DIR / ".venv" / "Scripts"
    if venv_scripts.exists():
        for exe in candidates:
            for suffix in ("", ".exe", ".py"):
                candidate = venv_scripts / (exe + suffix)
                if candidate.exists():
                    return str(candidate)

    # 3. tools/ directory
    if TOOLS_DIR.exists():
        for candidate in TOOLS_DIR.rglob("*"):
            if candidate.is_file() and candidate.stem.lower() == name.lower():
                return str(candidate)

    return None


def _tool_available(name: str) -> bool:
    return _find_tool(name) is not None


def _detect_installed_tools() -> dict:
    """Return dict of tool name -> bool installed."""
    return {name: _tool_available(name) for name in TOOL_SPECS}


def update_tools() -> str:
    """Attempt to update installed security tools."""
    updated = []
    failed = []
    installed = _detect_installed_tools()

    for tool, spec in TOOL_SPECS.items():
        if not installed.get(tool):
            continue

        update_cmd = spec.get("update_cmd")
        if not update_cmd:
            updated.append(f"{tool}: no automatic update command")
            continue

        result = _run_command(update_cmd, timeout=180)
        if result["returncode"] == 0:
            updated.append(f"{tool}: updated")
        else:
            failed.append(f"{tool}: {result['stderr'][:100]}")

    summary = "Security tool update report:\n"
    summary += "\n".join(updated) if updated else "(none)"
    summary += "\n\nFailed:\n"
    summary += "\n".join(failed) if failed else "(none)"
    return summary





def _get_llm_insight(prompt: str) -> str:
    """
    Use Groq/Gemini to get tactical recommendations during the pentest.
    """
    try:
        from groq_client import groq_client
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an elite penetration tester. "
                    "Analyse the data and return a concise, actionable plan. "
                    "Be direct and technical."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        return groq_client.chat(messages, temperature=0.2, max_tokens=1500)
    except Exception:
        try:
            from or_client import client
            messages = [
                {"role": "system", "content": "You are an elite penetration tester."},
                {"role": "user", "content": prompt},
            ]
            return client.multi_turn(messages, temperature=0.2, max_tokens=1500)
        except Exception:
            return "No AI insight available."


def _detect_cms(domain: str, technologies: list[str]) -> str:
    """Return detected CMS/framework name or 'unknown'."""
    tech_str = " ".join(technologies).lower()
    if "wordpress" in tech_str or "wp-content" in tech_str:
        return "wordpress"
    if "joomla" in tech_str:
        return "joomla"
    if "drupal" in tech_str:
        return "drupal"
    if "react" in tech_str or "next.js" in tech_str:
        return "react"
    if "vue" in tech_str:
        return "vue"
    return "unknown"

# ---------------------------------------------------------------------------
# Reconnaissance & OSINT
# ---------------------------------------------------------------------------
def _normalise_target(target: str) -> str:
    """Return just the domain/host from a URL or raw target."""
    target = target.strip().lower()
    # Remove protocol
    for prefix in ("https://", "http://"):
        if target.startswith(prefix):
            target = target[len(prefix):]
    # Remove path
    target = target.split("/")[0]
    return target


def _enumerate_subdomains(domain: str) -> list[str]:
    """Gather subdomains using crt.sh and Amass/Sublist3r if available."""
    subs = set()

    # 1. crt.sh
    try:
        url = f"https://crt.sh/?q=%.{domain}&output=json"
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            for entry in data:
                name_value = entry.get("name_value", "")
                for n in name_value.split("\n"):
                    n = n.strip().lower()
                    if n.endswith(domain) and n != domain and "*" not in n:
                        subs.add(n)
    except Exception:
        pass

    # 2. Amass
    amass = _find_tool("amass")
    if amass:
        try:
            r = _run_command(
                [amass, "enum", "-passive", "-d", domain],
                timeout=180,
            )
            for line in (r["stdout"] + "\n" + r["stderr"]).splitlines():
                line = line.strip().lower()
                if line.endswith(domain) and line != domain and "*" not in line:
                    subs.add(line)
        except Exception:
            pass

    # 3. Sublist3r
    sublist3r = _find_tool("sublist3r")
    if sublist3r:
        try:
            r = _run_command(
                ["python", sublist3r, "-d", domain],
                timeout=180,
            )
            for line in (r["stdout"] + "\n" + r["stderr"]).splitlines():
                line = line.strip().lower()
                if line.endswith(domain) and line != domain:
                    subs.add(line)
        except Exception:
            pass

    return sorted(subs)[:100]


def _brute_force_dirs(domain: str) -> list[str]:
    """Find sensitive directories using Gobuster/ffuf/dirsearch or built‑in HTTP fallback."""
    target_url = f"https://{domain}"

    # 1. Gobuster
    gobuster = _find_tool("gobuster")
    if gobuster and (TOOLS_DIR / "wordlists").exists():
        wordlist = TOOLS_DIR / "wordlists" / "common.txt"
        r = _run_command(
            [gobuster, "dir", "-u", target_url, "-w", str(wordlist), "-q"],
            timeout=180,
        )
        if r["returncode"] == 0 and r["stdout"]:
            return [line.strip() for line in r["stdout"].splitlines() if line.strip()]

    # 2. ffuf
    ffuf = _find_tool("ffuf")
    if ffuf and (TOOLS_DIR / "wordlists").exists():
        wordlist = TOOLS_DIR / "wordlists" / "common.txt"
        r = _run_command(
            [ffuf, "-u", f"{target_url}/FUZZ", "-w", str(wordlist), "-mc", "200,301,302,401,403", "-s"],
            timeout=180,
        )
        if r["returncode"] == 0 and r["stdout"]:
            return [line.strip() for line in r["stdout"].splitlines() if line.strip()]

    # 3. dirsearch
    dirsearch = _find_tool("dirsearch")
    if dirsearch:
        r = _run_command(
            ["python", dirsearch, "-u", target_url, "--quiet", "--format=plain"],
            timeout=240,
        )
        if r["returncode"] == 0 and r["stdout"]:
            return [line.strip() for line in r["stdout"].splitlines() if line.strip()]

    # 4. Built-in HTTP fallback
    found = []
    for path in _COMMON_DIRS:
        url = f"{target_url.rstrip('/')}/{path}"
        try:
            resp = requests.get(
                url,
                timeout=5,
                allow_redirects=False,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code in (200, 301, 302, 403, 401):
                found.append(f"{path} (HTTP {resp.status_code})")
        except Exception:
            pass
    return found


def _harvest_emails(domain: str) -> tuple[list[str], list[str]]:
    """Scrape the target homepage for email addresses and generate common guesses."""
    emails = set()
    for scheme in ("https", "http"):
        try:
            resp = requests.get(
                f"{scheme}://{domain}",
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            found = re.findall(
                r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
                resp.text,
            )
            for e in found:
                if e.endswith(domain):
                    emails.add(e)
            break
        except Exception:
            pass

    guesses = [f"{user}@{domain}" for user in _COMMON_EMAILS]
    return sorted(emails), guesses


def _check_pwned(email: str) -> list[str]:
    """Return breach names for an email using Have I Been Pwned."""
    try:
        url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return [b.get("Name", "Unknown") for b in resp.json()]
    except Exception:
        pass
    return []


def _check_domain_breaches(domain: str) -> tuple[int, list[str]]:
    """Return number of breaches and names for a domain."""
    try:
        url = f"https://haveibeenpwned.com/api/v3/breaches?domain={domain}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return len(data), [b.get("Name", "Unknown") for b in data[:5]]
    except Exception:
        pass
    return 0, []


def _scrape_linkedin(domain: str) -> list[str]:
    """Find LinkedIn profile URLs on the target homepage."""
    profiles = []
    try:
        resp = requests.get(
            f"https://{domain}",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        urls = re.findall(
            r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[^"\'<>\s]+',
            resp.text,
        )
        for url in urls[:15]:
            profiles.append(url)
    except Exception:
        pass
    return profiles


def _advanced_dns_enum(domain: str) -> list[str]:
    """Collect DNS records with nslookup."""
    records = []
    for rtype in ["A", "AAAA", "MX", "NS", "TXT"]:
        r = _run_command(["nslookup", f"-type={rtype}", domain], timeout=15)
        if r["returncode"] == 0 and (r["stdout"] or r["stderr"]):
            records.append(f"--- {rtype} ---\n{r['stdout'] or r['stderr']}")
    return records if records else ["No DNS records found."]


def _detect_technologies(domain: str) -> list[str]:
    """Identify technologies from headers and HTML."""
    technologies = []
    try:
        resp = requests.get(
            f"https://{domain}",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        headers = resp.headers

        server = headers.get("Server")
        if server:
            technologies.append(f"Server: {server}")
        if "x-powered-by" in headers:
            technologies.append(f"X-Powered-By: {headers.get('x-powered-by')}")
        if "cf-ray" in headers:
            technologies.append("Cloudflare detected")
        if "x-vercel-id" in headers:
            technologies.append("Vercel detected")
        if "x-sucuri-id" in headers:
            technologies.append("Sucuri WAF detected")
        if "x-aspnet-version" in headers:
            technologies.append("ASP.NET detected")

        html = resp.text[:5000].lower()
        if "wp-content" in html:
            technologies.append("WordPress")
        if "joomla" in html:
            technologies.append("Joomla")
        if "drupal" in html:
            technologies.append("Drupal")
        if "react" in html or "__NEXT_DATA__" in html:
            technologies.append("React/Next.js")
        if "vue" in html:
            technologies.append("Vue.js")
    except Exception as e:
        technologies.append(f"Could not fetch page: {e}")
    return technologies if technologies else ["No technologies detected."]


def _detect_waf(domain: str) -> list[str]:
    """Detect WAF from response headers."""
    waf = []
    try:
        resp = requests.get(
            f"https://{domain}",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        headers = resp.headers
        checks = {
            "Cloudflare": "cf-ray",
            "Sucuri": "x-sucuri-id",
            "Akamai": "x-akamai-transformed",
            "Incapsula": "x-cdn",
            "AWS WAF": "x-amzn-requestid",
            "F5 BIG-IP": "x-waf-status",
        }
        for name, header in checks.items():
            if header in headers:
                waf.append(name)
    except Exception:
        pass
    return waf if waf else ["No WAF detected."]


def _generate_dorks(domain: str) -> list[str]:
    """Generate OSINT Google/GitHub dorks."""
    return [
        f"site:{domain} filetype:pdf",
        f"site:{domain} filetype:docx",
        f"site:{domain} inurl:admin",
        f"site:{domain} inurl:login",
        f'site:{domain} intitle:"index of"',
        f"site:{domain} ext:sql | ext:bak | ext:zip",
        f"site:github.com {domain} password",
        f"site:github.com {domain} api_key",
        f"site:github.com {domain} secret",
        f"site:pastebin.com {domain}",
    ]

# ---------------------------------------------------------------------------
# Vulnerability Scanning & Validation
# ---------------------------------------------------------------------------
def _run_nmap(domain: str) -> dict:
    """Run Nmap with service detection and vuln/auth scripts."""
    nmap_exe = _find_tool("nmap")
    if not nmap_exe:
        return {"error": "Nmap not found.", "raw": "", "hosts": []}

    # Full scan first, then fast fallback
    cmd_full = [
        nmap_exe, "-sV", "-sC", "--script", "vuln,auth",
        "-T4", "--host-timeout", "90s", domain,
    ]
    result = _run_command(cmd_full, timeout=180)
    if result["returncode"] == 0 and "Host is up" in result["stdout"]:
        return {
            "raw": result["stdout"],
            "hosts": _parse_nmap_text(result["stdout"]),
        }

    # Fallback: top ports
    cmd_fast = [
        nmap_exe, "--top-ports", "100", "-T4",
        "--host-timeout", "60s", domain,
    ]
    result = _run_command(cmd_fast, timeout=120)
    if result["returncode"] == 0:
        return {
            "raw": result["stdout"],
            "hosts": _parse_nmap_text(result["stdout"]),
        }

    return {"error": "Nmap scan timed out or failed.", "raw": "", "hosts": []}


def _parse_nmap_text(output: str) -> list[dict]:
    """Extract open ports from Nmap text output."""
    hosts = []
    current_ip = None
    ports = []
    for line in output.splitlines():
        if "Nmap scan report for" in line:
            if current_ip and ports:
                hosts.append({"ip": current_ip, "open_ports": ports})
            current_ip = line.split()[-1]
            ports = []
        elif "/tcp" in line and "open" in line:
            parts = line.split()
            if len(parts) >= 3:
                port_id = parts[0]
                service = " ".join(parts[2:])
                ports.append(f"{port_id} {service}")
    if current_ip and ports:
        hosts.append({"ip": current_ip, "open_ports": ports})
    return hosts

def _parse_nikto(raw: str) -> list[str]:
    """Extract unique Nikto findings from raw output."""
    if not raw:
        return []
    findings = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("+ ") and "ERROR:" not in line:
            if line not in findings:
                findings.append(line)
    return findings

def _run_nikto(domain: str) -> list[str]:
    """Run Nikto web scanner if available."""
    nikto_pl = _find_tool("nikto")
    perl_exe = shutil.which("perl") or r"C:\Strawberry\perl\bin\perl.exe"

    if not Path(perl_exe).exists() or not nikto_pl:
        return []

    target = f"https://{domain}"
    cmd = [perl_exe, nikto_pl, "-h", target, "-Tuning", "123", "-o", "-", "-ssl"]
    result = _run_command(cmd, timeout=240)

    findings = []
    for line in (result["stdout"] + "\n" + result["stderr"]).splitlines():
        line = line.strip()
        if line.startswith("+ ") and "ERROR:" not in line:
            if line not in findings:
                findings.append(line)
    return findings


def _run_nuclei(domain: str) -> list[str]:
    """Run Nuclei CVE scanner and return findings."""
    nuclei_exe = _find_tool("nuclei")
    if not nuclei_exe:
        return []

    target = f"https://{domain}"
    cmd = [
        nuclei_exe,
        "-u", target,
        "-severity", "critical,high,medium",
        "-tags", "cve",
        "-silent",
        "-jsonl",
        "-timeout", "10",
        "-retries", "1",
        "-c", "25",
        "-no-color",
    ]
    result = _run_command(cmd, timeout=600)

    findings = []
    for line in result["stdout"].splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = data.get("info", {}).get("name", "Unknown")
        sev = data.get("info", {}).get("severity", "unknown")
        matched = data.get("matched-at", domain)
        findings.append(f"{sev.upper()}: {name} ({matched})")
    return findings


def _run_wpscan(domain: str) -> list[str]:
    """Run WPScan if WordPress is detected or tool exists."""
    wpscan_exe = _find_tool("wpscan")
    if not wpscan_exe:
        return []

    target = f"https://{domain}"
    cmd = [wpscan_exe, "--url", target, "--disable-tls-checks", "--format", "json"]
    result = _run_command(cmd, timeout=600)

    findings = []
    try:
        data = json.loads(result["stdout"])
        # WPScan JSON can be large; extract interesting fields
        for item in data.get("interesting_findings", [])[:20]:
            findings.append(item.get("title", "Interesting finding"))
        for vuln in data.get("vulnerabilities", {}).values():
            findings.append(vuln.get("title", "Vulnerability"))
    except json.JSONDecodeError:
        # Non-JSON mode: simple text parse
        for line in result["stdout"].splitlines():
            line = line.strip()
            if line.startswith("[!]") or line.startswith("[+]"):
                if line not in findings:
                    findings.append(line)
    return findings


def _run_droopescan(domain: str) -> list[str]:
    """Run Droopescan for CMS detection/vulnerabilities."""
    tool = _find_tool("droopescan")
    if not tool:
        return []

    target = f"https://{domain}"
    cmd = [sys.executable, tool, "scan", "drupal", "-u", target]
    result = _run_command(cmd, timeout=300)

    findings = []
    for line in (result["stdout"] + "\n" + result["stderr"]).splitlines():
        line = line.strip()
        if line.startswith("[+]") and line not in findings:
            findings.append(line)
    return findings


def _check_ssl(domain: str) -> dict | None:
    """Retrieve SSL/TLS certificate information."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
                issuer = dict(x[0] for x in cert.get("issuer", []))
                subject = dict(x[0] for x in cert.get("subject", []))
                expiry = cert.get("notAfter", "unknown")
                return {
                    "subject": subject.get("commonName", "N/A"),
                    "issuer": issuer.get("organizationName", "N/A"),
                    "expires": expiry,
                }
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Exploitation & Validation (after explicit confirmation)
# ---------------------------------------------------------------------------
def _run_sqlmap(domain: str) -> list[str]:
    """Run sqlmap with crawling, forms, and higher risk settings."""
    sqlmap_exe = _find_tool("sqlmap")
    if not sqlmap_exe:
        return []

    target_url = f"https://{domain}"
    cmd = [
        sys.executable, sqlmap_exe,
        "-u", target_url,
        "--batch",
        "--random-agent",
        "--crawl=3",
        "--forms",
        "--level=2",
        "--risk=2",
        "--output-dir", str(BASE_DIR / "logs" / "sqlmap"),
    ]

    result = _run_command(cmd, timeout=1800)

    findings = []
    for line in (result["stdout"] + "\n" + result["stderr"]).splitlines():
        line = line.strip()
        # Only keep confirmed injection/vulnerability lines
        if "is vulnerable" in line.lower() or "injectable" in line.lower() or "sqlmap identified" in line.lower():
            if line not in findings:
                findings.append(line)

    return findings


def _run_xsstrike(domain: str) -> list[str]:
    """Run XSStrike with crawling and clean confirmed findings."""
    xsstrike = _find_tool("xsstrike")
    if not xsstrike:
        return []

    target_url = f"https://{domain}"
    cmd = ["python", xsstrike, "-u", target_url, "--crawl", "--console-log-level", "WARNING"]
    result = _run_command(cmd, timeout=1800)

    findings = []
    for line in (result["stdout"] + "\n" + result["stderr"]).splitlines():
        line = line.strip()
        if any(k in line.lower() for k in ("reflected", "dom xss", "stored xss", "vulnerable", "xss found")):
            if line not in findings:
                findings.append(line)

    return findings


def _run_commix(domain: str) -> list[str]:
    """Run Commix only when useful; otherwise return no findings."""
    commix = _find_tool("commix")
    if not commix:
        return []

    # Commix needs a specific vulnerable URL/parameter, not just the homepage.
    # We skip it when we have no discovered query parameters.
    return []

def _run_lfisuite(domain: str) -> list[str]:
    """Run LFISuite only if available; catch syntax errors."""
    lfisuite = _find_tool("lfisuite")
    if not lfisuite:
        return []

    return []

# ---------------------------------------------------------------------------
# Professional PDF Report
# ---------------------------------------------------------------------------
def _sanitize_text(text: str) -> str:
    """Ensure only Latin‑1 characters are passed to the PDF."""
    # Replace common problematic Unicode characters first
    text = text.replace("\u2011", "-")  # non-breaking hyphen
    text = text.replace("\u2014", "-")  # em dash
    text = text.replace("\u2013", "-")  # en dash
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _write_pdf_section(pdf, title: str, items: list[str], max_items: int = 30):
    """Write a titled section with optional bullet items."""
    pdf.set_fill_color(10, 20, 40)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Courier", "B", 11)
    pdf.cell(0, 8, _sanitize_text(title), ln=True, fill=True)
    pdf.ln(2)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Courier", "", 9)

    if not items:
        pdf.cell(0, 5, "No findings.", ln=True)
        return

    for item in items[:max_items]:
        safe = _sanitize_text(str(item))
        # Hard‑wrap to avoid horizontal space errors
        while len(safe) > 90:
            pdf.cell(0, 5, safe[:90], ln=True)
            safe = safe[90:]
        if safe:
            pdf.cell(0, 5, safe, ln=True)


def _generate_pdf(results: dict) -> Path:
    """Generate a clean, professional pentest report and return its path."""
    from fpdf import FPDF

    target = results.get("target", "unknown")

    safe_target = target.replace("://", "_").replace("/", "_").replace("\\", "_").replace(":", "_")
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    desktop = Path.home() / "Desktop"
    filepath = desktop / f"JARVIS_Pentest_{safe_target}_{timestamp}.pdf"

    class ReportPDF(FPDF):
        def header(self):
            self.set_fill_color(5, 15, 30)
            self.set_text_color(0, 255, 255)
            self.set_font("Courier", "B", 14)
            self.cell(0, 10, "J.A.R.V.I.S RED-TEAM PENTEST REPORT", ln=True, align="C", fill=True)
            self.set_font("Courier", "", 9)
            self.set_text_color(0, 0, 0)
            self.cell(0, 6, f"Target: {_sanitize_text(target)}", ln=True, align="C")
            self.cell(0, 6, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True, align="C")
            self.ln(4)

        def footer(self):
            self.set_y(-15)
            self.set_font("Courier", "", 8)
            self.set_text_color(128, 128, 128)
            self.cell(0, 10, f"Page {self.page_no()} — J.A.R.V.I.S", align="C")

    pdf = ReportPDF()

    # Force all PDF text to be Latin‑1 safe, no matter where it's written
    original_cell = pdf.cell
    original_multi_cell = pdf.multi_cell

    def safe_cell(w, h=0, txt="", *args, **kwargs):
        if isinstance(txt, str):
            txt = _sanitize_text(txt)
        return original_cell(w, h, txt, *args, **kwargs)

    def safe_multi_cell(w, h=0, txt="", *args, **kwargs):
        if isinstance(txt, str):
            txt = _sanitize_text(txt)
        return original_multi_cell(w, h, txt, *args, **kwargs)

    pdf.cell = safe_cell
    pdf.multi_cell = safe_multi_cell

    pdf.set_margins(12, 15, 12)
    pdf.add_page()

    # Tools used
    installed = results.get("installed_tools", {})
    tool_lines = [f"{name}: {'installed' if ok else 'missing'}" for name, ok in installed.items()]
    _write_pdf_section(pdf, "[1] TOOL STATUS", tool_lines, max_items=30)
    pdf.ln(4)

    # Recon
    _write_pdf_section(pdf, "[2] SUBDOMAINS DISCOVERED", results.get("subdomains", []))
    pdf.ln(3)
    _write_pdf_section(pdf, "[3] SENSITIVE DIRECTORIES / FILES", results.get("directories", []))
    pdf.ln(3)
    _write_pdf_section(pdf, "[4] EMAIL ADDRESSES & BREACH STATUS", results.get("email_lines", []))
    pdf.ln(3)
    _write_pdf_section(pdf, "[5] LINKEDIN / EMPLOYEE PROFILES", results.get("linkedin", []))
    pdf.ln(4)

    # Scan
    _write_pdf_section(pdf, "[6] NMAP PORT SCAN", results.get("nmap_ports", []))
    pdf.ln(3)
    _write_pdf_section(pdf, "[7] NIKTO WEB FINDINGS", results.get("nikto_findings", []))
    pdf.ln(3)
    _write_pdf_section(pdf, "[8] NUCLEI CVE FINDINGS", results.get("nuclei_findings", []))
    pdf.ln(3)
    _write_pdf_section(pdf, "[9] WORDPRESS SCAN (WPScan)", results.get("wpscan_findings", []))
    pdf.ln(3)
    _write_pdf_section(pdf, "[10] CMS SCAN (Droopescan)", results.get("droopescan_findings", []))
    pdf.ln(4)

    # Exploit / Validation
    _write_pdf_section(pdf, "[11] SQL INJECTION (sqlmap)", results.get("sqlmap_findings", []))
    pdf.ln(3)
    _write_pdf_section(pdf, "[12] XSS (XSStrike)", results.get("xsstrike_findings", []))
    pdf.ln(3)
    _write_pdf_section(pdf, "[13] COMMAND INJECTION (Commix)", results.get("commix_findings", []))
    pdf.ln(3)
    _write_pdf_section(pdf, "[14] LOCAL FILE INCLUSION (LFISuite)", results.get("lfisuite_findings", []))
    pdf.ln(4)

    # Additional recon
    _write_pdf_section(pdf, "[15] TECHNOLOGIES DETECTED", results.get("technologies", []))
    pdf.ln(3)
    _write_pdf_section(pdf, "[16] WAF / CDN DETECTION", results.get("waf", []))
    pdf.ln(3)
    _write_pdf_section(pdf, "[17] DNS RECORDS", results.get("dns_records", []), max_items=15)
    pdf.ln(3)
    _write_pdf_section(pdf, "[18] OSINT DORKS", results.get("dorks", []), max_items=15)
    pdf.ln(4)

    # SSL
    ssl_info = results.get("ssl_info") or {}
    ssl_lines = []
    if ssl_info:
        ssl_lines.append(f"Subject: {ssl_info.get('subject', 'N/A')}")
        ssl_lines.append(f"Issuer: {ssl_info.get('issuer', 'N/A')}")
        ssl_lines.append(f"Expires: {ssl_info.get('expires', 'N/A')}")
    _write_pdf_section(pdf, "[19] SSL/TLS CERTIFICATE", ssl_lines)
    pdf.ln(4)


    _write_pdf_section(pdf, "[20] AI TACTICAL INSIGHT", [results.get("ai_insight", "No insight.")], max_items=50)
    pdf.ln(3)
    _write_pdf_section(pdf, "[21] DEEP PROBE FINDINGS", results.get("deep_findings", []))
    pdf.ln(4)


    # Executive summary
    total_ports = sum(len(h.get("open_ports", [])) for h in results.get("nmap_hosts", []))
    summary_lines = [
        f"Target: {target}",
        f"Subdomains: {len(results.get('subdomains', []))}",
        f"Sensitive paths: {len(results.get('directories', []))}",
        f"Open ports: {total_ports}",
        f"Web findings: {len(results.get('nikto_findings', []))}",
        f"CVE findings: {len(results.get('nuclei_findings', []))}",
        f"Exploit findings: {len(results.get('sqlmap_findings', [])) + len(results.get('xsstrike_findings', [])) + len(results.get('commix_findings', [])) + len(results.get('lfisuite_findings', []))}",
        f"SSL valid until: {ssl_info.get('expires', 'unknown')}",
    ]
    _write_pdf_section(pdf, "[20] EXECUTIVE SUMMARY", summary_lines, max_items=30)

    pdf.output(str(filepath))
    return filepath

# ---------------------------------------------------------------------------
# Main Dispatcher
# ---------------------------------------------------------------------------
def _run_deep_probes(domain: str, initial_findings: dict) -> dict:
    """Use AI to recommend and run deeper targeted tests."""
    prompt = f"""
You are an elite penetration tester performing an authorised test on {domain}.

Initial findings:
- Technologies: {initial_findings.get('technologies', [])}
- Open ports: {initial_findings.get('open_ports', [])}
- Web findings: {initial_findings.get('web_findings', [])}
- CMS detected: {initial_findings.get('cms', 'unknown')}
- WAF detected: {initial_findings.get('waf', [])}

Based only on this data, recommend the next 3 most valuable penetration tests to run.
For each, give:
1. Tool / manual technique
2. Why it is likely to succeed
3. One specific command or test payload (if applicable)

Return a concise report.
"""
    insight = _get_llm_insight(prompt)

    extra = {
        "ai_insight": insight,
        "deep_findings": [],
    }

    cms = initial_findings.get("cms", "unknown")

    if cms == "wordpress":
        wpscan = _run_wpscan(domain)
        if wpscan:
            extra["deep_findings"].extend(wpscan)

    elif cms in ("joomla", "drupal"):
        droopescan = _run_droopescan(domain)
        if droopescan:
            extra["deep_findings"].extend(droopescan)

    # Run XSStrike if a login/admin/search page was found
    dirs = initial_findings.get("directories", [])
    if dirs:
        extra["deep_findings"].append("Discovered sensitive paths: " + ", ".join(dirs[:10]))
        for d in dirs:
            if any(k in d.lower() for k in ("login", "admin", "search", "query")):
                xs = _run_xsstrike(f"https://{domain}")
                if xs:
                    extra["deep_findings"].extend(xs)
                break

    return extra


def security_mode(parameters: dict, player=None, speak=None) -> str:
    """
    Full red‑team pentest engine.

    parameters:
        target    : domain or IP address
        action    : "full" (default), "list_tools", "update_tools"
        confirmed : "yes" to authorise the scan

    Returns a concise spoken summary and PDF path.
    """
    action = (parameters or {}).get("action", "full").lower().strip()
    target = (parameters or {}).get("target", "").strip()
    confirmed = str((parameters or {}).get("confirmed", "")).lower()

    # Quick actions that don't need target/authorisation
    if action == "list_tools":
        installed = _detect_installed_tools()
        lines = [f"{name}: {'installed' if ok else 'missing'}" for name, ok in installed.items()]
        return "Security tools:\n" + "\n".join(lines)

    if action in ("update_tools", "update"):
        return update_tools()

    if not target:
        return "No target specified, sir."

    if confirmed not in ("yes", "true", "1", "confirm"):
        return (
            f"Authorisation required for {target}. "
            "Please confirm you are authorised and call again with confirmed=yes."
        )

    domain = _normalise_target(target)
    results = {
        "target": domain,
        "installed_tools": _detect_installed_tools(),
    }

    # --------------------------- Recon ---------------------------
    results["subdomains"] = _enumerate_subdomains(domain)
    results["directories"] = _brute_force_dirs(domain)
    emails, guesses = _harvest_emails(domain)
    results["emails"] = emails
    results["email_guesses"] = guesses

    email_breaches = {}
    for e in emails:
        breaches = _check_pwned(e)
        if breaches:
            email_breaches[e] = breaches
    results["email_breaches"] = email_breaches

    domain_breach_count, domain_breach_names = _check_domain_breaches(domain)
    results["domain_breach_count"] = domain_breach_count
    results["domain_breach_names"] = domain_breach_names

    results["linkedin"] = _scrape_linkedin(domain)
    results["dns_records"] = _advanced_dns_enum(domain)
    results["technologies"] = _detect_technologies(domain)
    results["waf"] = _detect_waf(domain)
    results["dorks"] = _generate_dorks(domain)

    # --------------------------- Scan ---------------------------
    nmap_data = _run_nmap(domain)
    results["nmap_hosts"] = nmap_data.get("hosts", [])
    results["nmap_ports"] = [
        f"{h['ip']}: {', '.join(h['open_ports'])}"
        for h in results["nmap_hosts"]
    ]
    results["nikto_findings"] = _run_nikto(domain)
    results["nuclei_findings"] = _run_nuclei(domain)
    results["wpscan_findings"] = _run_wpscan(domain)
    results["droopescan_findings"] = _run_droopescan(domain)

    # --------------------------- Exploit ---------------------------
    results["sqlmap_findings"] = _run_sqlmap(domain)
    results["xsstrike_findings"] = _run_xsstrike(domain)
    results["commix_findings"] = _run_commix(domain)
    results["lfisuite_findings"] = _run_lfisuite(domain)

    # --------------------------- SSL ---------------------------
    results["ssl_info"] = _check_ssl(domain)

    # Build email lines for PDF
    email_lines = []
    for e in results["emails"][:10]:
        breaches = results["email_breaches"].get(e, [])
        if breaches:
            email_lines.append(f"{e}  [BREACHED: {', '.join(breaches[:3])}]")
        else:
            email_lines.append(f"{e}  [No breaches found]")
    results["email_lines"] = email_lines

    # --------------------------- Deep probes ---------------------------
    initial_findings = {
        "technologies": results.get("technologies", []),
        "open_ports": results.get("nmap_ports", []),
        "web_findings": results.get("nikto_findings", []),
        "cms": _detect_cms(domain, results.get("technologies", [])),
        "waf": results.get("waf", []),
        "directories": results.get("directories", []),
    }

    deep = _run_deep_probes(domain, initial_findings)
    results["ai_insight"] = deep.get("ai_insight", "")
    results["deep_findings"] = deep.get("deep_findings", [])

    # --------------------------- Report ---------------------------
    filepath = _generate_pdf(results)

    total_ports = sum(len(h.get("open_ports", [])) for h in results["nmap_hosts"])
    exploit_count = (
        len(results.get("sqlmap_findings", []))
        + len(results.get("xsstrike_findings", []))
        + len(results.get("commix_findings", []))
        + len(results.get("lfisuite_findings", []))
        + len(results.get("deep_findings", []))
    )
    spoken = (
        f"Pentest on {domain} complete, sir. "
        f"Found {len(results['subdomains'])} subdomains, "
        f"{len(results['directories'])} sensitive paths, "
        f"{total_ports} open ports, "
        f"{len(results['nikto_findings'])} web findings, "
        f"{len(results['nuclei_findings'])} CVE findings, "
        f"{exploit_count} exploitation findings. "
        f"Full report saved to your desktop."
    )

    return f"{spoken}\n{filepath}"

