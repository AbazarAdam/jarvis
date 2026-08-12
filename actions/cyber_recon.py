"""
cyber_recon.py — JARVIS Unified Cyber Reconnaissance
Runs subdomain enumeration, directory brute‑forcing, Nmap, Nikto, and SSL checks.
Generates a single comprehensive PDF report.
"""

import requests
import subprocess
import shutil
import ssl
import socket
from pathlib import Path
from datetime import datetime


# ---------------------------------------------------------------------------
# Common sensitive directories (built‑in wordlist)
# ---------------------------------------------------------------------------
_COMMON_DIRS = [
    "admin", "login", "wp-admin", "dashboard", "backup", "backups",
    ".git", ".env", "config", "test", "dev", "staging", "api",
    "robots.txt", "sitemap.xml", "phpmyadmin", "wp-login.php",
    "administrator", "cpanel", "webmail", "db", "sql", ".svn",
    ".htaccess", "logs", "log", "tmp", "temp", "backup.zip",
    "backup.sql", "dump", "export", "private", "secret", "credentials",
]


# ---------------------------------------------------------------------------
# Reusable helpers (copied from security_scanner.py for independence)
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

    cmd = [nmap_exe, "-F", "-T4", "--host-timeout", "60s", target]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if proc.returncode == 0 and "Host is up" in proc.stdout:
            return {
                "raw": proc.stdout,
                "hosts": _parse_nmap_text(proc.stdout),
            }
    except subprocess.TimeoutExpired:
        pass

    cmd = [nmap_exe, "--top-ports", "20", "-T4", "--host-timeout", "45s", target]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
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


# ---------------------------------------------------------------------------
# Subdomain enumeration (crt.sh)
# ---------------------------------------------------------------------------
def _enumerate_subdomains(domain):
    """Return up to 50 subdomains using crt.sh."""
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
# Directory brute‑forcing
# ---------------------------------------------------------------------------
def _brute_force_dirs(target):
    """Check common sensitive paths on the target."""
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
# PDF generation
# ---------------------------------------------------------------------------
def _generate_pdf(target, subdomains, directories, nmap_data, nikto_findings, ssl_info):
    from fpdf import FPDF

    desktop = Path.home() / "Desktop"
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_target = target.replace("://", "_").replace("/", "_").replace("\\", "_").replace(":", "_")
    filename = f"cyber_recon_{safe_target}_{timestamp}.pdf"
    filepath = desktop / filename

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Courier", size=12)
    pdf.cell(0, 10, "CYBER RECONNAISSANCE REPORT", ln=True, align="C")
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

    # 2. Sensitive Directories
    pdf.cell(0, 7, "[2] SENSITIVE DIRECTORIES / FILES", ln=True)
    pdf.ln(2)
    if not directories:
        pdf.cell(0, 6, "No sensitive paths discovered.", ln=True)
    else:
        for d in directories:
            safe = d.encode("latin-1", errors="replace").decode("latin-1")
            pdf.cell(0, 6, safe, ln=True)
    pdf.ln(5)

    # 3. Port Scan
    pdf.cell(0, 7, "[3] PORT SCAN (Nmap)", ln=True)
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
    pdf.ln(5)

    # 4. SSL Certificate
    pdf.cell(0, 7, "[4] SSL/TLS CERTIFICATE", ln=True)
    pdf.ln(2)
    if ssl_info:
        pdf.cell(0, 6, f"Subject: {ssl_info['subject']}", ln=True)
        pdf.cell(0, 6, f"Issuer: {ssl_info['issuer']}", ln=True)
        pdf.cell(0, 6, f"Expires: {ssl_info['expires']}", ln=True)
    else:
        pdf.cell(0, 6, "Could not retrieve SSL certificate.", ln=True)
    pdf.ln(5)

    # 5. Web Vulnerabilities (Nikto)
    pdf.cell(0, 7, "[5] WEB VULNERABILITY SCAN (Nikto)", ln=True)
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

    # 6. Executive Summary
    pdf.cell(0, 7, "[6] EXECUTIVE SUMMARY", ln=True)
    pdf.ln(2)
    total_ports = sum(len(h["open_ports"]) for h in nmap_data.get("hosts", []))
    summary = (
        f"Target: {target}\n"
        f"Subdomains: {len(subdomains)}\n"
        f"Sensitive paths: {len(directories)}\n"
        f"Open ports: {total_ports}\n"
        f"Web findings: {len(nikto_findings)}\n"
        f"SSL valid until: {ssl_info['expires'] if ssl_info else 'unknown'}\n\n"
        "Full details are provided in the sections above. "
        "Review immediately and remediate any critical findings."
    )
    pdf.multi_cell(0, 6, summary)

    pdf.output(str(filepath))

    # Spoken summary
    spoken = (
        f"Full cyber recon on {target} complete. "
        f"Found {len(subdomains)} subdomains, {len(directories)} sensitive paths, "
        f"{total_ports} open ports, and {len(nikto_findings)} web findings. "
        f"Comprehensive report saved to your desktop."
    )

    return filepath, spoken


# ---------------------------------------------------------------------------
# Main tool entry point
# ---------------------------------------------------------------------------
def cyber_recon(parameters: dict, player=None) -> str:
    target = (parameters or {}).get("target", "").strip()
    if not target:
        return "No target specified."

    # Extract domain name for subdomain search
    domain = target.replace("https://", "").replace("http://", "").split("/")[0]

    # Run all phases
    subdomains = _enumerate_subdomains(domain)
    directories = _brute_force_dirs(domain)
    nmap_data = _run_nmap(domain)
    nikto_raw = _run_nikto(domain)
    nikto_findings = _parse_nikto(nikto_raw)
    ssl_info = _check_ssl(domain)

    filepath, spoken = _generate_pdf(
        domain, subdomains, directories, nmap_data, nikto_findings, ssl_info
    )

    return spoken + "\n" + str(filepath)