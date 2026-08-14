"""
plugins/git_plugin.py — Git source control for JARVIS.

Actions:
  status          → git status --short
  diff            → git diff
  log             → git log --oneline -5
  branch          → list local branches
  create_branch   → git checkout -b <branch_name>
  switch_branch   → git checkout <branch_name>
  merge           → git merge <branch_name>
  commit          → git add -A && git commit -m <message>
  push            → git push
  pull            → git pull
  current_branch  → git branch --show-current
  remote_url      → git remote get-url origin
  create_pr       → create a pull request using GitHub CLI (gh)
"""

import subprocess
from pathlib import Path

PLUGIN_INFO = {
    "name": "git_plugin",
    "description": (
        "Manage Git source control for any repository. Use repo_path to target a specific project folder. "
        "If no repo_path is given, use the JARVIS project folder. "
        "Supports: status, diff, log, branches, commit, push, pull, merge, pull requests, and project discovery."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "status | diff | log | branch | create_branch | switch_branch | merge | commit | push | pull | current_branch | remote_url | create_pr | list_projects"
            },
            "branch_name": {
                "type": "STRING",
                "description": "Branch name for create_branch, switch_branch, merge"
            },
            "base_branch": {
                "type": "STRING",
                "description": "Base branch for create_pr (default: main)"
            },
            "head_branch": {
                "type": "STRING",
                "description": "Head branch for create_pr (default: current branch)"
            },
            "message": {
                "type": "STRING",
                "description": "Commit message for commit action"
            },
            "title": {
                "type": "STRING",
                "description": "Pull request title"
            },
            "body": {
                "type": "STRING",
                "description": "Pull request body description"
            },
            "repo_path": {
                "type": "STRING",
                "description": "Full path to the Git repository. Use this to operate on any project, e.g. C:\\Users\\abaze\\Desktop\\JarvisProjects\\flask_demo"
            }
        },
        "required": ["action"]
    }
}

BASE_DIR = Path(__file__).resolve().parent.parent

def _list_projects() -> str:
    """List common project directories Jarvis knows about."""
    projects = []
    known_dirs = [
        Path.home() / "Desktop" / "JarvisProjects",
        Path.home() / "Desktop",
        BASE_DIR,
    ]
    for d in known_dirs:
        if d.exists() and d.is_dir():
            projects.append(str(d))
    return "\n".join(projects)


def _run_git(args: list, cwd: str, timeout: int = 60) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            creationflags=subprocess.CREATE_NO_WINDOW if Path.cwd().exists() else 0,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "Git command timed out."
    except FileNotFoundError:
        return 127, "", "Git not found."
    except Exception as e:
        return 1, "", str(e)


def _check_gh_cli() -> bool:
    code, out, err = _run_git(["--version"], str(BASE_DIR), timeout=5)
    # Actually check gh CLI, not git
    import shutil
    return shutil.which("gh") is not None


def _git_status(repo_path: str) -> str:
    code, out, err = _run_git(["status", "--short"], repo_path)
    if code == 0:
        return out or "No changes."
    return err or "Git status failed."


def _git_diff(repo_path: str) -> str:
    code, out, err = _run_git(["diff"], repo_path)
    if code == 0:
        return out or "No diff."
    return err or "Git diff failed."


def _git_log(repo_path: str) -> str:
    code, out, err = _run_git(["log", "--oneline", "-5"], repo_path)
    if code == 0:
        return out or "No commits."
    return err or "Git log failed."


def _git_branch(repo_path: str) -> str:
    code, out, err = _run_git(["branch"], repo_path)
    if code == 0:
        return out or "No branches."
    return err or "Git branch failed."


def _git_create_branch(repo_path: str, branch_name: str) -> str:
    if not branch_name:
        return "Please provide a branch name."
    code, out, err = _run_git(["checkout", "-b", branch_name], repo_path)
    if code == 0:
        return f"Created and switched to branch '{branch_name}'."
    return err or out or "Failed to create branch."


def _git_switch_branch(repo_path: str, branch_name: str) -> str:
    if not branch_name:
        return "Please provide a branch name."
    code, out, err = _run_git(["checkout", branch_name], repo_path)
    if code == 0:
        return f"Switched to branch '{branch_name}'."
    return err or out or "Failed to switch branch."


def _git_merge(repo_path: str, branch_name: str) -> str:
    if not branch_name:
        return "Please provide a branch to merge."
    code, out, err = _run_git(["merge", branch_name], repo_path)
    if code == 0:
        return f"Merged '{branch_name}' into current branch."
    return err or out or "Merge failed."


def _git_commit(repo_path: str, message: str) -> str:
    if not message:
        return "Please provide a commit message."
    code, _, err = _run_git(["add", "-A"], repo_path)
    if code != 0:
        return f"git add failed: {err}"
    code, out, err = _run_git(["commit", "-m", message], repo_path)
    if code == 0:
        return f"Committed with message: {message}"
    return err or out or "Commit failed."


def _git_push(repo_path: str) -> str:
    code, out, err = _run_git(["push"], repo_path)
    if code == 0:
        return out or "Push successful."
    return err or out or "Push failed. Check remote and credentials."


def _git_pull(repo_path: str) -> str:
    code, out, err = _run_git(["pull"], repo_path)
    if code == 0:
        return out or "Pull successful."
    return err or out or "Pull failed."


def _git_current_branch(repo_path: str) -> str:
    code, out, err = _run_git(["branch", "--show-current"], repo_path)
    if code == 0:
        return out or "Unknown"
    return err or out or "Could not determine branch."


def _git_remote_url(repo_path: str) -> str:
    code, out, err = _run_git(["remote", "get-url", "origin"], repo_path)
    if code == 0:
        return out or "No remote origin."
    return err or out or "Remote origin not found."


def _create_pr(repo_path: str, base: str, head: str, title: str, body: str) -> str:
    if not _check_gh_cli():
        return (
            "GitHub CLI (gh) is not installed. Please install it from https://cli.github.com/ "
            "and authenticate with 'gh auth login' to create pull requests."
        )

    if not title:
        return "Please provide a PR title."
    if not base:
        base = "main"
    if not head:
        head = _git_current_branch(repo_path)

    cmd = ["gh", "pr", "create", "--base", base, "--head", head, "--title", title]
    if body:
        cmd += ["--body", body]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            cwd=repo_path,
            creationflags=subprocess.CREATE_NO_WINDOW if Path.cwd().exists() else 0,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or "Pull request created."
        return proc.stderr.strip() or "PR creation failed."
    except Exception as e:
        return f"PR creation error: {e}"


def execute(parameters: dict, player=None, speak=None) -> str:
    action = (parameters or {}).get("action", "").lower().strip()
    repo_path = parameters.get("repo_path") or str(BASE_DIR)
    branch_name = parameters.get("branch_name", "").strip()
    base_branch = parameters.get("base_branch", "").strip()
    head_branch = parameters.get("head_branch", "").strip()
    message = parameters.get("message", "").strip()
    title = parameters.get("title", "").strip()
    body = parameters.get("body", "").strip()

    if action == "list_projects":
        return _list_projects()
    if action == "status":
        return _git_status(repo_path)
    elif action == "diff":
        return _git_diff(repo_path)
    elif action == "log":
        return _git_log(repo_path)
    elif action == "branch":
        return _git_branch(repo_path)
    elif action == "create_branch":
        return _git_create_branch(repo_path, branch_name)
    elif action == "switch_branch":
        return _git_switch_branch(repo_path, branch_name)
    elif action == "merge":
        return _git_merge(repo_path, branch_name)
    elif action == "commit":
        return _git_commit(repo_path, message)
    elif action == "push":
        return _git_push(repo_path)
    elif action == "pull":
        return _git_pull(repo_path)
    elif action == "current_branch":
        return _git_current_branch(repo_path)
    elif action == "remote_url":
        return _git_remote_url(repo_path)
    elif action == "create_pr":
        return _create_pr(repo_path, base_branch, head_branch, title, body)
    else:
        return f"Unknown git_plugin action: {action}"