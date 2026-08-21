"""
plugins/codebase_plugin.py — Codebase understanding interface for JARVIS.

This plugin gives JARVIS read-only project awareness.

Actions:
    index    — scan a project folder and build a codebase index
    summary  — return a short spoken project summary
    search   — search for functions, classes, imports, or routes
    auth     — find authentication-related code
    routes   — find likely routes/endpoints
"""

from __future__ import annotations

from pathlib import Path

from core.codebase_indexer import (
    find_auth_logic,
    find_routes,
    index_project,
    project_summary,
    search_symbol,
)


PLUGIN_INFO = {
    "name": "codebase_insight",
    "description": (
        "Understand and review any project codebase. Supports codebase indexing, "
        "project summaries, symbol search, authentication/route discovery, and "
        "security code review. The user can provide any project folder path. "
        "Use project_path='.' only when they ask about the current JARVIS project. "
        "Use for questions like 'summarise this project', 'find API routes', "
        "'where is the login logic', 'search for a function', or 'review my code'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "index | summary | search | auth | routes"
            },
            "project_path": {
                "type": "STRING",
                "description": "Path to the project folder. Use '.' for the current JARVIS project."
            },
            "query": {
                "type": "STRING",
                "description": "Search term for functions, classes, routes, or imports."
            }
        },
        "required": ["action"]
    }
}


def _default_path(parameters: dict) -> Path:
    raw = (parameters or {}).get("project_path", "").strip()
    return Path(raw) if raw else Path(".")


def execute(parameters: dict, player=None, speak=None) -> str:
    action = (parameters or {}).get("action", "").lower().strip()
    project_path = _default_path(parameters)

    if action == "index":
        if speak:
            speak("Indexing the project now, sir.")
        index = index_project(project_path)
        if index.get("error"):
            return index["error"]
        return (
            f"Project indexed. {index.get('file_count')} files and "
            f"{index.get('symbol_count')} symbols found."
        )

    elif action == "summary":
        return project_summary(project_path)

    elif action == "search":
        query = (parameters or {}).get("query", "").strip()
        if not query:
            return "Please provide a search query, sir."
        matches = search_symbol(query, index_project(project_path))
        if not matches:
            return f"No symbols found for '{query}'."
        lines = [f"{m.get('kind')}: {m.get('name')} ({m.get('file')})" for m in matches[:10]]
        return "Found:\n" + "\n".join(lines)

    elif action == "auth":
        results = find_auth_logic(project_path)
        if not results:
            return "No authentication-related code found."
        return "Authentication-related locations:\n" + "\n".join(results[:10])

    elif action == "routes":
        results = find_routes(project_path)
        if not results:
            return "No routes found."
        return "Possible routes:\n" + "\n".join(results[:10])

    elif action == "review":
        from core.code_audit import audit_report_text, save_audit_report

        save = parameters.get("save", False)
        if save:
            path = save_audit_report(project_path)
            return f"Code audit report saved to {path}, sir."
        return audit_report_text(project_path)

    return f"Unknown codebase_insight action: {action}"