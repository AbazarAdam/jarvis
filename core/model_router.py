"""
core/model_router.py — Central cloud-model orchestration for JARVIS.

This module gives JARVIS one place to call:
    - Google Gemini
    - OpenRouter models
    - Groq models

It selects the best available provider for a given task, handles fallbacks,
cools down after rate limits, and returns clear structured results.

No local Ollama is used. Local models are intentionally disabled for now.
"""

from __future__ import annotations

import json
import time
import threading
from pathlib import Path
from typing import Any, Optional

# No core.error_handler import needed here.


BASE_DIR = Path(__file__).resolve().parent.parent
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

LOG_DIR = BASE_DIR / "logs"
ROUTER_LOG = LOG_DIR / "model_router.log"


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------
def _log(msg: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(ROUTER_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------
def _load_api_keys() -> dict:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_key(name: str) -> str | None:
    keys = _load_api_keys()
    val = keys.get(name)
    return str(val).strip() if val else None


# ---------------------------------------------------------------------------
# Model Router
# ---------------------------------------------------------------------------
class ModelRouter:
    """Thread-safe cloud model router with provider fallback."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialised", False):
            return
        self._initialised = True

        self._cooldowns: dict[str, float] = {}
        self._cooldown_lock = threading.Lock()

        self._gemini_model = "gemini-2.5-flash"
        self._openrouter_model = "nvidia/nemotron-3-super-120b-a12b:free"
        self._groq_model = "llama-3.1-8b-instant"

    # ------------------------------------------------------------------
    # Cooldown helpers
    # ------------------------------------------------------------------
    def _in_cooldown(self, provider: str) -> bool:
        with self._cooldown_lock:
            until = self._cooldowns.get(provider, 0)
            return time.time() < until

    def _set_cooldown(self, provider: str, seconds: int) -> None:
        with self._cooldown_lock:
            self._cooldowns[provider] = time.time() + seconds

    # ------------------------------------------------------------------
    # Provider calls
    # ------------------------------------------------------------------
    def _try_gemini(self, messages: list[dict], temperature: float, max_tokens: int) -> str:
        """Try Gemini Flash. Raises on failure."""
        api_key = _get_key("gemini_api_key")
        if not api_key:
            raise RuntimeError("Gemini API key not configured.")

        from google import genai

        client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "v1beta"},
        )

        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                prompt_parts.insert(0, f"[SYSTEM]\n{content}")
            else:
                prompt_parts.append(f"[{role.upper()}]\n{content}")

        prompt = "\n\n".join(prompt_parts)

        response = client.models.generate_content(
            model=self._gemini_model,
            contents=prompt,
            config={
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        )
        text = getattr(response, "text", "").strip()
        if not text:
            raise RuntimeError("Gemini returned empty response.")
        return text

    def _try_openrouter(self, messages: list[dict], temperature: float, max_tokens: int) -> str:
        """Try OpenRouter. Raises on failure."""
        from or_client import client

        # Convert old-style system string if needed
        system = None
        clean_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system = msg.get("content", "")
            else:
                clean_messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            # or_client has `multi_turn(messages, ...)` in some paths.
            result = client.multi_turn(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            if result:
                return result
        except Exception:
            # Some or_client versions may only support chat(query, system=...)
            user_text = "\n".join(
                msg.get("content", "") for msg in messages if msg.get("role") == "user"
            )
            if not user_text:
                raise
            result = client.chat(
                user_text,
                system=system or "You are JARVIS, a highly capable AI assistant.",
            )
            if result:
                return result

        raise RuntimeError("OpenRouter returned empty response.")

    def _try_groq(self, messages: list[dict], temperature: float, max_tokens: int) -> str:
        """Try Groq. Raises on failure."""
        from groq_client import groq_client

        result = groq_client.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if result:
            return result
        raise RuntimeError("Groq returned empty response.")

    # ------------------------------------------------------------------
    # Main API
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 1500,
        providers: Optional[list[str]] = None,
    ) -> dict:
        """
        Route a chat completion request across configured cloud providers.

        Returns:
            {
                "success": bool,
                "text": str,
                "provider": str,
                "error": str | None,
            }
        """
        if not messages:
            return {"success": False, "text": "", "provider": "", "error": "No messages."}

        provider_order = providers or ["openrouter", "groq", "gemini"]

        for provider in provider_order:
            if self._in_cooldown(provider):
                continue

            try:
                if provider == "gemini":
                    text = self._try_gemini(messages, temperature, max_tokens)
                elif provider == "openrouter":
                    text = self._try_openrouter(messages, temperature, max_tokens)
                elif provider == "groq":
                    text = self._try_groq(messages, temperature, max_tokens)
                else:
                    continue

                if text.strip():
                    _log(f"SUCCESS provider={provider} len={len(text)}")
                    return {
                        "success": True,
                        "text": text,
                        "provider": provider,
                        "error": None,
                    }

            except Exception as e:
                err_text = str(e)
                _log(f"FAIL provider={provider} error={err_text[:200]}")

                # Apply cooldown on obvious rate-limit / auth errors
                low = err_text.lower()
                if any(k in low for k in ("rate limit", "429", "403", "access denied")):
                    self._set_cooldown(provider, 60)

        return {
            "success": False,
            "text": "",
            "provider": "",
            "error": "All configured model providers failed.",
        }

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1500,
    ) -> dict:
        """Convenience wrapper for a single user prompt."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, temperature=temperature, max_tokens=max_tokens)