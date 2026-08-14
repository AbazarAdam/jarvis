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

def _detect_technologies(target):
    """Identify web technologies from headers and HTML."""
    if not target.startswith("http"):
        target = "https://" + target

    technologies = []
    try:
        resp = requests.get(target, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
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

        # Basic CMS/framework detection from HTML
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


def _detect_waf(target):
    """Detect common Web Application Firewalls from headers."""
    if not target.startswith("http"):
        target = "https://" + target

    waf = []
    try:
        resp = requests.get(target, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
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


def _advanced_dns_enum(domain):
    """Collect DNS records using nslookup (Windows)."""
    records = []
    for rtype in ["A", "AAAA", "MX", "NS", "TXT"]:
        try:
            proc = subprocess.run(
                ["nslookup", "-type=" + rtype, domain],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            out = proc.stdout.strip()
            if out:
                records.append(f"--- {rtype} ---\n{out}")
        except Exception:
            pass
    return records if records else ["No DNS records found."]


def _generate_dorks(domain):
    """Return a list of Google/GitHub dorks for the target."""
    return [
        f"site:{domain} filetype:pdf",
        f"site:{domain} filetype:docx",
        f"site:{domain} inurl:admin",
        f"site:{domain} inurl:login",
        f"site:{domain} intitle:\"index of\"",
        f"site:{domain} ext:sql | ext:bak | ext:zip",
        f"site:github.com {domain} password",
        f"site:github.com {domain} api_key",
        f"site:github.com {domain} secret",
        f"site:pastebin.com {domain}",
    ]

def _run_safe_validation(target):
    """Run Nmap vuln/auth NSE and Nuclei vulnerability validation."""
    nmap_exe = _find_tool("nmap.exe")
    validation = {"nmap_vulns": [], "nuclei_findings": []}

    if nmap_exe:
        # Non-destructive vulnerability scripts
        cmd = [nmap_exe, "-sV", "--script", "vuln,auth", "-T4", "--host-timeout", "90s", target]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode == 0:
                # Extract lines with CVE or vulnerability information
                for line in proc.stdout.splitlines():
                    if "CVE" in line or "VULNERABLE" in line or "Exploit" in line:
                        validation["nmap_vulns"].append(line.strip())
        except subprocess.TimeoutExpired:
            validation["nmap_vulns"].append("Nmap validation timed out.")

    nuclei_findings = _run_nuclei(target)
    validation["nuclei_findings"] = nuclei_findings

    return validation

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
                  nuclei_findings=None, technologies=None, waf=None,
                  dns_records=None, dorks=None, nmap_vulns=None):
    from fpdf import FPDF

    # Defaults
    technologies = technologies or []
    waf = waf or []
    dns_records = dns_records or []
    dorks = dorks or []
    nmap_vulns = nmap_vulns or []
    nuclei_findings = nuclei_findings or []

    desktop = Path.home() / "Desktop"
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_target = target.replace("://", "_").replace("/", "_").replace("\\", "_").replace(":", "_")
    filename = f"security_assessment_{safe_target}_{timestamp}.pdf"
    filepath = desktop / filename

    # Custom PDF class with header/footer
    class ReportPDF(FPDF):
        def header(self):
            self.set_fill_color(10, 20, 40)        # dark blue
            self.set_text_color(255, 255, 255)
            self.set_font("Courier", "B", 14)
            self.cell(0, 10, "J.A.R.V.I.S SECURITY ASSESSMENT", ln=True, align="C", fill=True)
            self.set_font("Courier", "", 9)
            self.set_text_color(0, 0, 0)
            self.cell(0, 6, f"Target: {target}", ln=True, align="C")
            self.cell(0, 6, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True, align="C")
            self.ln(4)

        def footer(self):
            self.set_y(-15)
            self.set_font("Courier", "", 8)
            self.set_text_color(128, 128, 128)
            self.cell(0, 10, f"Page {self.page_no()} — J.A.R.V.I.S", align="C")

    pdf = ReportPDF()
    pdf.set_margins(10, 15, 10)
    pdf.add_page()

    # Sanitise all PDF text to Latin‑1 so Courier never crashes
    _orig_cell = pdf.cell
    _orig_multi_cell = pdf.multi_cell

    def safe_cell(w, h=0, txt="", *args, **kwargs):
        if isinstance(txt, str):
            txt = txt.encode("latin-1", errors="replace").decode("latin-1")
        return _orig_cell(w, h, txt, *args, **kwargs)

    def safe_multi_cell(w, h=0, txt="", *args, **kwargs):
        if isinstance(txt, str):
            txt = txt.encode("latin-1", errors="replace").decode("latin-1")
        return _orig_multi_cell(w, h, txt, *args, **kwargs)

    pdf.cell = safe_cell
    pdf.multi_cell = safe_multi_cell

    def section_title(title):
        pdf.set_fill_color(0, 120, 200)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Courier", "B", 11)
        pdf.cell(0, 8, title, ln=True, fill=True)
        pdf.ln(2)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Courier", "", 9)

    def line(text, indent=0, color=(0, 0, 0)):
        pdf.set_text_color(*color)
        # Keep only Latin‑1 characters that Courier can render
        safe = text.encode("latin-1", errors="replace").decode("latin-1")
        # Break long unbroken strings into small pieces that always fit
        max_chars = 70
        while len(safe) > max_chars:
            chunk = safe[:max_chars]
            safe = safe[max_chars:]
            if indent:
                pdf.cell(indent, 5, "")
            pdf.cell(0, 5, chunk, ln=True)
        if safe:
            if indent:
                pdf.cell(indent, 5, "")
            # Use cell for the final short piece — never multi_cell
            pdf.cell(0, 5, safe, ln=True)
        pdf.set_text_color(0, 0, 0)

    # 1. Subdomains
    section_title("[1] SUBDOMAINS DISCOVERED")
    if not subdomains:
        line("No subdomains discovered.")
    else:
        for sub in subdomains[:30]:
            line(sub.encode("latin-1", "replace").decode("latin-1"), indent=5)
    pdf.ln(4)

    # 2. LinkedIn
    section_title("[2] EMPLOYEE / LINKEDIN PROFILES")
    if not linkedin:
        line("No LinkedIn profiles found.")
    else:
        for profile in linkedin[:15]:
            line(profile.encode("latin-1", "replace").decode("latin-1"), indent=5)
    pdf.ln(4)

    # 3. Email + Breach
    section_title("[3] EMAIL ADDRESSES & BREACH STATUS")
    if emails:
        line("Harvested emails:")
        for e in emails[:10]:
            breaches = email_breaches.get(e, [])
            if breaches:
                line(f"{e}  [BREACHED: {', '.join(breaches[:3])}]", indent=5, color=(255,100,100))
            else:
                line(f"{e}  [No breaches found]", indent=5)
    line("Common email guesses (not verified):")
    for guess in email_guesses[:10]:
        line(guess, indent=5)
    if domain_breach_count > 0:
        line(f"Domain breaches: {domain_breach_count} ({', '.join(domain_breach_names)})", color=(255,100,100))
    else:
        line("Domain does not appear in known data breaches.")
    pdf.ln(4)

    # 4. Sensitive directories
    section_title("[4] SENSITIVE DIRECTORIES / FILES")
    if not directories:
        line("No sensitive paths discovered.")
    else:
        for d in directories:
            line(d.encode("latin-1", "replace").decode("latin-1"), indent=5)
    pdf.ln(4)

    # 5. Nmap
    section_title("[5] PORT SCAN WITH VULNERABILITY DETECTION (Nmap)")
    error = nmap_data.get("error")
    if error:
        line(f"Error: {error}", color=(255,100,100))
    else:
        hosts = nmap_data.get("hosts", [])
        if not hosts:
            line("No open ports found.")
        else:
            for host in hosts:
                line(f"Host: {host['ip']}", indent=5)
                for port in host["open_ports"]:
                    line(port, indent=10)
        raw = nmap_data.get("raw", "")
        if raw:
            pdf.ln(2)
            line("Raw Nmap output (first 50 lines):")
            pdf.set_font("Courier", "", 6)
            pdf.set_text_color(0, 0, 0)
            for rline in raw.splitlines()[:50]:
                pdf.cell(0, 4, rline.encode("latin-1", "replace").decode("latin-1"), ln=True)
            pdf.set_font("Courier", "", 9)
            pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    # 6. SSL
    section_title("[6] SSL/TLS CERTIFICATE")
    if ssl_info:
        line(f"Subject: {ssl_info['subject']}")
        line(f"Issuer: {ssl_info['issuer']}")
        line(f"Expires: {ssl_info['expires']}")
    else:
        line("Could not retrieve SSL certificate.")
    pdf.ln(4)

    # 7. Nikto
    section_title("[7] WEB VULNERABILITY SCAN (Nikto)")
    if not nikto_findings:
        line("No web vulnerabilities found or scan blocked.")
    else:
        for finding in nikto_findings[:15]:
            line(finding.encode("latin-1", "replace").decode("latin-1"), indent=5)
    pdf.ln(4)

    # 8. Nuclei
    section_title("[8] NUCLEI CVE FINDINGS")
    if not nuclei_findings:
        line("No CVE findings or Nuclei not available.")
    else:
        for finding in nuclei_findings[:20]:
            line(finding.encode("latin-1", "replace").decode("latin-1"), indent=5, color=(255,200,0))
    pdf.ln(4)

    # 9. Advanced recon
    section_title("[9] ADVANCED RECONNAISSANCE")
    line("Technologies:")
    for tech in technologies[:10]:
        line(tech.encode("latin-1", "replace").decode("latin-1"), indent=5)
    line("WAF Detection:")
    for w in waf[:5]:
        line(w.encode("latin-1", "replace").decode("latin-1"), indent=5)
    line("DNS Records (truncated):")
    pdf.set_font("Courier", "", 7)
    for record in dns_records[:15]:
        pdf.cell(0, 4, record.encode("latin-1", "replace").decode("latin-1"), ln=True)
    pdf.set_font("Courier", "", 9)
    line("OSINT Dorks:")
    pdf.set_font("Courier", "", 7)
    for dork in dorks[:10]:
        pdf.cell(0, 4, dork.encode("latin-1", "replace").decode("latin-1"), ln=True)
    pdf.set_font("Courier", "", 9)
    pdf.ln(4)

    # 10. Simulated exploitation
    section_title("[10] SIMULATED EXPLOITATION / VALIDATION")
    if nmap_vulns:
        for vuln in nmap_vulns[:20]:
            line(vuln.encode("latin-1", "replace").decode("latin-1"), indent=5, color=(255,100,100))
    else:
        line("No Nmap vulnerability scripts found issues.")
    pdf.ln(4)

    # 11. Summary
    section_title("[11] EXECUTIVE SUMMARY")
    total_ports = sum(len(h["open_ports"]) for h in nmap_data.get("hosts", []))
    summary = (
        f"Target: {target}\n"
        f"Subdomains: {len(subdomains)}\n"
        f"LinkedIn profiles: {len(linkedin)}\n"
        f"Harvested emails: {len(emails)}\n"
        f"Domain breaches: {domain_breach_count}\n"
        f"Sensitive paths: {len(directories)}\n"
        f"Open ports: {total_ports}\n"
        f"Technologies: {len(technologies)}\n"
        f"WAF: {', '.join(waf) if waf else 'None'}\n"
        f"Nmap vulnerability findings: {len(nmap_vulns)}\n"
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
        f"{len(technologies)} technologies detected, "
        f"{len(nmap_vulns)} Nmap vulnerability findings, "
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

    technologies = _detect_technologies(domain)
    waf = _detect_waf(domain)
    dns_records = _advanced_dns_enum(domain)
    dorks = _generate_dorks(domain)

    validation = _run_safe_validation(domain)
    nmap_vulns = validation.get("nmap_vulns", [])
    nuclei_findings = validation.get("nuclei_findings", [])



    filepath, spoken = _generate_pdf(
        domain, subdomains, linkedin, emails, email_guesses, email_breaches,
        domain_breach_count, domain_breach_names, directories,
        nmap_data, nikto_findings, ssl_info, nuclei_findings,
        technologies, waf, dns_records, dorks, nmap_vulns
    )

    return spoken + "\n" + str(filepath)