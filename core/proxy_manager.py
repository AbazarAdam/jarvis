"""
core/proxy_manager.py — Central proxy configuration for JARVIS.

Loads an optional proxy from config/api_keys.json:
  - "proxy": "socks5://127.0.0.1:9050"   (Tor)
  - "proxy": "http://127.0.0.1:8080"     (Burp/ZAP/VPN)

Provides:
  - get_proxy()         → dict or None
  - get_requests_proxies() → dict for requests
  - get_tool_proxy_arg() → list for tool subprocess
  - is_enabled()        → bool
"""

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _load_proxy_string() -> str | None:
    """Return proxy string from config or None."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("proxy", "").strip() or None
    except Exception:
        return None


def is_enabled() -> bool:
    return _load_proxy_string() is not None


def get_proxy() -> dict | None:
    """Return requests-compatible proxies dict."""
    proxy_str = _load_proxy_string()
    if not proxy_str:
        return None
    return {"http": proxy_str, "https": proxy_str}


def get_requests_proxies() -> dict | None:
    """Alias for get_proxy."""
    return get_proxy()


def get_tool_proxy_arg() -> list[str] | None:
    """Return proxy argument for command-line tools, e.g. ['--proxy', 'socks5://127.0.0.1:9050']."""
    proxy_str = _load_proxy_string()
    if not proxy_str:
        return None
    return ["--proxy", proxy_str]


def log_proxy_status() -> str:
    proxy_str = _load_proxy_string()
    if proxy_str:
        return f"Proxy enabled: {proxy_str}"
    return "No proxy configured. Running with direct connection."