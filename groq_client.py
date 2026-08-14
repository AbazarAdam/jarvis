"""
groq_client.py — Groq LLM client for JARVIS (lazy initialisation).
"""

import json
import sys
from pathlib import Path
import requests


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR     = _get_base_dir()
API_KEY_PATH = BASE_DIR / "config" / "api_keys.json"

GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqClient:
    def __init__(self):
        self.api_key = None   # loaded lazily

    def _load_key(self):
        if self.api_key:
            return
        with open(API_KEY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        key = data.get("groq_api_key", "").strip()
        if not key:
            raise RuntimeError("groq_api_key is empty in api_keys.json")
        self.api_key = key

    def chat(self, messages, temperature=0.2, max_tokens=4096):
        self._load_key()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": GROQ_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        raise RuntimeError(f"Groq error {resp.status_code}: {resp.text[:200]}")


# Lazy proxy so importing this module never requires a key yet.
class _LazyGroq:
    def __init__(self):
        self._client = None

    def _ensure(self):
        if self._client is None:
            self._client = GroqClient()

    def __getattr__(self, name):
        self._ensure()
        return getattr(self._client, name)


groq_client = _LazyGroq()