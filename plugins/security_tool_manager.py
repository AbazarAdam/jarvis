"""
plugins/security_tool_manager.py — JARVIS Security Tool Manager.

Installs, updates, and checks open‑source security tools used by security_mode.
All actions are logged to logs/security_tools.log.
"""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from datetime import datetime

import requests


PLUGIN_INFO = {
    "name": "security_tool_manager",
    "description": (
        "Install, update, and check security tools for JARVIS red‑team engine. "
        "Use for 'install security tools', 'update security tools', 'security tool status'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "install | update | status | rollback"
            }
        },
        "required": ["action"]
    }
}


BASE_DIR  = Path(__file__).resolve().parent.parent
TOOLS_DIR = BASE_DIR / "tools"
LOG_DIR   = BASE_DIR / "logs"
LOG_FILE  = LOG_DIR / "security_tools.log"


# Python packages installable via pip
PIP_TOOLS = {
    "dirsearch": "dirsearch",
    "sublist3r": "sublist3r",
    "droopescan": "droopescan",
    "arjun": "arjun",
    "wafw00f": "wafw00f",
    "sqlmap": "sqlmap",
}

# Git clones
GIT_TOOLS = {
    "xsstrike": {
        "repo": "https://github.com/s0md3v/XSStrike.git",
        "folder": "xsstrike",
    },
    "commix": {
        "repo": "https://github.com/commixproject/commix.git",
        "folder": "commix",
    },
    "lfisuite": {
        "repo": "https://github.com/D35m0nd142/LFISuite.git",
        "folder": "lfisuite",
    },
    "whatweb": {
        "repo": "https://github.com/urbanadventurer/WhatWeb.git",
        "folder": "whatweb",
    },
}

# Binary tools downloaded from GitHub latest release
BINARY_TOOLS = {
    "amass": {
        "repo": "owasp-amass/amass",
        "asset_patterns": ["windows_amd64.zip", "windows_amd64.tar.gz", "windows-amd64.zip"],
    },
    "gobuster": {
        "repo": "OJ/gobuster",
        "asset_patterns": ["Windows_x86_64.zip", "windows_amd64.zip"],
    },
    "subfinder": {
        "repo": "projectdiscovery/subfinder",
        "asset_patterns": ["windows_amd64.zip", "windows_amd64.zip"],
    },
    "httpx": {
        "repo": "projectdiscovery/httpx",
        "asset_patterns": ["windows_amd64.zip", "windows_amd64.zip"],
    },
    "dnsx": {
        "repo": "projectdiscovery/dnsx",
        "asset_patterns": ["windows_amd64.zip", "windows_amd64.zip"],
    },
    "katana": {
        "repo": "projectdiscovery/katana",
        "asset_patterns": ["windows_amd64.zip", "windows_amd64.zip"],
    },
    "dalfox": {
        "repo": "hahwul/dalfox",
        "asset_patterns": ["windows_amd64.tar.gz", "windows_amd64.zip", "windows_amd64"],
    },
    "trufflehog": {
        "repo": "trufflesecurity/trufflehog",
        "asset_patterns": ["windows_amd64.tar.gz", "windows_amd64.zip", "windows_amd64"],
    },
    "ffuf": {
        "repo": "ffuf/ffuf",
        "asset_patterns": ["windows_amd64.zip", "Windows_x86_64.zip"],
    },
}

# Tools that cannot be auto-installed reliably on Windows yet
MANUAL_WINDOWS_TOOLS = {
    "searchsploit",
}


def _log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


def _run(cmd: list, timeout: int = 900, cwd: str = None) -> tuple[int, str, str]:
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
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "Command timed out."
    except FileNotFoundError:
        return 127, "", "Command not found."
    except Exception as e:
        return 1, "", str(e)


def _find_tool(name: str) -> str | None:
    """Find an executable by name in PATH, .venv/Scripts, or tools/ recursively."""
    # 1. PATH
    for exe in (name, name + ".exe"):
        path = shutil.which(exe)
        if path:
            return path

    # 2. Virtual env Scripts
    venv_scripts = BASE_DIR / ".venv" / "Scripts"
    if venv_scripts.exists():
        for candidate in venv_scripts.iterdir():
            if candidate.is_file() and candidate.stem.lower() == name.lower():
                return str(candidate)

    # 3. tools/ directory recursively
    if TOOLS_DIR.exists():
        for candidate in TOOLS_DIR.rglob("*"):
            if candidate.is_file() and candidate.stem.lower() == name.lower():
                return str(candidate)

    return None


def _tool_exists(name: str) -> bool:
    return _find_tool(name) is not None


def install_pip_tools() -> list[str]:
    results = []
    for name, package in PIP_TOOLS.items():
        if _tool_exists(name):
            results.append(f"{name}: already installed")
            continue
        code, out, err = _run([sys.executable, "-m", "pip", "install", package], timeout=600)
        if code == 0:
            results.append(f"{name}: installed")
            _log(f"Installed {name}")
        else:
            results.append(f"{name}: FAILED — {err[:120]}")
            _log(f"Failed to install {name}: {err[:200]}")
    return results


def install_git_tools() -> list[str]:
    if shutil.which("git") is None:
        return ["git: not found — cannot install git-based tools"]
    results = []
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    for name, info in GIT_TOOLS.items():
        target = TOOLS_DIR / info["folder"]
        if target.exists():
            results.append(f"{name}: already present")
            continue
        code, out, err = _run(["git", "clone", "--depth", "1", info["repo"], str(target)], timeout=600)
        if code == 0:
            results.append(f"{name}: cloned")
            _log(f"Cloned {name}")
        else:
            results.append(f"{name}: FAILED — {err[:120]}")
            _log(f"Failed to clone {name}: {err[:200]}")
    return results


def _get_latest_release_asset(repo: str, patterns: list[str]) -> str | None:
    """Return download URL for the latest matching release asset."""
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        headers = {"User-Agent": "JARVIS"}
        resp = requests.get(api_url, timeout=30, headers=headers)
        if resp.status_code != 200:
            return None
        data = resp.json()
        for asset in data.get("assets", []):
            name = asset.get("name", "").lower()
            if any(pattern.lower() in name for pattern in patterns):
                return asset.get("browser_download_url")
    except Exception:
        pass
    return None


def install_binary_tools() -> list[str]:
    """Download and extract Windows binaries from latest GitHub releases."""
    import tarfile
    results = []
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)

    for name, info in BINARY_TOOLS.items():
        if _tool_exists(name):
            results.append(f"{name}: already installed")
            continue

        url = _get_latest_release_asset(info["repo"], info["asset_patterns"])
        if not url:
            results.append(f"{name}: no suitable Windows binary found")
            _log(f"No release asset for {name}")
            continue

        dest_dir = TOOLS_DIR / name
        dest_dir.mkdir(parents=True, exist_ok=True)
        archive_path = dest_dir / f"{name}.download"

        try:
            r = requests.get(url, timeout=180, stream=True)
            if r.status_code == 200:
                with open(archive_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

                if zipfile.is_zipfile(archive_path):
                    with zipfile.ZipFile(archive_path) as z:
                        z.extractall(dest_dir)
                elif tarfile.is_tarfile(archive_path):
                    with tarfile.open(archive_path) as tar:
                        tar.extractall(dest_dir)
                else:
                    results.append(f"{name}: unsupported archive format")
                    _log(f"Downloaded {name} archive is not zip or tar")
                    archive_path.unlink(missing_ok=True)
                    continue

                archive_path.unlink(missing_ok=True)

                # Verify extracted tool exists
                if _find_tool(name):
                    results.append(f"{name}: installed")
                    _log(f"Installed {name} from {url}")
                else:
                    results.append(f"{name}: downloaded but executable not detected")
                    _log(f"Downloaded {name} but could not find executable")
            else:
                results.append(f"{name}: download failed HTTP {r.status_code}")
        except Exception as e:
            results.append(f"{name}: download failed — {str(e)[:120]}")
            _log(f"Failed to download {name}: {e}")

    return results


def install_wpscan() -> str:
    """Install WPScan via Ruby gem, if Ruby is available."""
    if shutil.which("gem") is None:
        return "wpscan: Ruby not installed — install Ruby and try again"
    code, out, err = _run(["gem", "install", "wpscan"], timeout=900)
    if code == 0:
        return "wpscan: installed"
    return f"wpscan: FAILED — {err[:120]}"


def update_all_tools() -> list[str]:
    results = []

    for name, package in PIP_TOOLS.items():
        if not _tool_exists(name):
            continue
        code, out, err = _run([sys.executable, "-m", "pip", "install", "--upgrade", package], timeout=600)
        if code == 0:
            results.append(f"{name}: updated")
        else:
            results.append(f"{name}: update failed — {err[:120]}")

    if shutil.which("git"):
        for name, info in GIT_TOOLS.items():
            target = TOOLS_DIR / info["folder"]
            if not target.exists():
                continue
            code, out, err = _run(["git", "-C", str(target), "pull"], timeout=300)
            if code == 0:
                results.append(f"{name}: updated")
            else:
                results.append(f"{name}: update failed — {err[:120]}")

    nuclei_path = _find_tool("nuclei")
    if nuclei_path:
        code, out, err = _run([nuclei_path, "-update-templates"], timeout=600)
        if code == 0:
            results.append("nuclei: templates updated")
        else:
            results.append(f"nuclei: template update failed — {err[:120]}")

    return results


def status_tools() -> list[str]:
    names = set(PIP_TOOLS) | set(GIT_TOOLS) | set(BINARY_TOOLS) | {"nuclei", "nikto", "wpscan"} | MANUAL_WINDOWS_TOOLS
    lines = []
    for name in sorted(names):
        status = "installed" if _tool_exists(name) else "missing"
        lines.append(f"{name}: {status}")
    return lines


def rollback_last_install() -> str:
    return (
        "Automatic rollback is not configured for external security tools. "
        "Review logs/security_tools.log to identify failed installs and remove them manually."
    )


def execute(parameters: dict, player=None, speak=None) -> str:
    action = (parameters or {}).get("action", "").lower().strip()

    if action in ("install", "install_tools"):
        results = []
        results += install_pip_tools()
        results += install_git_tools()
        results += install_binary_tools()
        results.append(install_wpscan())
        summary = "\n".join(results)
        _log(f"Install action completed:\n{summary}")
        return "Security tool installation report:\n" + summary

    elif action in ("update", "update_tools"):
        results = update_all_tools()
        summary = "\n".join(results) if results else "(no updatable tools found)"
        _log(f"Update action completed:\n{summary}")
        return "Security tool update report:\n" + summary

    elif action in ("status", "list_tools"):
        lines = status_tools()
        _log("Status check requested.")
        return "Security tool status:\n" + "\n".join(lines)

    elif action == "rollback":
        return rollback_last_install()

    return f"Unknown security_tool_manager action: {action}"