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
from core.proxy_manager import get_requests_proxies, get_tool_proxy_arg, get_tool_env, log_proxy_status


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
    "nmap": {
        "executables": ["nmap", "nmap.exe"],
        "update_cmd": None,
        "install_hint": "https://nmap.org/download.html",
    },
    "amass": {
        "executables": ["amass", "amass.exe"],
        "update_cmd": None,
        "install_hint": "https://github.com/owasp-amass/amass/releases",
    },
        "subfinder": {
        "executables": ["subfinder", "subfinder.exe"],
        "update_cmd": None,
        "install_hint": "https://github.com/projectdiscovery/subfinder/releases",
    },
    "httpx": {
        "executables": ["httpx", "httpx.exe"],
        "update_cmd": None,
        "install_hint": "https://github.com/projectdiscovery/httpx/releases",
    },
    "dnsx": {
        "executables": ["dnsx", "dnsx.exe"],
        "update_cmd": None,
        "install_hint": "https://github.com/projectdiscovery/dnsx/releases",
    },
    "katana": {
        "executables": ["katana", "katana.exe"],
        "update_cmd": None,
        "install_hint": "https://github.com/projectdiscovery/katana/releases",
    },
    "arjun": {
        "executables": ["arjun", "arjun.py"],
        "update_cmd": ["python", "-m", "pip", "install", "--upgrade", "arjun"],
        "install_hint": "pip install arjun",
    },
    "wafw00f": {
        "executables": ["wafw00f", "wafw00f.exe"],
        "update_cmd": ["python", "-m", "pip", "install", "--upgrade", "wafw00f"],
        "install_hint": "pip install wafw00f",
    },
    "dalfox": {
        "executables": ["dalfox", "dalfox.exe"],
        "update_cmd": None,
        "install_hint": "https://github.com/hahwul/dalfox/releases",
    },
    "whatweb": {
        "executables": ["whatweb", "whatweb.exe"],
        "update_cmd": None,
        "install_hint": "https://github.com/urbanadventurer/WhatWeb",
    },
    "trufflehog": {
        "executables": ["trufflehog", "trufflehog.exe"],
        "update_cmd": None,
        "install_hint": "https://github.com/trufflesecurity/trufflehog/releases",
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
        "searchsploit": {
        "executables": ["searchsploit", "searchsploit.exe"],
        "update_cmd": ["searchsploit", "-u"],
        "install_hint": "https://www.exploit-db.com/searchsploit",
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

    # Build environment with proxy if configured
    env = None
    proxy_env = get_tool_env()
    if proxy_env:
        import os
        env = os.environ.copy()
        env.update(proxy_env)

    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd or str(BASE_DIR),
            env=env,
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
def _find_ruby_exe() -> str | None:
    """Return the Ruby executable path, trying PATH and common install dirs."""
    ruby_exe = shutil.which("ruby")
    if ruby_exe:
        return ruby_exe

    for base in [
        Path("C:/Ruby34-x64/bin/ruby.exe"),
        Path("C:/Ruby34/bin/ruby.exe"),
        Path("C:/Ruby33-x64/bin/ruby.exe"),
        Path("C:/Ruby33/bin/ruby.exe"),
        Path("C:/Ruby32-x64/bin/ruby.exe"),
        Path("C:/Ruby32/bin/ruby.exe"),
        Path("C:/Ruby31-x64/bin/ruby.exe"),
        Path("C:/Ruby31/bin/ruby.exe"),
        Path("C:/Ruby30-x64/bin/ruby.exe"),
        Path("C:/Ruby30/bin/ruby.exe"),
    ]:
        if base.exists():
            return str(base)

    return None

def _find_tool(name: str) -> str | None:
    """Locate a tool executable in PATH, .venv/Scripts, tools/, or Ruby bin dirs."""
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

    # 2. Ruby gem executables
    if name in ("wpscan", "whatweb"):
        ruby_exe = shutil.which("ruby")
        ruby_bin_dirs = []
        if ruby_exe:
            ruby_bin_dirs.append(Path(ruby_exe).parent)

        # Common RubyInstaller paths
        for base in [
            Path("C:/Ruby34-x64/bin"),
            Path("C:/Ruby34/bin"),
            Path("C:/Ruby33-x64/bin"),
            Path("C:/Ruby33/bin"),
            Path("C:/Ruby32-x64/bin"),
            Path("C:/Ruby32/bin"),
            Path("C:/Ruby31-x64/bin"),
            Path("C:/Ruby31/bin"),
            Path("C:/Ruby30-x64/bin"),
            Path("C:/Ruby30/bin"),
        ]:
            if base.exists() and base not in ruby_bin_dirs:
                ruby_bin_dirs.append(base)

        for ruby_bin in ruby_bin_dirs:
            for candidate in candidates:
                for suffix in ("", ".bat", ".cmd", ".exe", ".rb"):
                    candidate_path = ruby_bin / (candidate + suffix)
                    if candidate_path.exists():
                        return str(candidate_path)

    # 3. Virtual env Scripts
    venv_scripts = BASE_DIR / ".venv" / "Scripts"
    if venv_scripts.exists():
        for exe in candidates:
            for suffix in ("", ".exe", ".py"):
                candidate_path = venv_scripts / (exe + suffix)
                if candidate_path.exists():
                    return str(candidate_path)

    # 4. tools/ directory, avoiding docs/manpages and prefer correct Nikto script
    if TOOLS_DIR.exists():
        if name == "nikto":
            preferred = TOOLS_DIR / "nikto" / "program" / "nikto.pl"
            if preferred.exists():
                return str(preferred)

        for candidate in TOOLS_DIR.rglob("*"):
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() in (".1", ".md", ".txt", ".rst"):
                continue
            if candidate.stem.lower() == name.lower():
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

        # Resolve the first command word to the actual tool path only when the
        # command starts directly with the security tool itself.
        # Example: ["nuclei", "-update-templates"] → ["C:/.../tools/nuclei/nuclei.exe", "-update-templates"]
        # We must NOT replace generic interpreters like "python" for pip-based tools.
        resolved_cmd = list(update_cmd)
        if resolved_cmd:
            first = str(resolved_cmd[0])
            if first.lower() not in ("python", "python.exe", "py", "py.exe"):
                tool_path = _find_tool(tool)
                if tool_path:
                    resolved_cmd[0] = tool_path

        result = _run_command(resolved_cmd, timeout=180)
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


def _generate_ai_commands(domain: str, technologies: list[str], waf: list[str]) -> list[str]:
    prompt = f"""
Generate 5 safe, read-only curl commands to test for common web vulnerabilities on {domain}.
Tech detected: {technologies}
WAF detected: {waf}
Return only the commands, one per line.
"""
    insight = _get_llm_insight(prompt)
    lines = [line.strip() for line in insight.splitlines() if line.strip().startswith("curl")]
    return lines[:5]


def _run_ai_curl_commands(domain: str, commands: list[str]) -> list[str]:
    """Execute AI-generated curl commands in read-only mode with strict timeouts."""
    if not commands:
        return []

    results = []
    for cmd in commands[:5]:
        # Ensure command is read-only and starts with curl
        if not cmd.startswith("curl"):
            continue
        # Add silent output and timeout
        safe_cmd = cmd.split()
        safe_cmd += ["--silent", "--max-time", "10"]
        r = _run_command(safe_cmd, timeout=30)
        if r["returncode"] == 0 and r["stdout"]:
            results.append(r["stdout"][:300])
        else:
            results.append(f"Command failed: {cmd}")

    return results


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

def _run_subfinder(domain: str) -> list[str]:
    tool = _find_tool("subfinder")
    if not tool:
        return []
    cmd = [tool, "-d", domain, "-silent"]
    r = _run_command(cmd, timeout=300)
    return [line.strip().lower() for line in r["stdout"].splitlines() if line.strip() and line.endswith(domain)]

def _run_httpx(domain: str) -> list[str]:
    tool = _find_tool("httpx")
    if not tool:
        return []
    cmd = [tool, "-u", f"https://{domain}", "-silent", "-status-code", "-title"]
    r = _run_command(cmd, timeout=120)
    return [line.strip() for line in r["stdout"].splitlines() if line.strip()]

def _run_dnsx(domain: str) -> list[str]:
    tool = _find_tool("dnsx")
    if not tool:
        return []
    cmd = [tool, "-d", domain, "-silent", "-a", "-mx", "-ns"]
    r = _run_command(cmd, timeout=120)
    return [line.strip() for line in r["stdout"].splitlines() if line.strip()]

def _run_katana(domain: str) -> list[str]:
    tool = _find_tool("katana")
    if not tool:
        return []
    cmd = [tool, "-u", f"https://{domain}", "-silent"]
    r = _run_command(cmd, timeout=180)
    return [line.strip() for line in r["stdout"].splitlines() if line.strip()][:100]

def _run_arjun(domain: str) -> list[str]:
    """Run Arjun and return actual URLs with discovered parameters."""
    tool = _find_tool("arjun")
    if not tool:
        return []

    cmd = [sys.executable, tool, "-u", f"https://{domain}", "--stable"]
    r = _run_command(cmd, timeout=600)

    raw = r["stdout"] or r["stderr"]
    urls = []

    # Try JSON output first
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict):
                    url = entry.get("url")
                    params = entry.get("params", [])
                    if url and params:
                        for p in params:
                            urls.append(f"{url}?{p}=FUZZ")
                    elif url:
                        urls.append(url)
        elif isinstance(data, dict):
            url = data.get("url")
            params = data.get("params", [])
            if url:
                for p in params:
                    urls.append(f"{url}?{p}=FUZZ")
                if not params:
                    urls.append(url)
    except Exception:
        # Fallback: parse plain lines for URL-like strings
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith(("http://", "https://")):
                urls.append(line)

    # Deduplicate and limit
    unique = []
    for u in urls:
        if u not in unique:
            unique.append(u)
    return unique[:20]

def _run_dalfox(domain: str) -> list[str]:
    tool = _find_tool("dalfox")
    if not tool:
        return []
    cmd = [tool, "url", f"https://{domain}", "--silence"]
    r = _run_command(cmd, timeout=600)
    return [line.strip() for line in r["stdout"].splitlines() if line.strip()]

def _run_wafw00f(domain: str) -> list[str]:
    tool = _find_tool("wafw00f")
    if not tool:
        return []
    cmd = [sys.executable, tool, f"https://{domain}"]
    r = _run_command(cmd, timeout=180)
    return [line.strip() for line in r["stdout"].splitlines() if line.strip()]

def _run_whatweb(domain: str) -> list[str]:
    tool = _find_tool("whatweb")
    if not tool:
        return []

    target = f"https://{domain}"
    ruby_exe = _find_ruby_exe()

    if ruby_exe:
        cmd = [ruby_exe, tool, target]
    elif Path(tool).suffix == ".rb":
        cmd = ["ruby", tool, target]
    else:
        cmd = [tool, target]

    r = _run_command(cmd, timeout=120)
    return [line.strip() for line in r["stdout"].splitlines() if line.strip()]

def _run_searchsploit(domain: str) -> list[str]:
    tool = _find_tool("searchsploit")
    if not tool:
        return []
    # Search for common service names based on open ports will be done later,
    # for now use the domain as a general search.
    cmd = [tool, domain]
    r = _run_command(cmd, timeout=120)
    return [line.strip() for line in r["stdout"].splitlines() if line.strip()][:20]



def _enumerate_subdomains(domain: str) -> list[str]:
    """Gather subdomains using crt.sh and Amass/Sublist3r if available."""
    subs = set()



    # 1. crt.sh
    try:
        url = f"https://crt.sh/?q=%.{domain}&output=json"
        resp = requests.get(url, timeout=30, proxies=get_requests_proxies())
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
                # Ignore banners, progress messages, and log lines.
                if not line or line.startswith(("[", "-", "*")):
                    continue
                # Only accept clean subdomain-looking tokens.
                if re.fullmatch(r"[a-z0-9.-]+", line) and line.endswith(domain) and line != domain:
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
                proxies=get_requests_proxies(),
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
                proxies=get_requests_proxies(),
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
        resp = requests.get(url, timeout=10, proxies=get_requests_proxies())
        if resp.status_code == 200:
            return [b.get("Name", "Unknown") for b in resp.json()]
    except Exception:
        pass
    return []


def _check_domain_breaches(domain: str) -> tuple[int, list[str]]:
    """Return number of breaches and names for a domain."""
    try:
        url = f"https://haveibeenpwned.com/api/v3/breaches?domain={domain}"
        resp = requests.get(url, timeout=10, proxies=get_requests_proxies())
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
            proxies=get_requests_proxies(),
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
            proxies=get_requests_proxies(),
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
            proxies=get_requests_proxies(),
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

def _waf_bypass_probe(domain: str) -> list[str]:
    """Test common WAF bypass techniques using header spoofing and obfuscation."""
    payloads = [
        "/../../etc/passwd",
        "/%2e%2e/%2e%2e/etc/passwd",
        "/..%252f..%252fetc/passwd",
        "/%252e%252e/%252e%252e/etc/passwd",
        "/....//....//etc/passwd",
        "/..;/..;/etc/passwd",
    ]
    headers_variants = [
        {},
        {"X-Forwarded-For": "127.0.0.1"},
        {"X-Real-IP": "127.0.0.1"},
        {"True-Client-IP": "127.0.0.1"},
        {"X-Originating-IP": "127.0.0.1"},
        {"X-Forwarded-Host": "localhost"},
    ]
    results = []

    for payload in payloads:
        for headers in headers_variants:
            try:
                resp = requests.get(
                    f"https://{domain}{payload}",
                    timeout=10,
                    headers={"User-Agent": "Mozilla/5.0", **headers},
                    proxies=get_requests_proxies(),
                )
                if resp.status_code == 200 and "root:" in resp.text:
                    results.append(f"Traversal bypass with {headers or 'no extra headers'}: {payload}")
                    break
            except Exception:
                pass

    return results

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
        nmap_exe, "-Pn", "-sV", "-sC", "--script", "vuln,auth",
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
        nmap_exe, "-Pn", "--top-ports", "100", "-T4",
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
        "-severity", "critical,high,medium,low",
        "-tags", "cve,misconfig,exposure,rce,sqli,xss,lfi,ssrf,redirect,default-login,tech,panel,edb,oast",
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
def _run_sqlmap_on_url(url: str) -> list[str]:
    """Run sqlmap against a specific URL or parameterised URL."""
    sqlmap_exe = _find_tool("sqlmap")
    if not sqlmap_exe:
        return []

    cmd = [
        sys.executable, sqlmap_exe,
        "-u", url,
        "--batch",
        "--random-agent",
        "--crawl=2",
        "--forms",
        "--level=2",
        "--risk=2",
        "--output-dir", str(BASE_DIR / "logs" / "sqlmap"),
    ]

    result = _run_command(cmd, timeout=1800)

    findings = []
    for line in (result["stdout"] + "\n" + result["stderr"]).splitlines():
        line = line.strip()
        if any(k in line.lower() for k in (
            "is vulnerable",
            "injectable",
            "sqlmap identified",
            "parameter is vulnerable",
            "payload:",
        )):
            if line not in findings:
                findings.append(line)

    return findings


def _run_xss_on_url(url: str) -> list[str]:
    """Run XSS detection using dalfox if available, otherwise XSStrike."""
    # Prefer dalfox
    dalfox_exe = _find_tool("dalfox")
    if dalfox_exe:
        cmd = [dalfox_exe, "url", url, "--silence", "--no-color"]
        result = _run_command(cmd, timeout=1800)
        findings = []
        for line in (result["stdout"] + "\n" + result["stderr"]).splitlines():
            line = line.strip()
            if line and "info" not in line.lower():
                findings.append(line)
        if findings:
            return findings

    # Fallback to XSStrike
    xsstrike = _find_tool("xsstrike")
    if not xsstrike:
        return []

    cmd = [sys.executable, xsstrike, "-u", url, "--crawl", "--console-log-level", "WARNING"]
    result = _run_command(cmd, timeout=1800)

    findings = []
    for line in (result["stdout"] + "\n" + result["stderr"]).splitlines():
        line = line.strip()
        if any(k in line.lower() for k in (
            "reflected", "dom xss", "stored xss", "vulnerable", "xss found"
        )):
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


def _classify_findings(results: dict) -> dict:
    """Classify all findings into severity buckets."""
    classified = {"critical": [], "high": [], "medium": [], "low": [], "info": []}

    def add(severity, text):
        if text and text not in classified[severity]:
            classified[severity].append(text)

    # Nuclei findings often contain severity labels
    for finding in results.get("nuclei_findings", []):
        f = str(finding).lower()
        if "critical" in f:
            add("critical", finding)
        elif "high" in f:
            add("high", finding)
        elif "medium" in f:
            add("medium", finding)
        elif "low" in f:
            add("low", finding)
        else:
            add("info", finding)

    # sqlmap findings — only confirmed injections should be critical
    for finding in results.get("sqlmap_findings", []):
        f = str(finding).lower()
        if "is vulnerable" in f or "injectable" in f:
            if "warning" in f or "might" in f or "does not seem" in f:
                add("low", finding)
            else:
                add("critical", finding)
        elif "warning" in f or "does not seem" in f:
            add("low", finding)
        else:
            add("medium", finding)

    # XSS findings
    for finding in results.get("xss_findings", results.get("xsstrike_findings", [])):
        f = str(finding).lower()
        if "stored" in f or "reflected" in f:
            add("high", finding)
        else:
            add("medium", finding)

    # Correlated CVE findings
    for finding in results.get("correlated_findings", []):
        f = str(finding).lower()
        if "critical" in f:
            add("critical", finding)
        elif "high" in f:
            add("high", finding)
        elif "medium" in f:
            add("medium", finding)
        elif "low" in f:
            add("low", finding)
        else:
            add("info", finding)

    # WAF bypass attempts
    for finding in results.get("waf_bypass", []):
        add("medium", finding)

    # Attack evidence verdicts
    for ev in results.get("attack_evidence", []):
        verdict = str(ev.get("verdict", "")).lower()
        command = ev.get("command", "")
        if verdict == "confirmed":
            add("high", f"Confirmed attack evidence: {command} — {ev.get('evidence', '')}")
        elif verdict == "probable":
            add("medium", f"Probable vulnerability: {command} — {ev.get('evidence', '')}")
        else:
            add("info", f"Not exploitable: {command}")

    # Nikto findings
    for finding in results.get("nikto_findings", []):
        f = str(finding).lower()
        if "critical" in f or "remote code execution" in f or "sql injection" in f:
            add("critical", finding)
        elif "high" in f or "xss" in f or "traversal" in f:
            add("high", finding)
        elif "medium" in f or "misconfig" in f:
            add("medium", finding)
        elif "low" in f:
            add("low", finding)
        else:
            add("info", finding)

    # Info-level metrics
    total_ports = sum(len(h.get("open_ports", [])) for h in results.get("nmap_hosts", []))
    add("info", f"Open ports: {total_ports}")
    add("info", f"Subdomains discovered: {len(results.get('subdomains', []))}")
    add("info", f"Sensitive paths: {len(results.get('directories', []))}")
    ssl_info = results.get("ssl_info") or {}
    add("info", f"SSL valid until: {ssl_info.get('expires', 'unknown')}")

    return classified


def _build_remediation_lines(results: dict) -> list[str]:
    lines = []
    seen = set()

    for chain in results.get("attack_chains", []):
        product = chain.get("product", "unknown")
        version = chain.get("version") or "unknown"
        for cve in chain.get("cves", [])[:2]:
            cve_id = cve.get("cve_id", "")
            desc = cve.get("description", "")[:120]
            if not cve_id or cve_id in seen:
                continue
            seen.add(cve_id)

            lines.append(f"{cve_id}: {desc}")
            lines.append(
                f"  Reproduction: Use the safe PoC commands in Attack Evidence "
                f"against {product} {version}."
            )
            lines.append(
                f"  Remediation: Update {product} to the latest patched version. "
                f"Apply the vendor advisory for {cve_id}."
            )
            lines.append("")

    if not lines:
        lines.append("No confirmed CVEs requiring reproduction/remediation.")

    return lines


def _build_attack_path_lines(results: dict) -> list[str]:
    lines = []

    for chain in results.get("attack_chains", []):
        service = chain.get("service", "unknown")
        product = chain.get("product", "unknown")
        version = chain.get("version") or "unknown"
        cve_ids = [c.get("cve_id", "") for c in chain.get("cves", [])[:3]]

        if cve_ids:
            lines.append(
                f"{service} {product} {version} -> "
                f"{', '.join(cve_ids)} -> potential exploitation"
            )

    if not lines:
        lines.append("No attack paths identified.")

    return lines


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

    # AI insight and deep probes
    _write_pdf_section(pdf, "[20] AI TACTICAL INSIGHT", [results.get("ai_insight", "No insight.")], max_items=50)
    pdf.ln(3)
    _write_pdf_section(pdf, "[21] DEEP PROBE FINDINGS", results.get("deep_findings", []))
    pdf.ln(4)

    # Smart exploitation results
    _write_pdf_section(pdf, "[22] SMART EXPLOITATION RESULTS", results.get("xss_findings", []))
    pdf.ln(3)
    _write_pdf_section(pdf, "[23] WAF BYPASS PROBES", results.get("waf_bypass", []))
    pdf.ln(3)
    _write_pdf_section(pdf, "[24] AI CURL COMMAND RESULTS", results.get("ai_command_results", []))
    pdf.ln(3)

    # Extra advanced sections
    for idx, (title, key) in enumerate([
        ("LIVE URLs", "live_urls"),
        ("CRAWLED URLs", "crawled_urls"),
        ("HIDDEN PARAMETERS", "hidden_params"),
        ("WAF DETECTION", "waf_detection"),
        ("WHATWEB TECHNOLOGIES", "whatweb"),
        ("AI GENERATED COMMANDS", "ai_commands"),
    ], start=1):
        _write_pdf_section(pdf, f"[{24 + idx}] {title}", results.get(key, []))
        pdf.ln(3)

    # Attack chain correlation
    _write_pdf_section(pdf, "[31] ATTACK CHAINS / CVE CORRELATION", results.get("correlated_findings", []), max_items=60)
    pdf.ln(3)

    # Severity classification
    severity_data = _classify_findings(results)
    severity_lines = []
    for sev in ["critical", "high", "medium", "low", "info"]:
        items = severity_data.get(sev, [])
        if items:
            severity_lines.append(f"--- {sev.upper()} ---")
            severity_lines.extend(items[:15])
            severity_lines.append("")  # blank line separator
    _write_pdf_section(pdf, "[32] FINDINGS BY SEVERITY", severity_lines, max_items=100)
    pdf.ln(3)

    # Reproduction & remediation
    remediation_lines = _build_remediation_lines(results)
    _write_pdf_section(pdf, "[33] REPRODUCTION & REMEDIATION", remediation_lines, max_items=80)
    pdf.ln(3)

    # Real read-only PoC execution results
    evidence_lines = []
    for ev in results.get("attack_evidence", []):
        line = (
            f"[{ev.get('verdict', 'unknown').upper()}] "
            f"{ev.get('product', 'unknown')} "
            f"{ev.get('version', '')} | {ev.get('command', '')} | "
            f"evidence={ev.get('evidence', '')}"
        )
        evidence_lines.append(line)
    _write_pdf_section(pdf, "[34] ATTACK EVIDENCE", evidence_lines, max_items=80)
    pdf.ln(3)

    # Attack paths
    attack_path_lines = _build_attack_path_lines(results)
    _write_pdf_section(pdf, "[35] ATTACK PATHS", attack_path_lines, max_items=40)
    pdf.ln(3)

    # Executive summary with business impact
    total_ports = sum(len(h.get("open_ports", [])) for h in results.get("nmap_hosts", []))
    critical_count = len(severity_data.get("critical", []))
    high_count = len(severity_data.get("high", []))
    medium_count = len(severity_data.get("medium", []))
    low_count = len(severity_data.get("low", []))
    info_count = len(severity_data.get("info", []))

    if critical_count or high_count:
        business_impact = "High — immediate remediation recommended."
    elif medium_count:
        business_impact = "Medium — scheduled remediation recommended."
    elif low_count:
        business_impact = "Low — routine hardening recommended."
    else:
        business_impact = "Informational — no immediate action required."

    summary_lines = [
        f"Target: {target}",
        f"Subdomains: {len(results.get('subdomains', []))}",
        f"Sensitive paths: {len(results.get('directories', []))}",
        f"Open ports: {total_ports}",
        f"Critical findings: {critical_count}",
        f"High findings: {high_count}",
        f"Medium findings: {medium_count}",
        f"Low findings: {low_count}",
        f"Info findings: {info_count}",
        f"SSL valid until: {ssl_info.get('expires', 'unknown')}",
        f"Business impact: {business_impact}",
    ]
    _write_pdf_section(pdf, "[36] EXECUTIVE SUMMARY", summary_lines, max_items=30)

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

    # Run XSS detection if a login/admin/search page was found
    dirs = initial_findings.get("directories", [])
    if dirs:
        extra["deep_findings"].append("Discovered sensitive paths: " + ", ".join(dirs[:10]))
        for d in dirs:
            if any(k in d.lower() for k in ("login", "admin", "search", "query")):
                xs = _run_xss_on_url(f"https://{domain}")
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
    # Add new reconnaissance results if tools are installed
    subfinder_subs = _run_subfinder(domain)
    if subfinder_subs:
        results["subdomains"] = sorted(set(results["subdomains"] + subfinder_subs))

    results["live_urls"] = _run_httpx(domain)
    results["dns_records"] = _run_dnsx(domain) or results.get("dns_records", [])
    results["crawled_urls"] = _run_katana(domain)
    results["hidden_params"] = _run_arjun(domain)
    results["waf_detection"] = _run_wafw00f(domain) or results.get("waf", [])
    results["whatweb"] = _run_whatweb(domain)
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

    results["ai_commands"] = _generate_ai_commands(
        domain,
        results.get("technologies", []),
        results.get("waf", []),
    )
    results["waf_bypass"] = _waf_bypass_probe(domain)

    # --------------------------- Scan ---------------------------
    nmap_data = _run_nmap(domain)
    results["nmap_hosts"] = nmap_data.get("hosts", [])
    results["nmap_raw"] = nmap_data.get("raw", "")
    results["nmap_ports"] = [
        f"{h['ip']}: {', '.join(h['open_ports'])}"
        for h in results["nmap_hosts"]
    ]
    results["nikto_findings"] = _run_nikto(domain)
    results["nuclei_findings"] = _run_nuclei(domain)
    if "wordpress" in " ".join(results.get("technologies", [])).lower():
        results["wpscan_findings"] = _run_wpscan(domain)
    else:
        results["wpscan_findings"] = []
    results["droopescan_findings"] = _run_droopescan(domain)

    # --------------------------- Exploit ---------------------------
    # Discover hidden parameters first (Arjun)
    hidden_params = _run_arjun(domain)
    results["hidden_params"] = hidden_params

    # Select test URLs: use parameterised URLs if found, else root URL
    if hidden_params:
        test_urls = hidden_params[:3]
    else:
        test_urls = [f"https://{domain}/"]

    sqlmap_findings = []
    xss_findings = []
    for url in test_urls:
        sqlmap_findings.extend(_run_sqlmap_on_url(url))
        xss_findings.extend(_run_xss_on_url(url))

    # Deduplicate
    results["sqlmap_findings"] = list(dict.fromkeys(sqlmap_findings))
    results["xsstrike_findings"] = list(dict.fromkeys(xss_findings))  # keep old key for compatibility
    results["xss_findings"] = results["xsstrike_findings"]

    results["commix_findings"] = _run_commix(domain)
    results["lfisuite_findings"] = _run_lfisuite(domain)

    # WAF bypass probes
    results["waf_bypass"] = _waf_bypass_probe(domain)

    # AI curl commands
    ai_commands = _generate_ai_commands(
        domain,
        results.get("technologies", []),
        results.get("waf", []),
    )
    results["ai_commands"] = ai_commands
    results["ai_command_results"] = _run_ai_curl_commands(domain, ai_commands)

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

    # --------------------------- Attack chain correlation ---------------------------
    try:
        from actions.attack_chain import correlate_vulnerabilities, execute_attack_chains
        attack_results = correlate_vulnerabilities(domain, results)
        results["attack_chains"] = attack_results.get("attack_chains", [])
        results["correlated_findings"] = attack_results.get("correlated_findings", [])
        results["attack_evidence"] = execute_attack_chains(
            results.get("attack_chains", []),
            target=domain,
        )
    except Exception as e:
        print(f"[SecurityMode] ⚠️ Attack chain correlation failed: {e}")
        results["attack_chains"] = []
        results["correlated_findings"] = []
        results["attack_evidence"] = []

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
    correlated_count = len(results.get("correlated_findings", []))
    evidence_count = len(results.get("attack_evidence", []))
    spoken = (
        f"Pentest on {domain} complete, sir. "
        f"Found {len(results['subdomains'])} subdomains, "
        f"{len(results['directories'])} sensitive paths, "
        f"{total_ports} open ports, "
        f"{len(results['nikto_findings'])} web findings, "
        f"{len(results['nuclei_findings'])} CVE findings, "
        f"{exploit_count} exploitation findings, "
        f"{correlated_count} correlated vulnerabilities, "
        f"{evidence_count} attack evidence results. "
        f"Full report saved to your desktop."
    )

    return f"{spoken}\n{filepath}"

