import os
import sys
import shutil
import subprocess
import tempfile
import time
import json
import platform
import traceback
import zipfile
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Optional

SETUP_FLAG = ".ollama_setup_done"
DEFAULT_OLLAMA_WIN_URL = "https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip"
DEFAULT_OLLAMA_MAC_URL = "https://github.com/ollama/ollama/releases/latest/download/ollama-macos-universal.pkg"
DEFAULT_OLLAMA_LIN_URL = "https://github.com/ollama/ollama/releases/latest/download/ollama-linux-amd64.tar.gz"
DEFAULT_OLLAMA_LOCAL_DIR_NAME = "ollama_local"
DOWNLOAD_CHUNK_SIZE = 65536
DOWNLOAD_RETRY_COUNT = 3


def _project_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _flag_path() -> str:
    return os.path.join(_project_root(), SETUP_FLAG)


def _local_ollama_dir() -> str:
    return os.path.join(_project_root(), DEFAULT_OLLAMA_LOCAL_DIR_NAME)


def _local_ollama_executable() -> str:
    local_dir = _local_ollama_dir()
    if not os.path.isdir(local_dir):
        return os.path.join(local_dir, "ollama.exe" if sys.platform.startswith("win") else "ollama")
    for root, _, files in os.walk(local_dir):
        for name in files:
            if sys.platform.startswith("win") and name.lower() == "ollama.exe":
                return os.path.join(root, name)
            if not sys.platform.startswith("win") and name == "ollama":
                return os.path.join(root, name)
    return os.path.join(local_dir, "ollama.exe" if sys.platform.startswith("win") else "ollama")


def _ollama_command() -> list[str]:
    local_bin = _local_ollama_executable()
    if os.path.exists(local_bin):
        return [local_bin]
    return ["ollama"]


def is_ollama_installed() -> bool:
    # Prefer shutil.which
    exe = shutil.which("ollama")
    if exe:
        return True
    local_bin = _local_ollama_executable()
    if os.path.exists(local_bin):
        return True
    # Windows common location
    if sys.platform.startswith("win"):
        possible = [
            os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "Ollama", "ollama.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "Ollama", "ollama.exe"),
        ]
        for p in possible:
            if os.path.exists(p):
                return True
    return False


def _requests_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=DOWNLOAD_RETRY_COUNT,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["HEAD", "GET", "OPTIONS"]),
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _download(url: str, dest: str) -> None:
    print(f"Downloading Ollama from {url} to {dest}")
    if os.path.exists(dest):
        os.remove(dest)
    session = _requests_session()
    attempt = 0
    while True:
        attempt += 1
        try:
            with session.get(url, stream=True, timeout=(5, 60)) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                written = 0
                last_report = 0
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                        if chunk:
                            fh.write(chunk)
                            written += len(chunk)
                            if total and written - last_report >= total * 0.05:
                                last_report = written
                                pct = int((written / total) * 100)
                                print(f"Download progress: {pct}% ({written}/{total} bytes)")
                print(f"Downloaded {dest} ({written} bytes)")
                return
        except Exception as exc:
            print(f"Download attempt {attempt} failed: {exc}")
            if os.path.exists(dest):
                os.remove(dest)
            if attempt >= DOWNLOAD_RETRY_COUNT:
                raise
            wait = attempt * 2
            print(f"Retrying in {wait} seconds...")
            time.sleep(wait)


def _extract_zip(path: str) -> str:
    dest_dir = _local_ollama_dir()
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(path, "r") as zf:
        zf.extractall(dest_dir)
        for name in zf.namelist():
            if name.lower().endswith("ollama.exe") or name.lower().endswith("ollama"):
                return os.path.join(dest_dir, name)
    return _local_ollama_executable()


def _run_installer(path: str) -> None:
    # On Windows, prefer ZIP extraction if the installer is a ZIP package
    if path.lower().endswith(".zip") and sys.platform.startswith("win"):
        _extract_zip(path)
        return
    if path.lower().endswith(".msi") and sys.platform.startswith("win"):
        subprocess.run(["msiexec", "/i", path, "/quiet", "/norestart"], check=True)
    else:
        # Try executing; may need elevation
        subprocess.run([path], check=True)


def ensure_ollama_installed(ui=None) -> bool:
    try:
        if is_ollama_installed():
            return True

        # Download and run installer
        system = platform.system().lower()
        tmpdir = tempfile.gettempdir()
        if system == "windows":
            url = DEFAULT_OLLAMA_WIN_URL
            dest = os.path.join(tmpdir, "ollama_installer.zip")
        elif system == "darwin":
            url = DEFAULT_OLLAMA_MAC_URL
            dest = os.path.join(tmpdir, "ollama_installer.pkg")
        else:
            url = DEFAULT_OLLAMA_LIN_URL
            dest = os.path.join(tmpdir, "ollama_installer.tar.gz")

        if ui:
            try:
                ui.write_log("Installing Ollama for local LLM (one-time, may require elevation)...")
                # emit small progress if UI supports it
                if hasattr(ui, "_win") and hasattr(ui._win, "_setup_sig"):
                    try: ui._win._setup_sig.emit("Installing Ollama...", 10)
                    except Exception: pass
            except Exception:
                pass

        _download(url, dest)
        _run_installer(dest)

        # Short wait for PATH to update
        time.sleep(3)
        return is_ollama_installed()
    except Exception as e:
        try:
            if ui:
                ui.write_log(f"Ollama install failed: {e}")
        except Exception:
            pass
        print(f"Ollama install failed: {e}")
        traceback.print_exc()
        return False


def ensure_model_pulled(model_name: str, ui=None) -> bool:
    try:
        # Check if model exists via ollama list
        local_bin = _local_ollama_executable()
        work_dir = os.path.dirname(local_bin) if local_bin else None
        
        proc = subprocess.run(_ollama_command() + ["list"], capture_output=True, text=True, cwd=work_dir)
        if model_name in proc.stdout:
            return True

        if ui:
            try:
                ui.write_log(f"Pulling local model: {model_name} (one-time)")
                if hasattr(ui, "_win") and hasattr(ui._win, "_setup_sig"):
                    try: ui._win._setup_sig.emit("Pulling model...", 50)
                    except Exception: pass
            except Exception:
                pass

        subprocess.run(_ollama_command() + ["pull", model_name], check=True, cwd=work_dir)
        return True
    except Exception as e:
        try:
            if ui:
                ui.write_log(f"Model pull failed: {e}")
        except Exception:
            pass
        print(f"Model pull failed: {e}")
        traceback.print_exc()
        return False


def start_ollama_service(ui=None) -> Optional[subprocess.Popen]:
    try:
        # Check if service is already answering
        if is_ollama_ready():
            return None

        system = platform.system().lower()
        local_bin = _local_ollama_executable()
        work_dir = os.path.dirname(local_bin) if local_bin else None
        
        if system.startswith("win"):
            # Start as subprocess, hidden window, from the ollama directory
            CREATE_NO_WINDOW = 0x08000000
            proc = subprocess.Popen(_ollama_command() + ["serve"], creationflags=CREATE_NO_WINDOW, cwd=work_dir)
            time.sleep(2)
            return proc
        else:
            # On macOS/Linux, try to start in background
            proc = subprocess.Popen(_ollama_command() + ["serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=work_dir)
            time.sleep(2)
            return proc
    except Exception as e:
        try:
            if ui:
                ui.write_log(f"Failed to start Ollama service: {e}")
                if hasattr(ui, "_win") and hasattr(ui._win, "_setup_sig"):
                    try: ui._win._setup_sig.emit(f"Failed to start service", 0)
                    except Exception: pass
        except Exception:
            pass
        print(f"Failed to start Ollama service: {e}")
        traceback.print_exc()
        return None


def is_ollama_ready(timeout: int = 2) -> bool:
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def ensure_local_llm_ready(ui=None, config_path: str = "config/llm_config.json") -> bool:
    """High level orchestration: install, pull model, start service, verify.
    This is safe to run repeatedly; it uses a local flag file to avoid redoing work.
    """
    root = _project_root()
    flag = _flag_path()
    try:
        # Load config to get model choice
        model = "llama3.2:3b"
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    model = cfg.get("local_llm", {}).get("model", model)
            except Exception:
                pass

        # Skip if flag present and service responding
        if os.path.exists(flag) and is_ollama_ready():
            return True

        # Check disk space
        usage = shutil.disk_usage(root)
        free_gb = usage.free // (1024 * 1024 * 1024)
        if free_gb < 2:
            if ui:
                try:
                    ui.write_log("Insufficient disk space for local LLM; using cloud fallback.")
                except Exception:
                    pass
            return False

        # Install if missing
        if not is_ollama_installed():
            ok = ensure_ollama_installed(ui=ui)
            if not ok:
                return False
        else:
            if ui and hasattr(ui, "_win") and hasattr(ui._win, "_setup_sig"):
                try: ui._win._setup_sig.emit("Ollama present", 20)
                except Exception: pass

        # Pull model
        ok = ensure_model_pulled(model, ui=ui)
        if not ok:
            return False

        # Start service
        proc = start_ollama_service(ui=ui)

        # Wait for readiness
        for i in range(30):
            if is_ollama_ready():
                # Create flag
                try:
                    with open(flag, "w", encoding="utf-8") as fh:
                        fh.write(json.dumps({"model": model, "ts": time.time()}))
                except Exception:
                    pass
                if ui and hasattr(ui, "_win") and hasattr(ui._win, "_setup_sig"):
                    try: ui._win._setup_sig.emit("Local LLM ready", 100)
                    except Exception: pass
                return True
            # emit progress
            if ui and hasattr(ui, "_win") and hasattr(ui._win, "_setup_sig"):
                try:
                    pct = 60 + int((i / 30) * 40)
                    ui._win._setup_sig.emit("Starting Ollama service...", pct)
                except Exception:
                    pass
            time.sleep(2)

        return False
    except Exception as e:
        print(f"ensure_local_llm_ready unexpected error: {e}")
        traceback.print_exc()
        return False
