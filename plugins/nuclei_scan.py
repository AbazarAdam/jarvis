"""
Nuclei vulnerability scanner plugin for JARVIS.
Uses ProjectDiscovery Nuclei to scan a target for known CVEs.
"""

import subprocess
import shutil
import json
from pathlib import Path

PLUGIN_INFO = {
    "name": "nuclei_scan",
    "description": (
        "Scans a target with Nuclei for known vulnerabilities and CVE templates. "
        "Use for 'nuclei scan example.com', 'scan for CVEs on example.com'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "target": {"type": "STRING", "description": "Target URL or IP address to scan"},
            "severity": {"type": "STRING", "description": "Severity levels: critical,high,medium,low,info (default: critical,high,medium)"},
            "tags": {"type": "STRING", "description": "Optional comma-separated tags (default: cve)"}
        },
        "required": ["target"]
    }
}


def _find_nuclei():
    """Locate nuclei executable."""
    tools_dir = Path(__file__).resolve().parent.parent / "tools"
    candidate = tools_dir / "nuclei" / "nuclei.exe"
    if candidate.exists():
        return str(candidate)
    path = shutil.which("nuclei")
    return path


def _normalise_target(target: str) -> str:
    """Ensure the target has a scheme."""
    if not target.startswith(("http://", "https://")):
        return "https://" + target
    return target


def execute(parameters: dict, player=None, speak=None) -> str:
    target = (parameters or {}).get("target", "").strip()
    if not target:
        return "No target specified for Nuclei scan."

    nuclei_exe = _find_nuclei()
    if not nuclei_exe:
        return (
            "Nuclei not found. Please download nuclei.exe and place it in "
            "tools/nuclei/nuclei.exe"
        )

    target = _normalise_target(target)
    severity = parameters.get("severity", "critical,high,medium")
    tags = parameters.get("tags", "cve")

    cmd = [
        nuclei_exe,
        "-u", target,
        "-severity", severity,
        "-tags", tags,
        "-silent",
        "-jsonl",
        "-timeout", "10",
        "-retries", "1",
        "-c", "25",
        "-no-color",
    ]

    if speak:
        speak(f"Running CVE scan on {target}, sir. This may take a minute.")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # up to 10 minutes
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        output = proc.stdout.strip()
        if not output:
            return f"Nuclei scan completed with no CVE findings for {target}."

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

        if not findings:
            return f"Nuclei scan completed but no vulnerabilities identified for {target}."

        limited = findings[:20]
        summary = "\n".join(limited)
        return (
            f"Nuclei scan on {target} found {len(findings)} finding(s):\n{summary}"
            + (f"\n...and {len(findings)-20} more" if len(findings) > 20 else "")
        )

    except subprocess.TimeoutExpired:
        return "Nuclei scan timed out after 10 minutes."
    except Exception as e:
        return f"Nuclei scan failed: {e}"