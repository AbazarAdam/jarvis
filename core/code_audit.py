"""
core/code_audit.py — Code review and static security audit for JARVIS.

Scans a project folder for common security mistakes, bad patterns, and
maintenance issues. Returns a structured findings list and can generate a
Markdown/text report on the Desktop.

All operations are read-only.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent


IGNORE_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "logs",
    "tools",
    "chrome_profile",
    "vosk_model",
    "ollama_local",
}

IGNORE_FILES = {
    "api_keys.json",
    "long_term.json",
    "shortcuts.json",
    "gmail_token.pickle",
    "gmail_credentials.json",
}

# Severity patterns
HIGH_PATTERNS = [
    (re.compile(r"(password|passwd|pwd|secret|api_key|apikey|token)\s*=\s*['\"][^'\"]+['\"]", re.I), "Hardcoded secret"),
    (re.compile(r"subprocess\.(run|call|Popen)\([^)]*shell=True", re.I), "Subprocess shell=True"),
    (re.compile(r"os\.system\s*\(", re.I), "os.system call"),
    (re.compile(r"eval\s*\(", re.I), "eval() usage"),
    (re.compile(r"exec\s*\(", re.I), "exec() usage"),
    (re.compile(r"pickle\.loads\s*\(", re.I), "Unsafe pickle.loads"),
]

MEDIUM_PATTERNS = [
    (re.compile(r"SELECT .*\+.*FROM", re.I), "SQL concatenation"),
    (re.compile(r"execute\(.*%.*\)", re.I), "SQL parameter interpolation"),
    (re.compile(r"except\s*:\s*pass", re.I), "Bare except pass"),
    (re.compile(r"open\([^)]*['\"]w['\"]", re.I), "File write without context manager"),
]

LOW_PATTERNS = [
    (re.compile(r"TODO|FIXME", re.I), "TODO/FIXME note"),
    (re.compile(r"print\(.*\)", re.I), "Print statement in production"),
]


def _is_ignored(path: Path, root: Path) -> bool:
    parts = {p.name for p in path.parents if p != root.parent}
    return bool(parts.intersection(IGNORE_DIRS))


def audit_codebase(project_path: str | Path) -> dict[str, Any]:
    """
    Scan a project directory and return a structured audit result.

    Returns:
        {
            "project_path": str,
            "audited_at": str,
            "findings": [
                {
                    "file": str,
                    "line": int,
                    "level": "HIGH" | "MEDIUM" | "LOW",
                    "issue": str,
                    "match": str,
                }
            ]
        }
    """
    root = Path(project_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return {"error": "Invalid project path."}

    findings: list[dict[str, Any]] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if _is_ignored(path, root) or path.name in IGNORE_FILES:
            continue
        if path.suffix.lower() not in (".py", ".js", ".ts", ".html", ".css", ".md", ".yml", ".yaml", ".json"):
            continue

        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        rel = str(path.relative_to(root))

        for pattern, description in HIGH_PATTERNS:
            for m in pattern.finditer(source):
                line = source.count("\n", 0, m.start()) + 1
                findings.append({
                    "file": rel,
                    "line": line,
                    "level": "HIGH",
                    "issue": description,
                    "match": m.group(0)[:100],
                })

        for pattern, description in MEDIUM_PATTERNS:
            for m in pattern.finditer(source):
                line = source.count("\n", 0, m.start()) + 1
                findings.append({
                    "file": rel,
                    "line": line,
                    "level": "MEDIUM",
                    "issue": description,
                    "match": m.group(0)[:100],
                })

        for pattern, description in LOW_PATTERNS:
            for m in pattern.finditer(source):
                line = source.count("\n", 0, m.start()) + 1
                findings.append({
                    "file": rel,
                    "line": line,
                    "level": "LOW",
                    "issue": description,
                    "match": m.group(0)[:100],
                })

    # De-duplicate identical findings
    unique = []
    seen = set()
    for f in findings:
        key = (f["file"], f["line"], f["issue"], f["match"])
        if key not in seen:
            seen.add(key)
            unique.append(f)

    return {
        "project_path": str(root),
        "audited_at": datetime.now().isoformat(timespec="seconds"),
        "findings": unique,
    }


def audit_report_text(project_path: str | Path, max_findings: int = 100) -> str:
    """Return a formatted human-readable audit report."""
    data = audit_codebase(project_path)
    if "error" in data:
        return data["error"]

    findings = data["findings"][:max_findings]
    lines = [
        "CODE AUDIT REPORT",
        f"Project: {data['project_path']}",
        f"Generated: {data['audited_at']}",
        "=" * 50,
    ]

    if not findings:
        lines.append("No notable issues found.")
        return "\n".join(lines)

    current_level = None
    for f in findings:
        if f["level"] != current_level:
            current_level = f["level"]
            lines.append(f"\n--- {current_level} ---")
        lines.append(f"[{f['file']}:{f['line']}] {f['issue']} -> {f['match']}")

    lines.append("\nRecommendation: Review flagged items based on severity.")
    return "\n".join(lines)


def save_audit_report(project_path: str | Path, output_dir: str | Path | None = None, format: str = "md") -> str:
    """Save the audit report to the Desktop or a chosen directory."""
    report = audit_report_text(project_path)
    root = Path(project_path).expanduser().resolve()
    out_dir = Path(output_dir) if output_dir else Path.home() / "Desktop"
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_name = root.name.replace(" ", "_")
    suffix = ".md" if format == "md" else ".txt"
    path = out_dir / f"{safe_name}_code_audit{suffix}"
    path.write_text(report, encoding="utf-8")
    return str(path)