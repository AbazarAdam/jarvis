from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

import requests
import shutil
import sounddevice as sd

from actions.browser_control import browser_control
from actions.code_helper import code_helper
from actions.computer_control import computer_control
from actions.computer_settings import computer_settings
from actions.desktop import desktop_control
from actions.file_controller import file_controller
from actions.file_processor import file_processor
from actions.flight_finder import flight_finder
from actions.game_updater import game_updater
from actions.open_app import open_app
from actions.reminder import reminder
from actions.send_message import send_message
from actions.web_search import web_search as web_search_action
from actions.weather_report import weather_action
from actions.youtube_video import youtube_video

BASE_DIR = Path(__file__).resolve().parent
LOG_PATH = BASE_DIR / "logs" / "offline_errors.log"
LLM_CONFIG_PATH = BASE_DIR / "config" / "llm_config.json"
OFFLINE_CONFIG_PATH = BASE_DIR / "config" / "offline_voice.json"
VOSK_MODEL_DIR = BASE_DIR / "vosk_model"
VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"
VOSK_MODEL_URL = f"https://alphacephei.com/vosk/models/{VOSK_MODEL_NAME}.zip"
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_OLLAMA_ENDPOINT = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
DEFAULT_VOSK_MODEL_CANDIDATES = [
    VOSK_MODEL_DIR / VOSK_MODEL_NAME,
    BASE_DIR / "models" / "vosk-model-small-en-us-0.15",
    BASE_DIR / "models" / "vosk-model-small-en-us",
    BASE_DIR / "vosk-model-small-en-us",
]
BLOCKED_TOOLS = {
    "weather_report",
    "web_search",
    "youtube_video",
    "flight_finder",
    "send_message",
    "dev_agent",
    "code_helper",
    "file_processor",
    "game_updater",
}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _ensure_log_dir() -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


class OfflineVoiceAssistant:
    def __init__(self, ui):
        self.ui = ui
        self.audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=64)
        self._speaking_lock = threading.Lock()
        self._speaking = False
        self._tts_engine = None
        self.sample_rate = DEFAULT_SAMPLE_RATE
        self.ollama_endpoint = DEFAULT_OLLAMA_ENDPOINT
        self.ollama_model = DEFAULT_OLLAMA_MODEL
        self.vosk_model_path = self._resolve_vosk_model_path()
        self._load_config()

    def _log_error(self, scope: str, error: Exception | str) -> None:
        _ensure_log_dir()
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {scope}: {error}\n")
        except Exception:
            pass

    def _load_config(self) -> None:
        cfg = _load_json(LLM_CONFIG_PATH)
        offline_cfg = _load_json(OFFLINE_CONFIG_PATH)

        local_llm = cfg.get("local_llm", {}) if isinstance(cfg, dict) else {}
        self.ollama_endpoint = offline_cfg.get("ollama_endpoint", local_llm.get("endpoint", self.ollama_endpoint))
        self.ollama_model = offline_cfg.get("ollama_model", local_llm.get("model", self.ollama_model))
        self.sample_rate = int(offline_cfg.get("sample_rate", self.sample_rate))

        custom_model_path = offline_cfg.get("vosk_model_path")
        if custom_model_path:
            custom_path = Path(custom_model_path)
            if not custom_path.is_absolute():
                custom_path = BASE_DIR / custom_path
            self.vosk_model_path = custom_path

    def _resolve_vosk_model_path(self) -> Optional[Path]:
        env_path = os.environ.get("JARVIS_VOSK_MODEL_PATH")
        if env_path:
            candidate = Path(env_path)
            if candidate.exists():
                return candidate

        for candidate in DEFAULT_VOSK_MODEL_CANDIDATES:
            if candidate.exists():
                return candidate
        return None

    def _manual_model_command(self) -> str:
        return (
            "powershell -Command \"New-Item -ItemType Directory -Force vosk_model | Out-Null; "
            f"Invoke-WebRequest -Uri '{VOSK_MODEL_URL}' -OutFile 'vosk_model\\{VOSK_MODEL_NAME}.zip'; "
            f"Expand-Archive -Force 'vosk_model\\{VOSK_MODEL_NAME}.zip' 'vosk_model'; "
            f"Remove-Item 'vosk_model\\{VOSK_MODEL_NAME}.zip'\""
        )

    def _is_online(self, timeout: int = 3) -> bool:
        try:
            requests.get("https://www.google.com/generate_204", timeout=timeout)
            return True
        except Exception:
            return False

    def _download_vosk_model(self) -> Path:
        VOSK_MODEL_DIR.mkdir(parents=True, exist_ok=True)
        target_dir = VOSK_MODEL_DIR / VOSK_MODEL_NAME
        if target_dir.exists():
            return target_dir

        zip_path = VOSK_MODEL_DIR / f"{VOSK_MODEL_NAME}.zip"
        response = requests.get(VOSK_MODEL_URL, stream=True, timeout=30)
        response.raise_for_status()

        with open(zip_path, "wb") as fh:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    fh.write(chunk)

        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(VOSK_MODEL_DIR)

        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            pass

        if target_dir.exists():
            return target_dir

        for candidate in VOSK_MODEL_DIR.iterdir():
            if candidate.is_dir() and candidate.name.startswith("vosk-model-small-en-us"):
                return candidate

        return target_dir

    def _ensure_vosk_model_ready(self) -> Path:
        model_path = self._resolve_vosk_model_path()
        if model_path and model_path.exists():
            return model_path

        if not self._is_online():
            raise RuntimeError(
                "No Vosk model is installed yet. Run once with internet or use: "
                f"{self._manual_model_command()}"
            )

        try:
            model_path = self._download_vosk_model()
            if model_path.exists():
                return model_path
        except Exception as exc:
            self._log_error("vosk_download", exc)
            raise RuntimeError(
                "Failed to download the Vosk model automatically. "
                f"Run once with internet or use: {self._manual_model_command()}"
            ) from exc

        raise RuntimeError(
            "Failed to prepare the Vosk model. "
            f"Run once with internet or use: {self._manual_model_command()}"
        )

    def _set_state(self, state: str) -> None:
        try:
            self.ui.set_state(state)
        except Exception:
            pass

    def _set_speaking(self, value: bool) -> None:
        with self._speaking_lock:
            self._speaking = value
        self._set_state("SPEAKING" if value else "LISTENING")

    def _is_speaking(self) -> bool:
        with self._speaking_lock:
            return self._speaking

    def _speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return

        self._set_speaking(True)
        try:
            self._speak_with_tts(text)
        except Exception as exc:
            self._log_error("tts", exc)
            try:
                self.ui.write_log(f"Jarvis: {text}")
            except Exception:
                pass
        finally:
            self._set_speaking(False)

    def _speak_with_tts(self, text: str) -> None:
        try:
            import pyttsx3
        except Exception:
            pyttsx3 = None

        if pyttsx3 is not None:
            if self._tts_engine is None:
                self._tts_engine = pyttsx3.init()
            self._tts_engine.say(text)
            self._tts_engine.runAndWait()
            return

        if sys.platform.startswith("win"):
            subprocess.run(
                [
                    "powershell",
                    "-Command",
                    f"Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Speak('{text.replace("'", "''")}')",
                ],
                check=False,
            )
            return

        if shutil.which("say"):
            subprocess.run(["say", text], check=False)
            return

        if shutil.which("espeak"):
            subprocess.run(["espeak", text], check=False)

    def _ensure_local_llm(self) -> bool:
        try:
            from local_llm_manager import ensure_local_llm_ready

            return ensure_local_llm_ready(ui=self.ui)
        except Exception as exc:
            self._log_error("local_llm_setup", exc)
            return False

    def _build_prompt(self, user_text: str) -> str:
        return (
            "You are JARVIS running offline. Answer concisely and directly. "
            "Never invent web data. If the user asks for weather, search, YouTube, flights, "
            "news, or any information that requires the internet, respond with the exact JSON object "
            '{"response":"I need an internet connection to get that."}. '
            "If a local tool is needed, respond with a JSON object in one of these forms: "
            '{"response":"..."} or {"tool":"tool_name","arguments":{...},"response":"..."}. '
            "Allowed local tools: open_app, browser_control, computer_control, computer_settings, "
            "desktop_control, file_controller, reminder, game_updater. "
            f"User: {user_text}"
        )

    def _needs_internet(self, user_text: str) -> Optional[str]:
        text = user_text.lower()
        if any(keyword in text for keyword in ["weather", "forecast"]):
            return "I need an internet connection to get the weather."
        if any(keyword in text for keyword in ["search the web", "web search", "google", "search online", "search the internet"]):
            return "I need an internet connection to search the web."
        if "youtube" in text or "video" in text and "play" in text:
            return "I need an internet connection to access YouTube."
        if any(keyword in text for keyword in ["flight", "flights", "airfare", "plane ticket"]):
            return "I need an internet connection to search for flights."
        if any(keyword in text for keyword in ["news", "headline", "headlines"]):
            return "I need an internet connection to check the news."
        return None

    def _call_local_llm(self, user_text: str) -> dict[str, Any]:
        payload = {
            "model": self.ollama_model,
            "prompt": self._build_prompt(user_text),
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.4,
                "num_predict": 256,
            },
        }
        response = requests.post(
            f"{self.ollama_endpoint}/api/generate",
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        raw = str(data.get("response", "") or "").strip()
        if not raw:
            return {"response": "I did not get a response from the local model."}

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        return {"response": raw}

    def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name in BLOCKED_TOOLS:
            return self._internet_refusal(tool_name)

        try:
            if tool_name == "open_app":
                return open_app(parameters=arguments, response=None, player=self.ui) or "Done."
            if tool_name == "browser_control":
                return browser_control(parameters=arguments, player=self.ui) or "Done."
            if tool_name == "computer_control":
                return computer_control(parameters=arguments, player=self.ui) or "Done."
            if tool_name == "computer_settings":
                return computer_settings(parameters=arguments, response=None, player=self.ui) or "Done."
            if tool_name == "desktop_control":
                return desktop_control(parameters=arguments, player=self.ui) or "Done."
            if tool_name == "file_controller":
                return file_controller(parameters=arguments, player=self.ui) or "Done."
            if tool_name == "reminder":
                return reminder(parameters=arguments, response=None, player=self.ui) or "Reminder set."
        except Exception as exc:
            self._log_error(tool_name, exc)
            return f"Tool '{tool_name}' failed: {exc}"

        return f"Unknown local tool: {tool_name}"

    def _internet_refusal(self, tool_or_query: str) -> str:
        text = tool_or_query.lower()
        if "weather" in text:
            return "I need an internet connection to get the weather."
        if "search" in text or "web" in text:
            return "I need an internet connection to search the web."
        if "youtube" in text:
            return "I need an internet connection to access YouTube."
        if "flight" in text:
            return "I need an internet connection to search for flights."
        return "I need an internet connection for that."

    def _handle_transcript(self, user_text: str) -> None:
        user_text = (user_text or "").strip()
        if not user_text:
            return

        try:
            self.ui.write_log(f"You: {user_text}")
        except Exception:
            pass

        refusal = self._needs_internet(user_text)
        if refusal:
            try:
                self.ui.write_log(f"Jarvis: {refusal}")
            except Exception:
                pass
            self._speak(refusal)
            return

        try:
            result = self._call_local_llm(user_text)
        except Exception as exc:
            self._log_error("local_llm", exc)
            message = "I could not reach the local model."
            try:
                self.ui.write_log(f"Jarvis: {message}")
            except Exception:
                pass
            self._speak(message)
            return

        tool_name = result.get("tool") if isinstance(result, dict) else None
        response_text = result.get("response") if isinstance(result, dict) else None

        if tool_name:
            tool_result = self._execute_tool(str(tool_name), result.get("arguments", {}) or {})
            response_text = response_text or tool_result

        if not response_text:
            response_text = str(result.get("content") or "") if isinstance(result, dict) else str(result)

        response_text = response_text.strip()
        if not response_text:
            response_text = "Done."

        try:
            self.ui.write_log(f"Jarvis: {response_text}")
        except Exception:
            pass
        self._speak(response_text)

    def _load_vosk_model(self):
        try:
            import vosk
        except Exception as exc:
            raise RuntimeError("vosk is not installed") from exc

        self.vosk_model_path = self._ensure_vosk_model_ready()

        try:
            vosk.SetLogLevel(-1)
        except Exception:
            pass

        return vosk.Model(str(self.vosk_model_path))

    def _audio_loop(self, recognizer) -> None:
        def callback(indata, frames, time_info, status):
            if status:
                self._log_error("audio_status", status)
            if self._is_speaking():
                return
            try:
                self.audio_queue.put_nowait(bytes(indata))
            except queue.Full:
                pass

        with sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=8000,
            dtype="int16",
            channels=1,
            callback=callback,
        ):
            self._set_state("LISTENING")
            while True:
                try:
                    data = self.audio_queue.get(timeout=0.25)
                except queue.Empty:
                    continue

                if recognizer.AcceptWaveform(data):
                    try:
                        text = json.loads(recognizer.Result()).get("text", "").strip()
                    except Exception:
                        text = ""
                    if text:
                        self._set_state("THINKING")
                        self._handle_transcript(text)
                else:
                    try:
                        partial = json.loads(recognizer.PartialResult()).get("partial", "").strip()
                        if partial and not self._is_speaking():
                            self.ui.write_log(f"[Offline partial] {partial}")
                    except Exception:
                        pass

    def run(self) -> None:
        try:
            self._ensure_local_llm()
        except Exception as exc:
            self._log_error("startup", exc)
            try:
                self.ui.write_log(f"Offline mode could not start local LLM: {exc}")
            except Exception:
                pass
            return

        try:
            model = self._load_vosk_model()
        except Exception as exc:
            self._log_error("vosk", exc)
            try:
                self.ui.write_log(f"Offline speech model missing: {exc}")
            except Exception:
                pass
            return

        try:
            self.ui.write_log("SYS: Offline mode ready. Using local speech recognition and local Ollama.")
        except Exception:
            pass

        from vosk import KaldiRecognizer

        recognizer = KaldiRecognizer(model, self.sample_rate)
        self._audio_loop(recognizer)


def run_offline_mode(jarvis_or_ui) -> None:
    ui = getattr(jarvis_or_ui, "ui", jarvis_or_ui)
    assistant = OfflineVoiceAssistant(ui)
    assistant.run()