"""
Security_mode.py — JARVIS Unified Cyber Reconnaissance (OSINT + Exploit)
"""

import re
import json
import requests
import subprocess
import shutil
import ssl
import socket
from pathlib import Path
from datetime import datetime


# ---------------------------------------------------------------------------
# Common sensitive directories
# ---------------------------------------------------------------------------
_COMMON_DIRS = [
    "admin", "login", "wp-admin", "dashboard", "backup", "backups",
    ".git", ".env", "config", "test", "dev", "staging", "api",
    "robots.txt", "sitemap.xml", "phpmyadmin", "wp-login.php",
    "administrator", "cpanel", "webmail", "db", "sql", ".svn",
    ".htaccess", "logs", "log", "tmp", "temp", "backup.zip",
    "backup.sql", "dump", "export", "private", "secret", "credentials",
]

# Common emails to try
_COMMON_EMAILS = [
    "admin", "contact", "info", "support", "security",
    "webmaster", "postmaster", "abuse", "hello", "sales",
]


# ---------------------------------------------------------------------------
# Tool helpers (unchanged)
# ---------------------------------------------------------------------------
def _find_tool(name):
    path = shutil.which(name)
    if path:
        return path
    tools_dir = Path(__file__).resolve().parent.parent / "tools"
    if name == "nmap.exe":
        candidate = tools_dir / "nmap" / "nmap.exe"
        if candidate.exists():
            return str(candidate)
    if name in ("nikto.pl", "nikto"):
        candidate = tools_dir / "nikto" / "program" / "nikto.pl"
        if candidate.exists():
            return str(candidate)
    for p in [r"C:\Program Files (x86)\Nmap\nmap.exe", r"C:\Program Files\Nmap\nmap.exe"]:
        if Path(p).exists():
            return p
    return None


def _run_nmap(target):
    nmap_exe = _find_tool("nmap.exe")
    if not nmap_exe:
        return {"error": "Nmap not found.", "raw": "", "hosts": []}

    # Fast scan with vulnerability scripts
    cmd = [nmap_exe, "-sV", "--script", "vuln", "-T4", "--host-timeout", "90s", target]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode == 0 and "Host is up" in proc.stdout:
            return {
                "raw": proc.stdout,
                "hosts": _parse_nmap_text(proc.stdout),
            }
    except subprocess.TimeoutExpired:
        pass

    # Fallback: top ports
    cmd = [nmap_exe, "--top-ports", "100", "-T4", "--host-timeout", "60s", target]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if proc.returncode == 0:
            return {
                "raw": proc.stdout,
                "hosts": _parse_nmap_text(proc.stdout),
            }
    except subprocess.TimeoutExpired:
        pass

    return {"error": "Nmap scan timed out.", "raw": "", "hosts": []}


def _parse_nmap_text(output):
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


def _run_nikto(target):
    nikto_pl = _find_tool("nikto.pl")
    perl_exe = shutil.which("perl") or r"C:\Strawberry\perl\bin\perl.exe"
    if not Path(perl_exe).exists() or not nikto_pl:
        return ""
    if not target.startswith("http"):
        target = "https://" + target
    cmd = [perl_exe, nikto_pl, "-h", target, "-Tuning", "123", "-o", "-", "-ssl"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        output = proc.stdout or proc.stderr
        if "Unable to connect" in output:
            return ""
        return output
    except Exception:
        return ""


def _parse_nikto(raw):
    if not raw:
        return []
    findings = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("+ ") or "ERROR:" in line:
            continue
        if line not in findings:
            findings.append(line)
    return findings


def _check_ssl(target):
    host = target.replace("https://", "").replace("http://", "").split("/")[0]
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
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

def _find_nuclei():
    tools_dir = Path(__file__).resolve().parent.parent / "tools"
    candidate = tools_dir / "nuclei" / "nuclei.exe"
    if candidate.exists():
        return str(candidate)
    return shutil.which("nuclei")


def _run_nuclei(target):
    nuclei_exe = _find_nuclei()
    if not nuclei_exe:
        return []

    if not target.startswith(("http://", "https://")):
        target = "https://" + target

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

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        output = proc.stdout.strip()
        findings = []
        for line in output.splitlines():
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = data.get("info", {}).get("name", "Unknown")
            sev = data.get("info", {}).get("severity", "unknown")
            matched = data.get("matched-at", target)
            findings.append(f"{sev.upper()}: {name} ({matched})")
        return findings
    except subprocess.TimeoutExpired:
        print("[SecurityMode] ⚠️ Nuclei scan timed out")
        return []
    except Exception as e:
        print(f"[SecurityMode] ⚠️ Nuclei error: {e}")
        return []
# ---------------------------------------------------------------------------
# Subdomains (unchanged)
# ---------------------------------------------------------------------------
def _enumerate_subdomains(domain):
    url = f"https://crt.sh/?q=%.{domain}&output=json"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            subs = set()
            for entry in data:
                name = entry.get("name_value", "")
                for n in name.split("\n"):
                    n = n.strip().lower()
                    if n.endswith(domain) and n != domain and "*" not in n:
                        subs.add(n)
            return sorted(subs)[:50]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Directory brute‑forcing (unchanged)
# ---------------------------------------------------------------------------
def _brute_force_dirs(target):
    if not target.startswith("http"):
        target = "https://" + target
    found = []
    for path in _COMMON_DIRS:
        url = f"{target.rstrip('/')}/{path}"
        try:
            r = requests.get(url, timeout=5, allow_redirects=False)
            if r.status_code in (200, 301, 302, 403, 401):
                found.append(f"{path} (HTTP {r.status_code})")
        except Exception:
            pass
    return found


# ---------------------------------------------------------------------------
# NEW: OSINT – Email harvesting & breach check
# ---------------------------------------------------------------------------
def _harvest_emails(domain):
    """Scrape the homepage for email addresses and guess common ones."""
    emails = set()
    # Try to get emails from the homepage
    for scheme in ["https", "http"]:
        try:
            resp = requests.get(f"{scheme}://{domain}", timeout=10, headers={
                "User-Agent": "Mozilla/5.0"
            })
            # Find email patterns
            found = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", resp.text)
            for e in found:
                if e.endswith(domain):
                    emails.add(e)
            break
        except Exception:
            pass
    # Also include common pattern guesses (not verified)
    guesses = [f"{user}@{domain}" for user in _COMMON_EMAILS]
    return sorted(emails), guesses


def _check_pwned(email):
    """Check an email address against haveibeenpwned.com (k-anonymity)."""
    try:
        # Use the API that returns breach names
        url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
        headers = {"hibp-api-key": ""}  # no key needed for this endpoint
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return [b["Name"] for b in data]
        elif resp.status_code == 404:
            return []
    except Exception:
        pass
    return []


def _check_domain_breaches(domain):
    """Check how many times the domain appears in breaches (generic search)."""
    try:
        # Use the domain search API (no key needed)
        url = f"https://haveibeenpwned.com/api/v3/breaches?domain={domain}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return len(data), [b["Name"] for b in data[:5]]
    except Exception:
        pass
    return 0, []


# ---------------------------------------------------------------------------
# NEW: OSINT – Employee discovery via LinkedIn scraping
# ---------------------------------------------------------------------------
def _scrape_linkedin(domain):
    """Find LinkedIn profile URLs on the target's website and extract names."""
    profiles = []
    for scheme in ["https", "http"]:
        try:
            resp = requests.get(f"{scheme}://{domain}", timeout=10, headers={
                "User-Agent": "Mozilla/5.0"
            })
            # Find LinkedIn URLs
            linkedin_urls = re.findall(
                r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[^"\'<>\s]+',
                resp.text
            )
            for url in linkedin_urls[:10]:
                # Try to get the page title to extract a name
                try:
                    profile_resp = requests.get(url, timeout=5, headers={
                        "User-Agent": "Mozilla/5.0"
                    })
                    title_match = re.search(r"<title>(.*?)</title>", profile_resp.text)
                    if title_match:
                        name = title_match.group(1).split("|")[0].strip()
                        profiles.append(f"{name} ({url})")
                    else:
                        profiles.append(url)
                except Exception:
                    profiles.append(url)
            break
        except Exception:
            pass
    return profiles


# ---------------------------------------------------------------------------
# PDF generation (extended with new sections)
# ---------------------------------------------------------------------------
def _generate_pdf(target, subdomains, linkedin, emails, email_guesses,
                  email_breaches, domain_breach_count, domain_breach_names,
                  directories, nmap_data, nikto_findings, ssl_info,
                  nuclei_findings=None):
    from fpdf import FPDF

    desktop = Path.home() / "Desktop"
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_target = target.replace("://", "_").replace("/", "_").replace("\\", "_").replace(":", "_")
    filename = f"cyber_recon_{safe_target}_{timestamp}.pdf"
    filepath = desktop / filename

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Courier", size=12)
    pdf.cell(0, 10, "SECURITY ASSESSMENT REPORT", ln=True, align="C")
    pdf.cell(0, 8, f"Target: {target}", ln=True, align="C")
    pdf.cell(0, 8, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True, align="C")
    pdf.ln(5)

    # 1. Subdomains
    pdf.set_font("Courier", size=10)
    pdf.cell(0, 7, "[1] SUBDOMAINS DISCOVERED", ln=True)
    pdf.ln(2)
    if not subdomains:
        pdf.cell(0, 6, "No subdomains discovered.", ln=True)
    else:
        for sub in subdomains[:30]:
            safe = sub.encode("latin-1", errors="replace").decode("latin-1")
            pdf.cell(0, 6, safe, ln=True)
    pdf.ln(5)

    # 2. Employee / LinkedIn profiles
    pdf.cell(0, 7, "[2] EMPLOYEE / LINKEDIN PROFILES", ln=True)
    pdf.ln(2)
    if not linkedin:
        pdf.cell(0, 6, "No LinkedIn profiles found.", ln=True)
    else:
        for profile in linkedin[:15]:
            safe = profile.encode("latin-1", errors="replace").decode("latin-1")
            pdf.cell(0, 6, safe, ln=True)
    pdf.ln(5)

    # 3. Email harvesting & breach check
    pdf.cell(0, 7, "[3] EMAIL ADDRESSES & BREACH STATUS", ln=True)
    pdf.ln(2)
    if emails:
        pdf.cell(0, 6, "Harvested emails:", ln=True)
        for e in emails[:10]:
            pdf.cell(6, 6, "")
            safe_e = e.encode("latin-1", errors="replace").decode("latin-1")
            breaches = email_breaches.get(e, [])
            if breaches:
                pdf.cell(0, 6, f"{safe_e}  [BREACHED: {', '.join(breaches[:3])}]", ln=True)
            else:
                pdf.cell(0, 6, f"{safe_e}  [No breaches found]", ln=True)
    pdf.cell(0, 6, "Common email guesses (not verified):", ln=True)
    for guess in email_guesses[:10]:
        safe_g = guess.encode("latin-1", errors="replace").decode("latin-1")
        pdf.cell(6, 6, "")
        pdf.cell(0, 6, safe_g, ln=True)
    if domain_breach_count > 0:
        pdf.cell(0, 6, f"Domain breaches: {domain_breach_count} ({', '.join(domain_breach_names)})", ln=True)
    else:
        pdf.cell(0, 6, "Domain does not appear in known data breaches.", ln=True)
    pdf.ln(5)

    # 4. Sensitive directories
    pdf.cell(0, 7, "[4] SENSITIVE DIRECTORIES / FILES", ln=True)
    pdf.ln(2)
    if not directories:
        pdf.cell(0, 6, "No sensitive paths discovered.", ln=True)
    else:
        for d in directories:
            safe = d.encode("latin-1", errors="replace").decode("latin-1")
            pdf.cell(0, 6, safe, ln=True)
    pdf.ln(5)

    # 5. Port scan (with vuln scripts)
    pdf.cell(0, 7, "[5] PORT SCAN WITH VULNERABILITY DETECTION (Nmap)", ln=True)
    pdf.ln(2)
    error = nmap_data.get("error")
    if error:
        pdf.cell(0, 6, f"Error: {error}", ln=True)
    else:
        hosts = nmap_data.get("hosts", [])
        if not hosts:
            pdf.cell(0, 6, "No open ports found.", ln=True)
        else:
            for host in hosts:
                pdf.cell(0, 6, f"Host: {host['ip']}", ln=True)
                for port in host["open_ports"]:
                    pdf.cell(6, 6, "")
                    pdf.cell(0, 6, port, ln=True)
        raw = nmap_data.get("raw", "")
        if raw:
            pdf.ln(3)
            pdf.cell(0, 6, "Raw Nmap output (first 80 lines, includes CVE data):", ln=True)
            pdf.set_font("Courier", size=6)
            for line in raw.splitlines()[:80]:
                safe_line = line.encode("latin-1", errors="replace").decode("latin-1")
                pdf.cell(0, 4, safe_line, ln=True)
            pdf.set_font("Courier", size=10)
    pdf.ln(5)

    # 6. SSL cert
    pdf.cell(0, 7, "[6] SSL/TLS CERTIFICATE", ln=True)
    pdf.ln(2)
    if ssl_info:
        pdf.cell(0, 6, f"Subject: {ssl_info['subject']}", ln=True)
        pdf.cell(0, 6, f"Issuer: {ssl_info['issuer']}", ln=True)
        pdf.cell(0, 6, f"Expires: {ssl_info['expires']}", ln=True)
    else:
        pdf.cell(0, 6, "Could not retrieve SSL certificate.", ln=True)
    pdf.ln(5)

    # 7. Web vulnerabilities (Nikto)
    pdf.cell(0, 7, "[7] WEB VULNERABILITY SCAN (Nikto)", ln=True)
    pdf.ln(2)
    if not nikto_findings:
        pdf.cell(0, 6, "No web vulnerabilities found or scan blocked.", ln=True)
    else:
        for finding in nikto_findings[:15]:
            safe = finding.encode("latin-1", errors="replace").decode("latin-1")
            pdf.set_font("Courier", size=8)
            pdf.multi_cell(0, 5, safe)
        pdf.set_font("Courier", size=10)
    pdf.ln(5)

    # 8. Nuclei CVE findings
    pdf.cell(0, 7, "[8] NUCLEI CVE FINDINGS", ln=True)
    pdf.ln(2)
    if not nuclei_findings:
        pdf.cell(0, 6, "No CVE findings or Nuclei not available.", ln=True)
    else:
        for finding in nuclei_findings[:20]:
            safe = finding.encode("latin-1", errors="replace").decode("latin-1")
            pdf.set_font("Courier", size=8)
            pdf.multi_cell(0, 5, safe)
        pdf.set_font("Courier", size=10)
    pdf.ln(5)

    # 9. Executive Summary
    pdf.cell(0, 7, "[9] EXECUTIVE SUMMARY", ln=True)
    pdf.ln(2)
    total_ports = sum(len(h["open_ports"]) for h in nmap_data.get("hosts", []))
    summary = (
        f"Target: {target}\n"
        f"Subdomains: {len(subdomains)}\n"
        f"LinkedIn profiles: {len(linkedin)}\n"
        f"Harvested emails: {len(emails)}\n"
        f"Domain breaches: {domain_breach_count}\n"
        f"Sensitive paths: {len(directories)}\n"
        f"Open ports: {total_ports}\n"
        f"Nuclei findings: {len(nuclei_findings)}\n"
        f"Web findings: {len(nikto_findings)}\n"
        f"SSL valid until: {ssl_info['expires'] if ssl_info else 'unknown'}\n\n"
        "Full details are provided in the sections above. "
        "Review immediately and remediate any critical findings."
    )
    pdf.multi_cell(0, 6, summary)

    pdf.output(str(filepath))

    # Spoken summary
    spoken = (
        f"Security assessment on {target} complete. "
        f"Found {len(subdomains)} subdomains, {len(linkedin)} employee profiles, "
        f"{len(emails)} emails ({sum(1 for e in emails if email_breaches.get(e))} breached), "
        f"{domain_breach_count} domain breaches, {len(directories)} sensitive paths, "
        f"{total_ports} open ports, {len(nikto_findings)} web findings, "
        f"and {len(nuclei_findings)} CVE findings. "
        f"Comprehensive report saved to your desktop."
    )

    return filepath, spoken


# ---------------------------------------------------------------------------
# Main tool entry point
# ---------------------------------------------------------------------------
def security_mode(parameters: dict, player=None) -> str:
    target = (parameters or {}).get("target", "").strip()
    if not target:
        return "No target specified."

    domain = target.replace("https://", "").replace("http://", "").split("/")[0]

    # Phases
    subdomains = _enumerate_subdomains(domain)
    linkedin = _scrape_linkedin(domain)
    emails, email_guesses = _harvest_emails(domain)

    # Breach checks
    email_breaches = {}
    for e in emails:
        breaches = _check_pwned(e)
        if breaches:
            email_breaches[e] = breaches
    domain_breach_count, domain_breach_names = _check_domain_breaches(domain)

    directories = _brute_force_dirs(domain)
    nmap_data = _run_nmap(domain)
    nikto_raw = _run_nikto(domain)
    nikto_findings = _parse_nikto(nikto_raw)
    ssl_info = _check_ssl(domain)

    nuclei_findings = _run_nuclei(domain)
    nuclei_findings = nuclei_findings or []

    filepath, spoken = _generate_pdf(
        domain, subdomains, linkedin, emails, email_guesses, email_breaches,
        domain_breach_count, domain_breach_names, directories,
        nmap_data, nikto_findings, ssl_info, nuclei_findings
    )

    return spoken + "\n" + str(filepath)