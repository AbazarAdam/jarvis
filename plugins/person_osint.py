"""
plugins/person_osint.py — OSINT person search for JARVIS.

Searches public sources for information about a person.
Inputs: name, usernames, email, phone.
Outputs: compiled report.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from actions.web_search import web_search as web_search_action


PLUGIN_INFO = {
    "name": "person_osint",
    "description": (
        "Search for a person across public sources using OSINT techniques. "
        "Use this tool when the user asks to find information about a person, "
        "do social engineering research, investigate an individual, or look up "
        "someone by name, username, email, or phone number. "
        "Examples: 'search for John Doe', 'find info on username johndoe', "
        "'OSINT on email test@example.com', 'who is this phone number?'. "
        "Provide at least one of: name, usernames, email, phone. "
        "Returns a detailed report of findings."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "name": {"type": "STRING", "description": "Full name of the person"},
            "usernames": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "List of known usernames/handles"},
            "email": {"type": "STRING", "description": "Email address"},
            "phone": {"type": "STRING", "description": "Phone number"},
        },
        "required": []
    }
}


def _run_web_search(query: str, player=None) -> str:
    """Use JARVIS web search action."""
    try:
        result = web_search_action(
            parameters={"query": query, "mode": "search", "items": []},
            player=player,
        )
        return result
    except Exception as e:
        return f"Web search failed: {e}"


def _check_sherlock(username: str) -> str:
    """Run Sherlock if installed."""
    if shutil.which("sherlock") is None:
        return None
    try:
        result = subprocess.run(
            ["sherlock", username, "--timeout", "10"],
            capture_output=True, text=True, timeout=120
        )
        return result.stdout[-2000:] if result.stdout else "No output from Sherlock."
    except subprocess.TimeoutExpired:
        return "Sherlock timed out."
    except Exception as e:
        return f"Sherlock failed: {e}"


def _check_hibp(email: str, player=None) -> str:
    """Check email against HaveIBeenPwned if API key exists."""
    import json
    config_path = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
    api_key = None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        api_key = cfg.get("hibp_api_key")
    except Exception:
        pass

    if not api_key:
        return "HaveIBeenPwned API key not configured; skipping breach check."

    import requests
    headers = {"hibp-api-key": api_key}
    url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}?truncateResponse=true"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            breaches = [b.get("Name", "unknown") for b in data]
            return f"Email found in {len(breaches)} breach(es): {', '.join(breaches[:10])}"
        elif resp.status_code == 404:
            return "Email not found in known breaches."
        elif resp.status_code == 401:
            return "Invalid HIBP API key."
        else:
            return f"HIBP check failed (HTTP {resp.status_code})."
    except Exception as e:
        return f"HIBP request failed: {e}"


def execute(parameters: dict, player=None, speak=None) -> str:
    name = (parameters or {}).get("name", "").strip()
    usernames = (parameters or {}).get("usernames", [])
    email = (parameters or {}).get("email", "").strip()
    phone = (parameters or {}).get("phone", "").strip()

    if not name and not usernames and not email and not phone:
        return "Please provide a name, username, email, or phone number, sir."

    findings = []

    if name:
        findings.append(f"### Search results for name: {name}")
        query = f'"{name}"'
        findings.append(_run_web_search(query, player))

    if usernames:
        findings.append("### Username search")
        for uname in usernames:
            # Try Sherlock first, fallback to web search
            sher_result = _check_sherlock(uname)
            if sher_result:
                findings.append(f"\n**Sherlock results for {uname}:**\n{sher_result}")
            else:
                findings.append(f"\n**Web search for username {uname}:**")
                findings.append(_run_web_search(f'"{uname}"', player))

    if email:
        findings.append("### Email breach check")
        findings.append(_check_hibp(email, player))

    if phone:
        findings.append(f"### Phone number search: {phone}")
        findings.append(_run_web_search(f'"{phone}"', player))

    final_report = "\n\n".join(findings)

    if speak:
        speak("OSINT scan completed, sir. Here is the summary.", player=player) if False else speak("OSINT scan completed, sir.")

    return final_report