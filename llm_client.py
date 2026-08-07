import requests
import json
import time
import os
from typing import Dict, Any, List, Optional

LOG_PATH = os.path.join("logs", "llm_errors.log")


class LLMClient:
    def __init__(self, config_path: str = "config/llm_config.json") -> None:
        self.config = {
            "local_llm": {"enabled": False, "endpoint": "http://localhost:11434", "model": "llama3.2:3b", "timeout": 45},
            "fallback_to_cloud": True,
        }
        try:
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.config.update(data)
        except Exception as e:
            self._log(f"Failed to load LLM config: {e}")

        ll = self.config.get("local_llm", {})
        self.ollama_endpoint = ll.get("endpoint", "http://localhost:11434")
        self.ollama_model = ll.get("model", "llama3.2:3b")
        self.ollama_timeout = ll.get("timeout", 45)

    def _log(self, message: str) -> None:
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
        except Exception:
            pass

    def is_online(self, timeout: int = 3) -> bool:
        try:
            requests.get("https://www.google.com", timeout=timeout)
            return True
        except Exception:
            return False

    def is_ollama_available(self) -> bool:
        try:
            r = requests.get(f"{self.ollama_endpoint}/api/tags", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def generate(self, prompt: str, tools: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """
        Unified generation interface.
        Returns a dict with either:
          {"type":"text","content":"..."}
        or {"type":"tool_call","name":"...","arguments":{...}}
        """
        try:
            local_cfg = self.config.get("local_llm", {})
            if local_cfg.get("enabled") and self.is_ollama_available():
                return self._call_ollama(prompt, tools)
        except Exception as e:
            self._log(f"Local LLM check failed: {e}")

        # Fallback to cloud if available
        if self.is_online() and self.config.get("fallback_to_cloud", True):
            try:
                return self._call_cloud(prompt, tools)
            except Exception as e:
                self._log(f"Cloud LLM failed: {e}")

        return {"type": "text", "content": "I'm offline and no local LLM is available. Please check your internet connection or start Ollama."}

    def _call_ollama(self, prompt: str, tools: Optional[List[Dict]] = None) -> Dict[str, Any]:
        payload = {
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": 512, "temperature": 0.7},
        }
        if tools:
            payload["tools"] = tools

        try:
            resp = requests.post(f"{self.ollama_endpoint}/api/generate", json=payload, timeout=self.ollama_timeout)
            resp.raise_for_status()
            data = resp.json()
            return self._parse_ollama_response(data)
        except Exception as e:
            self._log(f"Ollama call failed: {e}")
            # If cloud available, let _call_cloud handle fallback
            if self.is_online() and self.config.get("fallback_to_cloud", True):
                return self._call_cloud(prompt, tools)
            return {"type": "text", "content": f"Local LLM error: {e}"}

    def _parse_ollama_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # Ollama's response shape may vary; attempt to extract a textual response
        raw = ""
        if isinstance(data, dict):
            raw = data.get("response") or data.get("text") or ""
        raw = (raw or "").strip()
        if raw.startswith("{") and '"name"' in raw:
            try:
                tool_call = json.loads(raw)
                return {"type": "tool_call", "name": tool_call.get("name"), "arguments": tool_call.get("arguments", {})}
            except Exception:
                pass
        return {"type": "text", "content": raw}

    def _call_cloud(self, prompt: str, tools: Optional[List[Dict]] = None) -> Dict[str, Any]:
        # Prefer OpenRouter client if available
        try:
            from or_client import client as or_client
            reply = or_client.chat(prompt, system=("You are JARVIS, a helpful assistant."))
            return {"type": "text", "content": reply}
        except Exception as e:
            self._log(f"OpenRouter call failed: {e}")

        # Last resort: try to use google.genai if present
        try:
            from google import genai
            c = genai.Client(api_key=self._get_api_key())
            # simple single-turn generate
            resp = c.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            text = ""
            for part in resp.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text
            return {"type": "text", "content": text.strip()}
        except Exception as e:
            self._log(f"Google genai call failed: {e}")
            raise

    def _get_api_key(self) -> str:
        try:
            with open("config/api_keys.json", "r", encoding="utf-8") as f:
                return json.load(f).get("gemini_api_key", "")
        except Exception:
            return ""


# Module-level client for convenience
llm = LLMClient()
