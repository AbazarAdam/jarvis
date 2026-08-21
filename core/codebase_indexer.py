"""
core/codebase_indexer.py — Project codebase understanding for JARVIS.

This module gives JARVIS the ability to understand a project at a high level.

It scans a directory, parses supported Python and web files, and builds a
structured index of:

- files
- Python functions and classes
- imports
- likely API routes
- authentication-related code locations
- TODO/FIXME notes

All operations are read-only. No files are modified.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


BASE_DIR = Path(__file__).resolve().parent.parent
MEMORY_DIR = BASE_DIR / "memory"
INDEX_FILE = MEMORY_DIR / "codebase_index.json"


# ---------------------------------------------------------------------------
# Path filtering
# ---------------------------------------------------------------------------
DEFAULT_IGNORE_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "logs",
    "tools",
    "chrome_profile",
    "vosk_model",
    "ollama_local",
    ".idea",
    ".vscode",
}

DEFAULT_IGNORE_FILES = {
    "api_keys.json",
    "long_term.json",
    "shortcuts.json",
    "gmail_token.pickle",
    "gmail_credentials.json",
}

SUPPORTED_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class CodeSymbol:
    name: str
    kind: str                    # function | class | method | route | import
    location: str                # relative_path:line
    signature: Optional[str] = None
    doc: Optional[str] = None


@dataclass
class CodeFile:
    path: str
    language: str
    symbols: list[CodeSymbol] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    auth_hints: list[str] = field(default_factory=list)
    todos: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Python AST analysis
# ---------------------------------------------------------------------------
class _PythonVisitor(ast.NodeVisitor):
    """Extract functions, classes, routes, and auth hints from Python code."""

    def __init__(self):
        self.functions: list[CodeSymbol] = []
        self.classes: list[CodeSymbol] = []
        self.imports: list[CodeSymbol] = []
        self.routes: list[str] = []
        self.auth_hints: list[str] = []

    def visit_FunctionDef(self, node):
        symbol = CodeSymbol(
            name=node.name,
            kind="function",
            location=f"{node.lineno}",
            signature=self._format_args(node.args),
            doc=ast.get_docstring(node),
        )
        self.functions.append(symbol)
        self._check_route_and_auth(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node):
        symbol = CodeSymbol(
            name=node.name,
            kind="class",
            location=f"{node.lineno}",
            doc=ast.get_docstring(node),
        )
        self.classes.append(symbol)
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.append(
                CodeSymbol(
                    name=alias.name,
                    kind="import",
                    location=f"{node.lineno}",
                )
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        module = node.module or ""
        for alias in node.names:
            full = f"{module}.{alias.name}" if module else alias.name
            self.imports.append(
                CodeSymbol(
                    name=full,
                    kind="import",
                    location=f"{node.lineno}",
                )
            )
        self.generic_visit(node)

    def _format_args(self, args) -> str:
        arg_names = [a.arg for a in args.args]
        if args.vararg:
            arg_names.append(f"*{args.vararg.arg}")
        if args.kwarg:
            arg_names.append(f"**{args.kwarg.arg}")
        return f"({', '.join(arg_names)})"

    def _check_route_and_auth(self, node):
        """Detect Flask/FastAPI/Django route decorators and auth hints."""
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call):
                # Flask/FastAPI route
                if isinstance(decorator.func, ast.Attribute):
                    attr_name = decorator.func.attr
                    if attr_name in ("route", "get", "post", "put", "delete", "patch"):
                        route_path = ""
                        if decorator.args:
                            first = decorator.args[0]
                            if isinstance(first, ast.Constant):
                                route_path = str(first.value)
                        self.routes.append(f"{attr_name.upper()} {route_path} → {node.name}")

                # Auth hints
                if isinstance(decorator.func, ast.Name):
                    if decorator.func.id in ("login_required", "jwt_required", "require_auth"):
                        self.auth_hints.append(f"{decorator.func.id} on {node.name}")

        # Function name / docstring auth hints
        name_lower = node.name.lower()
        if any(word in name_lower for word in ("login", "auth", "password", "token", "session")):
            self.auth_hints.append(f"auth-related function: {node.name}")


# ---------------------------------------------------------------------------
# Text file analysis
# ---------------------------------------------------------------------------
ROUTE_PATTERNS = [
    re.compile(r"@(?:app|router|bp)\.(?:route|get|post|put|delete|patch)\(['\"]([^'\"]+)['\"]"),
    re.compile(r"@(?:app|router)\.(?:add_url_rule)\(['\"]([^'\"]+)['\"]"),
    re.compile(r"fetch\(['\"]([^'\"]+)['\"]"),
    re.compile(r"axios\.(?:get|post|put|delete|patch)\(['\"]([^'\"]+)['\"]"),
]

AUTH_PATTERNS = [
    re.compile(r"(?:login|auth|token|password|session|jwt)", re.IGNORECASE),
]


def _parse_text_file(path: Path) -> tuple[list[str], list[str], list[str]]:
    routes: list[str] = []
    auth_hints: list[str] = []
    todos: list[str] = []

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return routes, auth_hints, todos

    for pattern in ROUTE_PATTERNS:
        for m in pattern.finditer(text):
            routes.append(m.group(1))

    for pattern in AUTH_PATTERNS:
        for m in pattern.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            auth_hints.append(f"auth pattern at line {line_no}")

    for pattern in [re.compile(r"TODO:?(.*)"), re.compile(r"FIXME:?(.*)")]:
        for m in pattern.finditer(text):
            todos.append(m.group(0).strip())

    return routes, auth_hints, todos


# ---------------------------------------------------------------------------
# Main indexing API
# ---------------------------------------------------------------------------
def _is_ignored(path: Path, project_root: Path) -> bool:
    parts = {p.name for p in path.parents if p != project_root.parent}
    return bool(parts.intersection(DEFAULT_IGNORE_DIRS))


def index_project(project_path: str | Path) -> dict:
    """
    Scan a project directory and return a structured codebase index.

    Returns dict:
        {
            "project_path": str,
            "indexed_at": str,
            "file_count": int,
            "symbol_count": int,
            "files": [CodeFile as dict ...]
        }
    """
    root = Path(project_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return {
            "project_path": str(root),
            "error": "Project path does not exist or is not a directory.",
        }

    files: list[dict] = []
    symbol_count = 0
    file_count = 0

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if _is_ignored(path, root):
            continue
        if path.name in DEFAULT_IGNORE_FILES:
            continue
        ext = path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue

        file_count += 1
        rel = str(path.relative_to(root))
        language = ext.lstrip(".")

        symbols: list[CodeSymbol] = []
        routes: list[str] = []
        auth_hints: list[str] = []
        todos: list[str] = []

        if ext == ".py":
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source)
                visitor = _PythonVisitor()
                visitor.visit(tree)

                symbols.extend(visitor.functions)
                symbols.extend(visitor.classes)
                symbols.extend(visitor.imports)
                routes.extend(visitor.routes)
                auth_hints.extend(visitor.auth_hints)

                # TODO/FIXME from comments or string literal remains in source
                for m in re.finditer(r"(?:TODO|FIXME):?[^\n]*", source):
                    todos.append(m.group(0).strip())

            except SyntaxError:
                pass
            except Exception:
                pass

        else:
            txt_routes, txt_auth, txt_todos = _parse_text_file(path)
            routes.extend(txt_routes)
            auth_hints.extend(txt_auth)
            todos.extend(txt_todos)

        files.append({
            "path": rel,
            "language": language,
            "symbols": [s.__dict__ for s in symbols],
            "routes": routes,
            "auth_hints": auth_hints,
            "todos": todos,
        })
        symbol_count += len(symbols)

    index = {
        "project_path": str(root),
        "indexed_at": datetime.now().isoformat(timespec="seconds"),
        "file_count": file_count,
        "symbol_count": symbol_count,
        "files": files,
    }

    _save_index(index)
    return index


def _save_index(index: dict) -> None:
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        INDEX_FILE.write_text(json.dumps(index, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_index() -> dict | None:
    if not INDEX_FILE.exists():
        return None
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def search_symbol(query: str, index: dict | None = None) -> list[dict]:
    """
    Search for functions, classes, imports, or routes matching a query.

    Returns a list of matching symbols with their file paths.
    """
    data = index or load_index()
    if not data:
        return []

    query_lower = query.lower()
    matches = []

    for file_entry in data.get("files", []):
        file_path = file_entry.get("path", "")
        for sym in file_entry.get("symbols", []):
            if query_lower in sym.get("name", "").lower():
                matches.append({
                    "file": file_path,
                    "name": sym.get("name"),
                    "kind": sym.get("kind"),
                    "line": sym.get("location"),
                })
        for route in file_entry.get("routes", []):
            if query_lower in route.lower():
                matches.append({
                    "file": file_path,
                    "name": route,
                    "kind": "route",
                })
    return matches[:30]


def project_summary(project_path: str | Path) -> str:
    """Return a short spoken summary of a project."""
    index = index_project(project_path)

    if index.get("error"):
        return index["error"]

    file_count = index.get("file_count", 0)
    symbol_count = index.get("symbol_count", 0)

    route_count = 0
    auth_count = 0
    todo_count = 0

    for f in index.get("files", []):
        route_count += len(f.get("routes", []))
        auth_count += len(f.get("auth_hints", []))
        todo_count += len(f.get("todos", []))

    return (
        f"Project contains {file_count} files, "
        f"{symbol_count} code symbols, "
        f"{route_count} possible routes, "
        f"{auth_count} authentication-related items, and "
        f"{todo_count} TODO/FIXME notes."
    )


def find_auth_logic(project_path: str | Path) -> list[str]:
    """Return files/lines likely related to authentication."""
    index = index_project(project_path)
    if index.get("error"):
        return [index["error"]]

    results = []
    for f in index.get("files", []):
        for hint in f.get("auth_hints", []):
            results.append(f"{f['path']}: {hint}")
    return results[:20]


def find_routes(project_path: str | Path) -> list[str]:
    """Return likely API/UI routes discovered in the project."""
    index = index_project(project_path)
    if index.get("error"):
        return [index["error"]]

    results = []
    for f in index.get("files", []):
        for route in f.get("routes", []):
            results.append(f"{f['path']}: {route}")
    return results[:30]