"""
or_client.py — JARVIS model client compatibility layer.

All text/chat calls now delegate to the stable central ModelRouter.
Vision calls use a minimal OpenRouter request, not the old dead-model loop.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Optional

import requests


API_KEY_PATH = Path(__file__).resolve().parent / "config" / "api_keys.json"

DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.7

# Small, stable vision model list. We intentionally do not loop through
# dozens of models because that caused the old fallback storm.
VISION_MODELS = [
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "google/gemma-4-31b-it:free",
]

_rate_limited: dict[str, float] = {}
RATE_LIMIT_COOLDOWN = 60


def _load_api_key(name: str = "openrouter_api_key") -> str:
    try:
        with open(API_KEY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return str(data.get(name, "")).strip()
    except Exception as e:
        raise RuntimeError(f"Failed to load {name}: {e}")


def _is_rate_limited(model: str) -> bool:
    ts = _rate_limited.get(model)
    if ts is None:
        return False
    if time.time() - ts > RATE_LIMIT_COOLDOWN:
        del _rate_limited[model]
        return False
    return True


def _mark_rate_limited(model: str) -> None:
    _rate_limited[model] = time.time()


class OpenRouterClient:
    """Compatibility client preserving the old public API."""

    def chat(
        self,
        prompt: str,
        system: str = "You are JARVIS. Be concise, helpful, and precise.",
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        from core.model_router import ModelRouter

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

        response = ModelRouter().chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if not response.get("success"):
            raise RuntimeError(response.get("error") or "Cloud model failed.")

        return response["text"].strip()

    def multi_turn(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        from core.model_router import ModelRouter

        response = ModelRouter().chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if not response.get("success"):
            raise RuntimeError(response.get("error") or "Cloud model failed.")

        return response["text"].strip()

    def chat_json(
        self,
        prompt: str,
        system: str = "Return ONLY valid JSON. No markdown, no extra text.",
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> dict:
        raw = self.chat(
            prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=0.2,
        )

        clean = raw.strip()
        if clean.startswith("```"):
            parts = clean.split("```")
            clean = parts[1] if len(parts) > 1 else clean
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip().rstrip("`").strip()

        try:
            return json.loads(clean)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Model returned unparseable JSON: {e}\n"
                f"Raw output: {raw[:200]}"
            )

    def vision(
        self,
        prompt: str,
        image_b64: str,
        mime: str = "image/png",
        system: str = "Analyze the image and describe what you see clearly and concisely.",
        model: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> str:
        api_key = _load_api_key("openrouter_api_key")
        if not api_key:
            raise RuntimeError("OpenRouter API key not configured.")

        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/AbazarAdam",
            "X-Title": "J.A.R.V.I.S",
        }

        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            },
        ]

        candidates = [model] if model else VISION_MODELS
        candidates = [m for m in candidates if m]

        for candidate in candidates:
            if _is_rate_limited(candidate):
                continue

            payload = {
                "model": candidate,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.2,
            }

            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=60)
            except Exception:
                continue

            if resp.status_code == 429:
                _mark_rate_limited(candidate)
                continue

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    content = (
                        data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    )
                except Exception:
                    content = ""

                if content and content.strip():
                    return content.strip()

        raise RuntimeError("Vision request failed with all configured models.")

    def vision_from_file(
        self,
        prompt: str,
        image_path: str,
        system: str = "Analyze the image and describe what you see clearly and concisely.",
        model: Optional[str] = None,
        max_tokens: int = 1024,
    ) -> str:
        path = Path(image_path)
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        mime = mime_map.get(path.suffix.lower(), "image/png")

        with open(path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        return self.vision(prompt, image_b64, mime, system, model, max_tokens)

    def available_models(self) -> dict:
        return {
            "text_models": ["central_model_router"],
            "vision_models": VISION_MODELS,
            "rate_limited": list(_rate_limited.keys()),
            "total_text": 1,
            "total_vision": len(VISION_MODELS),
        }


class _LazyClient:
    def __init__(self):
        self._client = None

    def _ensure(self):
        if self._client is None:
            self._client = OpenRouterClient()

    def __getattr__(self, name):
        self._ensure()
        return getattr(self._client, name)

    def __bool__(self):
        try:
            self._ensure()
            return True
        except Exception:
            return False


client = _LazyClient()