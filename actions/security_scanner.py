"""
security_scanner.py — JARVIS Professional Pentest Report Generator
"""

import subprocess
import shutil
import ssl
import socket
from pathlib import Path
from datetime import datetime


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
        return {"error": "Nmap not found.", "raw": ""}

    # Fast scan first – finds open ports reliably
    cmd_fast = [nmap_exe, "-F", "-T4", "--host-timeout", "60s", target]
    try:
        proc = subprocess.run(cmd_fast, capture_output=True, text=True, timeout=90)
        if proc.returncode == 0 and "Host is up" in proc.stdout:
            return {
                "raw": proc.stdout,
                "hosts": _parse_nmap_text(proc.stdout),
            }
    except subprocess.TimeoutExpired:
        pass

    # Fallback: very quick top-ports scan
    cmd_top = [nmap_exe, "--top-ports", "20", "-T4", "--host-timeout", "45s", target]
    try:
        proc = subprocess.run(cmd_top, capture_output=True, text=True, timeout=60)
        if proc.returncode == 0:
            return {
                "raw": proc.stdout,
                "hosts": _parse_nmap_text(proc.stdout),
            }
    except subprocess.TimeoutExpired:
        pass

    return {"error": "Nmap scan timed out.", "raw": ""}


def _parse_nmap_text(output):
    """Extract hosts and open ports from Nmap text output."""
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


def _generate_pdf(nmap_data, nikto_findings, ssl_info, target):
    from fpdf import FPDF

    desktop = Path.home() / "Desktop"
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_target = target.replace("://", "_").replace("/", "_").replace("\\", "_").replace(":", "_")
    filename = f"security_report_{safe_target}_{timestamp}.pdf"
    filepath = desktop / filename

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Courier", size=12)
    pdf.cell(0, 10, "PENTEST SECURITY REPORT", ln=True, align="C")
    pdf.cell(0, 8, f"Target: {target}", ln=True, align="C")
    pdf.cell(0, 8, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True, align="C")
    pdf.ln(5)

    # Port scan
    pdf.set_font("Courier", size=10)
    pdf.cell(0, 7, "[1] PORT SCAN (Nmap)", ln=True)
    pdf.ln(2)
    error = nmap_data.get("error")
    if error:
        pdf.cell(0, 6, f"Error: {error}", ln=True)
    else:
        hosts = nmap_data.get("hosts", [])
        if not hosts:
            pdf.cell(0, 6, "No ports found in an interesting state. (CDN/proxy may hide real ports.)", ln=True)
        else:
            for host in hosts:
                pdf.cell(0, 6, f"Host: {host['ip']}", ln=True)
                for port in host["open_ports"]:
                    pdf.cell(6, 6, "")
                    pdf.cell(0, 6, port, ln=True)
        # Include raw Nmap output for manual review (first 50 lines)
        raw = nmap_data.get("raw", "")
        if raw:
            pdf.ln(3)
            pdf.cell(0, 6, "Raw Nmap output (first 50 lines):", ln=True)
            pdf.set_font("Courier", size=6)
            for line in raw.splitlines()[:50]:
                safe_line = line.encode("latin-1", errors="replace").decode("latin-1")
                pdf.cell(0, 4, safe_line, ln=True)
            pdf.set_font("Courier", size=10)
    pdf.ln(5)

    # SSL info
    pdf.cell(0, 7, "[2] SSL/TLS CERTIFICATE", ln=True)
    pdf.ln(2)
    if ssl_info:
        pdf.cell(0, 6, f"Subject: {ssl_info['subject']}", ln=True)
        pdf.cell(0, 6, f"Issuer: {ssl_info['issuer']}", ln=True)
        pdf.cell(0, 6, f"Expires: {ssl_info['expires']}", ln=True)
    else:
        pdf.cell(0, 6, "Could not retrieve SSL certificate.", ln=True)
    pdf.ln(5)

    # Web scan
    pdf.cell(0, 7, "[3] WEB VULNERABILITY SCAN (Nikto)", ln=True)
    pdf.ln(2)
    if not nikto_findings:
        pdf.cell(0, 6, "No web vulnerabilities found or scan blocked by CDN/IPS.", ln=True)
    else:
        for finding in nikto_findings[:15]:
            safe = finding.encode("latin-1", errors="replace").decode("latin-1")
            pdf.set_font("Courier", size=8)
            pdf.multi_cell(0, 5, safe)
        pdf.set_font("Courier", size=10)
    pdf.ln(5)

    # Executive summary
    pdf.set_font("Courier", size=10)
    pdf.cell(0, 7, "[4] EXECUTIVE SUMMARY", ln=True)
    pdf.ln(2)

    hosts = nmap_data.get("hosts", [])
    total_ports = sum(len(h["open_ports"]) for h in hosts) if hosts else 0
    ssl_expiry = ssl_info["expires"] if ssl_info else "unknown"
    nikto_count = len(nikto_findings)

    if total_ports == 0:
        port_note = " (target likely behind a CDN/proxy)"
    else:
        port_note = ""

    summary = (
        f"The scan of {target} found {len(hosts)} host(s) with {total_ports} potentially interesting port(s){port_note}, "
        f"{nikto_count} web finding(s), and SSL certificate valid until {ssl_expiry}. "
        "Critical issues should be addressed immediately. Full details above."
    )
    pdf.multi_cell(0, 6, summary)

    pdf.output(str(filepath))

    # Build spoken summary
    if total_ports > 0:
        spoken = (
            f"Pentest of {target} complete. Found {total_ports} interesting port(s), "
            f"{nikto_count} web finding(s). Full report saved to your desktop."
        )
    else:
        spoken = (
            f"Pentest of {target} complete. No interesting ports found{port_note}. "
            f"{nikto_count} web finding(s). Report saved to your desktop."
        )

    return filepath, spoken


def security_scan(parameters: dict, player=None) -> str:
    target = (parameters or {}).get("target", "").strip()
    if not target:
        return "No target specified."

    nmap_data = _run_nmap(target)
    nikto_raw = _run_nikto(target)
    nikto_findings = _parse_nikto(nikto_raw)
    ssl_info = _check_ssl(target)

    filepath, spoken = _generate_pdf(nmap_data, nikto_findings, ssl_info, target)

    return spoken + "\n" + str(filepath)