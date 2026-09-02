"""
plugins/person_osint.py — Advanced OSINT Person Search for JARVIS.

Performs passive OSINT using free sources only:
  - Search engine queries (DuckDuckGo primary, Bing fallback optional)
  - Username enumeration with Sherlock
  - Social media profile existence checks
  - Email breach lookup via HaveIBeenPwned (if API key present)
  - Phone number analysis with phonenumbers + web search
  - Aggregated report with sources

No paid APIs are used. All results are from public, freely accessible sources.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import requests

from actions.web_search import web_search as web_search_action


PLUGIN_INFO = {
    "name": "person_osint",
    "description": (
        "Perform deep OSINT reconnaissance on a person using free public sources. "
        "Provide a full name, username, email, or phone number. "
        "Returns a detailed report including social media profiles, possible locations, "
        "education, work history, breach data, and more. "
        "Use this tool for any person-search, social engineering research, or background investigation."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "name": {"type": "STRING", "description": "Full name of the person"},
            "usernames": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "List of known usernames/handles"},
            "email": {"type": "STRING", "description": "Email address"},
            "phone": {"type": "STRING", "description": "Phone number in international format"},
            "location": {"type": "STRING", "description": "Known city/country to narrow search"},
            "org": {"type": "STRING", "description": "Known organization/university/workplace"},
        },
        "required": []
    }
}


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
def _web_search(query: str, player=None) -> str:
    """Use JARVIS web_search action (now DDG-first)."""
    try:
        result = web_search_action(
            parameters={"query": query, "mode": "search", "items": []},
            player=player,
        )
        return result
    except Exception as e:
        return f"Search failed: {e}"


def _ddg_search_raw(query: str, max_results: int = 8) -> list[dict]:
    """Direct DuckDuckGo search for more control. Never raises."""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return []

    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "url": r.get("href", ""),
                })
        return results
    except Exception as e:
        print(f"[OSINT] DDG raw failed: {e}")
        return []


def _generate_usernames(full_name: str) -> list[str]:
    """Generate common username patterns from a full name."""
    parts = full_name.lower().split()
    if not parts:
        return []
    first = parts[0]
    last = parts[-1] if len(parts) > 1 else ""
    candidates = set()
    if last:
        candidates.add(f"{first}{last}")
        candidates.add(f"{first}.{last}")
        candidates.add(f"{first}_{last}")
        candidates.add(f"{last}{first}")
        candidates.add(f"{first}-{last}")
        candidates.add(f"{last}.{first}")
    candidates.add(first)
    # Add initials
    if len(parts) >= 2:
        initials = "".join([p[0] for p in parts])
        candidates.add(initials)
        if last:
            candidates.add(f"{first[0]}{last}")
            candidates.add(f"{first}{last[0]}")
    return list(candidates)


def _check_social_media(username: str) -> list[dict]:
    """Check reliable platforms for profile existence."""
    platforms = [
        ("GitHub", f"https://api.github.com/users/{username}"),
        ("Reddit", f"https://www.reddit.com/user/{username}/about.json"),
    ]
    found = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    for name, url in platforms:
        try:
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                # GitHub: login field exists; Reddit: data is dict with kind
                if name == "GitHub" and "login" in data:
                    found.append({"platform": name, "url": f"https://github.com/{username}", "status": "profile exists"})
                elif name == "Reddit" and data.get("kind") == "t2":
                    found.append({"platform": name, "url": f"https://www.reddit.com/user/{username}", "status": "profile exists"})
                else:
                    found.append({"platform": name, "url": url, "status": "not found"})
            elif resp.status_code == 404:
                found.append({"platform": name, "url": url, "status": "not found"})
            else:
                found.append({"platform": name, "url": url, "status": "check failed"})
        except Exception:
            found.append({"platform": name, "url": url, "status": "check failed"})
    return found


def _run_sherlock(username: str) -> str:
    """Run Sherlock for username enumeration."""
    if shutil.which("sherlock") is None:
        return None
    try:
        result = subprocess.run(
            ["sherlock", username, "--timeout", "5", "--print-found"],
            capture_output=True, text=True, timeout=30
        )
        output = result.stdout
        if "No username found" in output:
            return "No social media profiles found via Sherlock."
        return output[-3000:] if output else "Sherlock produced no output."
    except subprocess.TimeoutExpired:
        return "Sherlock timed out."
    except Exception as e:
        return f"Sherlock failed: {e}"


def _check_email_breach(email: str) -> str:
    """Check email against HaveIBeenPwned if API key exists."""
    config_path = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
    api_key = None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        api_key = cfg.get("hibp_api_key")
    except Exception:
        pass

    if not api_key:
        return "HIBP API key not configured; skipping breach check."

    headers = {"hibp-api-key": api_key}
    url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{quote(email)}?truncateResponse=true"
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


def _phone_analysis(phone: str) -> str:
    """Analyze phone number validity and region using phonenumbers."""
    try:
        import phonenumbers
        from phonenumbers import carrier, timezone

        parsed = phonenumbers.parse(phone, None)
        valid = phonenumbers.is_valid_number(parsed)
        possible = phonenumbers.is_possible_number(parsed)
        region = phonenumbers.region_code_for_number(parsed)
        carrier_name = carrier.name_for_number(parsed, "en") or "unknown"
        timezones = timezone.time_zones_for_number(parsed)
        return (f"Valid: {valid}, Possible: {possible}, Region: {region}, "
                f"Carrier: {carrier_name}, Timezones: {', '.join(timezones)}")
    except Exception as e:
        return f"Phone analysis failed: {e}"


def _generate_report(results: dict) -> str:
    """Format results into a clean report."""
    lines = []
    lines.append("OSINT INVESTIGATION REPORT")
    lines.append("=" * 50)

    if results.get("name_search"):
        lines.append(f"\n🔎 NAME SEARCH")
        lines.append(results["name_search"])

    if results.get("username_results"):
        lines.append(f"\n👤 USERNAME SEARCH")
        lines.append(results["username_results"])

    if results.get("social_media"):
        lines.append(f"\n🌐 SOCIAL MEDIA PROFILE CHECKS")
        for profile in results["social_media"]:
            status = profile.get("status", "")
            if "likely exists" in status:
                lines.append(f"  ✅ {profile['platform']}: {profile['url']}")
            elif "not found" in status:
                lines.append(f"  ❌ {profile['platform']}: not found")
            else:
                lines.append(f"  ⚠️ {profile['platform']}: {status}")

    if results.get("email_breach"):
        lines.append(f"\n📧 EMAIL BREACH CHECK")
        lines.append(results["email_breach"])

    if results.get("phone"):
        lines.append(f"\n📱 PHONE NUMBER")
        lines.append(results["phone"])

    if results.get("sherlock"):
        lines.append(f"\n🕵️ SHERLOCK USERNAME ENUMERATION")
        lines.append(results["sherlock"])

    lines.append("\n" + "=" * 50)
    lines.append("Note: All data gathered from public sources only.")
    return "\n".join(lines)


def execute(parameters: dict, player=None, speak=None) -> str:
    name = (parameters or {}).get("name", "").strip()
    usernames = (parameters or {}).get("usernames", [])
    email = (parameters or {}).get("email", "").strip()
    phone = (parameters or {}).get("phone", "").strip()
    location = (parameters or {}).get("location", "").strip()
    org = (parameters or {}).get("org", "").strip()

    if not any([name, usernames, email, phone]):
        return "Please provide at least one identifier, sir."

    results = {}

    # 1. Name search with direct DDG queries
    if name:
        queries = [f'"{name}"']
        if location:
            queries.append(f'"{name}" {location}')
        if org:
            queries.append(f'"{name}" {org}')
        queries.append(f'"{name}" LinkedIn')
        queries.append(f'"{name}" Facebook')
        queries.append(f'"{name}" Instagram')
        queries.append(f'"{name}" university OR college OR education')
        queries.append(f'"{name}" work OR job OR company')
        # Also add raw queries with DDG formatting
        query_results = []
        for q in queries:
            raw = _ddg_search_raw(q, max_results=5)
            if raw:
                query_results.append(f"### {q}\n" + "\n".join([f"{r['title']}\n{r['snippet']}\n{r['url']}" for r in raw]))
            else:
                query_results.append(f"### {q}\nNo results from DDG.")
        results["name_search"] = "\n\n".join(query_results)

    # 2. Username enumeration (provided or generated from name)
    if usernames:
        candidates = list(usernames)
    elif name:
        candidates = _generate_usernames(name)
    else:
        candidates = []

    if candidates:
        username_report = []
        all_social = []
        sherlock_reports = []
        for uname in candidates[:8]:  # limit to avoid too many requests
            # Social media check
            social = _check_social_media(uname)
            for s in social:
                if s["status"] == "profile likely exists":
                    all_social.append(s)
            # Sherlock
            sher = _run_sherlock(uname)
            if sher and "No social media profiles" not in sher:
                sherlock_reports.append(f"Username '{uname}':\n{sher}")
            # Also search username on DDG
            username_report.append(f"Username '{uname}':\n" + _web_search(f'"{uname}"', player))

        results["social_media"] = all_social
        if username_report:
            results["username_results"] = "\n\n".join(username_report)
        if sherlock_reports:
            results["sherlock"] = "\n\n".join(sherlock_reports)

    # 3. Email breach
    if email:
        results["email_breach"] = _check_email_breach(email)

    # 4. Phone analysis and search
    if phone:
        analysis = _phone_analysis(phone)
        searches = []
        queries = [f'"{phone}"', f'"{phone}" name', f'"{phone}" social media', f'"{phone}" address']
        for q in queries:
            raw = _ddg_search_raw(q, max_results=3)
            if raw:
                searches.append(f"### {q}\n" + "\n".join([f"{r['title']}\n{r['snippet']}\n{r['url']}" for r in raw]))
            else:
                searches.append(f"### {q}\nNo results.")
        results["phone"] = f"{analysis}\n\nSearch results:\n" + "\n\n".join(searches)

    return _generate_report(results)