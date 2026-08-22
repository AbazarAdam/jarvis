import asyncio
import threading
import json
import sys
import re
import traceback
import importlib.util
import uuid
import time
import psutil
import os
from pathlib import Path
from server import start_server, generate_qr
from actions.morning_brief import morning_brief
from actions.security_mode import security_mode
from actions.screen_processor import screen_process, camera_stream
from core.audit import log_action
from core.self_heal import self_heal

import requests
import sounddevice as sd
from google import genai
from google.genai import types
from google.genai.errors import APIError
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    should_extract_memory, extract_memory
)

from core.conversation_memory import WorkingMemory
from core.context_manager import build_context
from core.reflection import ReflectionMemory
from core.workflow_scheduler import WorkflowScheduler


from actions.file_processor import file_processor
from actions.open_app          import open_app
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control

def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent



BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

PLUGIN_DIR = BASE_DIR / "plugins"
PLUGIN_DECLARATIONS = []
PLUGIN_FUNCTIONS = {}


class HardResetException(Exception):
    """Raised inside the session loop to force a full restart."""
    pass



def load_plugins():
    """Scan the plugins/ folder and register any valid plugins."""
    global PLUGIN_DECLARATIONS, PLUGIN_FUNCTIONS

    if not PLUGIN_DIR.exists():
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        return

    for file in PLUGIN_DIR.glob("*.py"):
        if file.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"jarvis_plugin_{file.stem}", file
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            info = getattr(module, "PLUGIN_INFO", None)
            if not info:
                print(f"[Plugins] ⚠️ {file.name}: missing PLUGIN_INFO")
                continue

            name = info.get("name")
            description = info.get("description")
            parameters = info.get("parameters", {
                "type": "OBJECT",
                "properties": {},
                "required": []
            })
            execute_fn = getattr(module, "execute", None) or getattr(module, "run", None)

            if not name or not description or not callable(execute_fn):
                print(f"[Plugins] ⚠️ {file.name}: invalid plugin")
                continue

            PLUGIN_DECLARATIONS.append({
                "name": name,
                "description": description,
                "parameters": parameters
            })
            PLUGIN_FUNCTIONS[name] = execute_fn
            print(f"[Plugins] ✅ Loaded: {name}")
        except Exception as e:
            print(f"[Plugins] ❌ Failed to load {file.name}: {e}")

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )


def is_online(timeout: int = 3) -> bool:
    try:
        requests.get("https://www.google.com/generate_204", timeout=timeout)
        return True
    except Exception:
        return False
    
_last_memory_input = ""

def _update_memory_async(user_text: str, jarvis_text: str) -> None:
    global _last_memory_input

    user_text   = (user_text   or "").strip()
    jarvis_text = (jarvis_text or "").strip()

    if len(user_text) < 5 or user_text == _last_memory_input:
        return
    _last_memory_input = user_text

    try:
        api_key = _get_api_key()
        if not should_extract_memory(user_text, jarvis_text, api_key):
            return
        data = extract_memory(user_text, jarvis_text, api_key)
        if data:
            update_memory(data)
            print(f"[Memory] ✅ {list(data.keys())}")
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ {e}")

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the Windows computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "self_heal",
        "description": (
            "Runs the self‑healing system to fix recent errors automatically. "
            "Requires explicit confirmation before modifying any files. "
            "Use when the user asks to 'heal yourself', 'fix the last error', or 'repair the project'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "confirmed": {
                    "type": "STRING",
                    "description": "Set to 'yes' to confirm the self‑heal operation."
                }
            },
            "required": []
        }
    },
    {
        "name": "background_status",
        "description": "Checks the status of background tasks such as security scans or briefs.",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "camera_stream",
        "description": (
            "Streams camera frames continuously for a short duration and speaks real-time observations. "
            "Use for 'watch through the camera', 'observe the room', 'monitor what is happening'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "duration": {"type": "INTEGER", "description": "Total streaming time in seconds (default 20, max 60)"},
                "interval": {"type": "NUMBER",  "description": "Seconds between camera captures (default 3, min 1)"},
                "text":     {"type": "STRING",  "description": "Question or instruction for each frame (default: What do you see?)"}
            },
            "required": []
        }
    },
    {
        "name": "security_mode",
        "description": (
            "Runs a full security assessment: OSINT, subdomain enumeration, "
            "Nmap port scanning with CVE scripts, Nikto web scanning, "
            "Nuclei CVE scanning, SSL analysis, and generates a PDF report. "
            "Use for any security scan, penetration test, reconnaissance, or vulnerability assessment."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target": {"type": "STRING", "description": "Domain or IP address to scan"}
            },
            "required": ["target"]
        }
    },
    {
        "name": "morning_brief",
        "description": (
            "Generates a morning briefing with cybersecurity news, AI/software engineering news, "
            "and unread emails from Gmail. Saves the full report to the desktop and speaks a summary. "
            "Call this when the user says 'good morning', 'morning brief', 'brief me', etc."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "save": {"type": "BOOLEAN", "description": "Save report to desktop (default: true)"},
                "speak": {"type": "BOOLEAN", "description": "Speak the report (default: true)"}
            },
            "required": []
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets an alarm or timer. For a timer, pass seconds or minutes. For an alarm, pass date and time.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "message": {"type": "STRING", "description": "Reminder message text"},
                "seconds": {"type": "INTEGER", "description": "Countdown seconds for a timer"},
                "minutes": {"type": "INTEGER", "description": "Countdown minutes for a timer"},
                "date": {"type": "STRING", "description": "Date for an alarm, YYYY-MM-DD"},
                "time": {"type": "STRING", "description": "Time for an alarm, HH:MM 24h"}
            },
            "required": ["message"]
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
    "name": "computer_settings",
    "description": (
        "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
        "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
        "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
        "Use for ANY single computer control command. For volume, pass action=volume_set with a "
        "single integer percentage value from 0 to 100. For brightness, pass action=brightness_set "
        "with a single integer percentage value from 0 to 100; brightness only works on laptops or "
        "devices with a built-in controllable display. "
        "For minimize, maximize, close_window, close_app – also pass window_title with the window name "
        "to target a specific application window (e.g. 'Chrome', 'Notepad'). "
        "NEVER route these to agent_task."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "The action to perform, such as volume_set, brightness_set, type_text, press_key, reload_n, minimize, maximize, close_window, close_app, screenshot, etc."
            },
            "description": {
                "type": "STRING",
                "description": "Natural language description of what to do when action is not specified"
            },
            "value": {
                "type": "STRING",
                "description": "Single integer percentage for volume_set or brightness_set; otherwise text, key, or other action value"
            },
            "window_title": {
                "type": "STRING",
                "description": "Optional. The title/name of the window to minimize, maximize, or close (e.g. 'Chrome', 'Notepad'). Required when targeting a specific application window."
            },
            "confirmed": {
                "type": "STRING",
                "description": "Set to 'yes' to confirm dangerous actions like restart, shutdown. Required for those actions."
            }
        },
        "required": []
    }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls the web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, tab management, and any web-based task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | press | close | new_tab | switch_tab | close_tab | list_tabs | scroll_page | reload"},
                "url":         {"type": "STRING", "description": "URL for go_to or new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google, bing, duckduckgo (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type/scroll into view"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up or down for scroll and scroll_page actions"},
                "amount":      {"type": "INTEGER", "description": "Scroll pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, Tab)"},
                "fields":      {"type": "OBJECT", "description": "Dictionary of {selector: value} for fill_form"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear input before typing (default: true)"},
                "index":       {"type": "INTEGER", "description": "Tab index for switch_tab or close_tab (0-based)"},
                "title":       {"type": "STRING", "description": "Tab title fragment for switch_tab"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
                "confirmed":   {"type": "STRING", "description": "Set to 'yes' to confirm dangerous actions like delete. Required for those actions."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, builds, analyzes, tests, and fixes code. Supports Git status/diff/commit.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "write | read | edit | explain | run | build | optimize | analyze | generate_tests | test | git_status | git_diff | git_commit | self_fix | screen_debug | auto"
                },
                "description": {
                    "type": "STRING",
                    "description": "What the code should do, what change to make, or what problem to analyze"
                },
                "language": {
                    "type": "STRING",
                    "description": "Programming language (default: python)"
                },
                "output_path": {
                    "type": "STRING",
                    "description": "Where to save the generated file"
                },
                "file_path": {
                    "type": "STRING",
                    "description": "Path to existing file for edit/explain/run/build/analyze/test/self_fix"
                },
                "code": {
                    "type": "STRING",
                    "description": "Raw code string for explain/optimize/edit without a file"
                },
                "args": {
                    "type": "STRING",
                    "description": "CLI arguments for run/build"
                },
                "timeout": {
                    "type": "INTEGER",
                    "description": "Execution timeout in seconds (default: 30)"
                },
                "line_start": {
                    "type": "INTEGER",
                    "description": "Start line for line-aware edit"
                },
                "line_end": {
                    "type": "INTEGER",
                    "description": "End line for line-aware edit"
                },
                "new_content": {
                    "type": "STRING",
                    "description": "Replacement content for line-aware edit"
                },
                "attempts": {
                    "type": "INTEGER",
                    "description": "Number of self-fix attempts (default 3)"
                },
                "message": {
                    "type": "STRING",
                    "description": "Git commit message for git_commit"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, generates tests, CI/CD, Dockerfile, README, initialises Git, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate, to_pdf), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet | to_pdf\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png', 'jpg', 'jpeg', 'webp'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5 for 50%)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"}
        },
        "required": []
    }
    },
    {
    "name": "shutdown_jarvis",
    "description": (
        "Shuts down the assistant completely. "
        "Call this when the user expresses intent to end the conversation, "
        "close the assistant, say goodbye, or stop Jarvis. "
        "The user can say this in ANY language."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {},
    }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
]


DANGEROUS_TOOL_ACTIONS = {
    "computer_settings": {"restart", "shutdown"},
    "file_controller": {"delete"},
}


class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.interrupt_flag = ui.interrupt_flag
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self.background_tasks = {}
        self._loop          = None
        self._is_speaking   = False
        self._last_speech_time = 0.0
        self._turn_complete_sent = False
        self._speaking_lock = threading.Lock()
        # Counter for dropped audio frames from the input callback
        self._dropped_frames = 0
        self.ui.on_text_command = self._on_text_command
        # LLM client placeholder; will be assigned once local setup completes.
        self.llm = None


        self._last_response = ""
        self._response_lock = threading.Lock()
        self.background_tasks = {}

        # Cognitive memory
        self.working_memory = WorkingMemory()
        self.reflection = ReflectionMemory()

        # Hard reset support
        self._hard_reset_event = threading.Event()
        self._hard_reset_triggered = False
        self._stop_background_event = threading.Event()
        self._background_threads = {}
        self._ready_for_text = False



    def _clean_transcript(self, text: str) -> str:
        try:
            import re
            return re.sub(r"<[^>]*>", "", text).strip()
        except Exception:
            return text.strip()




    def get_last_response(self) -> str:
        with self._response_lock:
            return self._last_response

    def _note_proactive_activity(self, text: str):
        """Send user activity to the proactive engine if available."""
        try:
            if getattr(self, "proactive", None):
                self.proactive.note_user_activity(text)
        except Exception:
            pass

    def _on_text_command(self, text: str):
        if not self._loop or not self.session or not self._ready_for_text:
            self.ui.write_log("SYS: JARVIS is still restarting. Please wait.")
            return

        self.working_memory.add_user(text)
        self._note_proactive_activity(text)

        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _enqueue_out(self, item):
        """Synchronous helper to safely put an item into the out_queue from
        the audio callback thread via loop.call_soon_threadsafe(). If the
        queue is full, drop the frame silently to avoid raising in the
        audio callback (which causes many logged exceptions).
        """
        try:
            self.out_queue.put_nowait(item)
        except Exception:
            # QueueFull or other issues: drop the audio chunk and count it.
            try:
                self._dropped_frames += 1
                # Print a periodic summary to avoid log flooding.
                if self._dropped_frames % 50 == 0:
                    out_q = self.out_queue.qsize() if self.out_queue else 'N/A'
                    print(f"[JARVIS] ⚠️ Dropped audio frames: {self._dropped_frames} (out_q={out_q})")
            except Exception:
                pass
            return

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            prev = self._is_speaking
            if prev == value:
                return
            self._is_speaking = value

        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return

        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _announce_local(self, text: str = ""):
        """Play a short beep when a background task completes."""
        def _run():
            try:
                import winsound
                winsound.Beep(1000, 200)
                winsound.Beep(1200, 200)
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

    def _start_background_tool(self, tool_name: str, args: dict, func) -> str:
        task_id = uuid.uuid4().hex[:8]
        self.background_tasks[task_id] = {
            "tool": tool_name,
            "status": "running",
            "result": None,
        }
        def wrapper():
            try:
                result = func(parameters=args, player=self.ui)
                task = self.background_tasks.get(task_id)
                if task:
                    task["status"] = "completed"
                    task["result"] = result
                self._announce_local(f"{tool_name} completed, sir.")
                self.reflection.record(
                    goal=tool_name,
                    result=str(result)[:200],
                    outcome="success",
                    tool=tool_name,
                )
            except Exception as e:
                task = self.background_tasks.get(task_id)
                if task:
                    task["status"] = "failed"
                    task["result"] = str(e)
                self._announce_local(f"{tool_name} failed, sir.")
                self.reflection.record(
                    goal=tool_name,
                    result="",
                    outcome="failure",
                    error=str(e),
                    tool=tool_name,
                )
                from core.audit import log_action
                log_action(tool_name, args, result=str(e), status="failed")

        thread = threading.Thread(target=wrapper, daemon=True)
        self._background_threads[task_id] = thread
        thread.start()
        return task_id

    def cancel_all_background_tasks(self):
        """Signal all background tasks to stop without corrupting active threads."""
        self._stop_background_event.set()

        # Cancel all agent_task queue items
        try:
            from agent.task_queue import get_queue
            queue = get_queue()
            if hasattr(queue, "cancel_all"):
                queue.cancel_all()
        except Exception as e:
            print(f"[JARVIS] ⚠️ Could not cancel task queue: {e}")

        # Mark tasks as cancelled but keep entries so wrapper threads can
        # safely update their status later without raising KeyError.
        for task in self.background_tasks.values():
            task["status"] = "cancelled"

        # Threads are daemon; clear only our thread map.
        self._background_threads.clear()


    def _kill_child_processes(self):
        """
        Kill every child process spawned by JARVIS immediately.

        This stops security tools, browsers, sandbox scripts, and other
        subprocesses that a normal thread-cancellation cannot kill.
        """
        try:
            parent = psutil.Process(os.getpid())
            children = parent.children(recursive=True)

            for child in children:
                try:
                    child.terminate()
                except Exception:
                    pass

            # Give processes a short moment to die, then force kill survivors.
            time.sleep(0.5)

            for child in children:
                try:
                    if child.is_running():
                        child.kill()
                except Exception:
                    pass

            print(f"[JARVIS] ⏹ Killed {len(children)} child process(es).")
        except Exception as e:
            print(f"[JARVIS] ⚠️ Child process cleanup failed: {e}")

    def _reset_volatile_plugins(self):
        """Reset in-memory plugin state after a hard reset."""
        try:
            import plugins.stopwatch as sw
            with sw._lock:
                sw._start_time = None
                sw._elapsed = 0.0
        except Exception as e:
            print(f"[JARVIS] ⚠️ Stopwatch reset failed: {e}")

        try:
            import actions.browser_control as bc
            bc._bt._browser = None
            bc._bt._context = None
            bc._bt._page = None
        except Exception:
            pass

    async def _close_browser_safe(self):
        """Best-effort browser close after a hard reset.

        This version only closes the browser if its event loop is actually
        running. It prevents the unawaited-coroutine warning and does not
        block the hard-reset recovery."""
        try:
            import actions.browser_control as bc

            bt = bc._bt
            loop = getattr(bt, "_loop", None)

            # No browser thread / loop? Nothing to close safely.
            if loop is None or loop.is_closed():
                return

            # Schedule _close_browser on the browser thread loop, then wait.
            try:
                future = asyncio.run_coroutine_threadsafe(
                    bt._close_browser(),
                    loop,
                )
                await asyncio.wrap_future(future)
            except Exception:
                pass
        except Exception:
            pass


    def request_hard_reset(self):
        """Synchronous wrapper called from the UI thread to schedule hard_reset."""
        if self._loop and not self._loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self.hard_reset(), self._loop)
            except Exception as e:
                print(f"[JARVIS] Hard reset scheduling failed: {e}")
        else:
            print("[JARVIS] Hard reset requested but event loop not ready.")


    async def hard_reset(self):
        """Full hard reset: kill all tasks, clear all state, force session restart."""
        print("[JARVIS] ⏹ Hard reset initiated.")
        self.ui.write_log("SYS: Hard reset initiated.")

        self.working_memory.save_checkpoint(
            last_task=self.working_memory.get_last_user_text()[:120],
            summary=self.working_memory.get_last_jarvis_text()[:200],
        )

        # Mark that a hard reset is in progress
        self._hard_reset_triggered = True

        # Stop accepting new text input until JARVIS is back online
        self._ready_for_text = False

        # 1. Cancel background tasks and agent queue
        self.cancel_all_background_tasks()

        # 2. Kill every child process immediately
        self._kill_child_processes()

        # 3. Reset volatile plugin state
        self._reset_volatile_plugins()

        # 4. Close browser if possible
        await self._close_browser_safe()

        # 5. Clear audio queues
        if getattr(self, "audio_in_queue", None):
            while not self.audio_in_queue.empty():
                try:
                    self.audio_in_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        if getattr(self, "out_queue", None):
            while not self.out_queue.empty():
                try:
                    self.out_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

        # 6. Reset speaking state and update UI
        self.set_speaking(False)
        self.ui.set_state("RESTARTING")

        # 7. Force the session loop to terminate and reconnect
        self._hard_reset_event.set()

        # Safety fallback: if for any reason the run loop does not set
        # _ready_for_text=True within 15 seconds, force it back online.
        def _recovery_guard():
            time.sleep(15)
            try:
                if not self._ready_for_text:
                    self._ready_for_text = True
                    self.ui.reset_complete()
            except Exception:
                pass

        threading.Thread(target=_recovery_guard, daemon=True).start()


    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")


    def _build_config(self) -> types.LiveConnectConfig:
        sys_prompt = _load_system_prompt()

        context = build_context(
            working_memory=self.working_memory,
            reflection_memory=self.reflection,
        )
        combined = f"{context['combined']}\n\n{sys_prompt}"

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction=combined,
            tools=[{"function_declarations": TOOL_DECLARATIONS + PLUGIN_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

    async def _run_cancellable(self, fn, *args):
        """Run a blocking tool in an executor, but cancel waiting if STOP is pressed."""
        loop = asyncio.get_event_loop()
        future = loop.run_in_executor(None, fn, *args)

        while True:
            # STOP pressed → abandon the future, return immediately
            if self.interrupt_flag.is_set():
                self.interrupt_flag.clear()
                self.set_speaking(False)
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
                print("[JARVIS] ⏹ Tool execution interrupted by user")
                return None

            done, _ = await asyncio.wait([future], timeout=0.1)
            if done:
                try:
                    return future.result()
                except Exception as e:
                    raise e

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        log_action(name, args, status="started")

        # ------------------------------------------------------------------
        # CORTEX + SAFETY PREFLIGHT
        # ------------------------------------------------------------------
        try:
            from core.execution_guard import preflight_tool_call
            from core.skill_store import SkillStore

            try:
                learned_skills = SkillStore().list_skills(include_all=False)
            except Exception:
                learned_skills = []

            guard_decision = preflight_tool_call(
                name=name,
                parameters=args,
                tool_declarations=TOOL_DECLARATIONS,
                plugin_declarations=PLUGIN_DECLARATIONS,
                skills=learned_skills,
            )

            if not guard_decision.get("allowed"):
                reason = guard_decision.get("reason", "Blocked by execution guard.")
                log_action(name, args, result=f"Blocked: {reason}", status="blocked")
                self.speak_error(name, reason)
                return types.FunctionResponse(
                    id=fc.id,
                    name=name,
                    response={"result": f"Blocked: {reason}"},
                )
        except Exception as guard_error:
            # Guard failure should not crash the whole tool loop.
            print(f"[JARVIS] ⚠️ Execution guard error: {guard_error}")

        # Ensure result always exists, even for early returns
        result = "Done."

        self.ui.set_state("THINKING")
        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")

            log_action(name, args, result=str(result)[:200], status="completed")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()

        # Permission manager: require confirmation for dangerous actions
        dangerous_actions = DANGEROUS_TOOL_ACTIONS.get(name, set())
        action_value = args.get("action", "").lower()
        if action_value in dangerous_actions:
            confirmed = str(args.get("confirmed", "")).lower()
            if confirmed not in ("yes", "true", "1", "confirm"):
                log_action(name, args, result="Blocked: confirmation required", status="blocked")
                self.speak(
                    f"This action requires confirmation, sir. "
                    f"Please say 'yes' to confirm {action_value}."
                )
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": "Confirmation required."}
                )

        try:
            # Special handling for slow security_tool_manager actions
            if name == "security_tool_manager":
                action = args.get("action", "").lower()
                if action in ("install", "install_tools", "update", "update_tools"):
                    plugin_fn = PLUGIN_FUNCTIONS[name]

                    def _run_tool_manager(parameters, player=None):
                        return plugin_fn(parameters=parameters, player=player, speak=None)

                    self._start_background_tool("security_tool_manager", args, _run_tool_manager)
                    self._announce_local("Security tool operation started in background, sir.")
                    self.speak(f"Starting security tool {action}, sir.")
                    return types.FunctionResponse(
                        id=fc.id, name=name,
                        response={"result": "ok", "silent": True}
                    )
                # status/rollback are quick, run synchronously below

            # Plugin dispatch (all other plugins and quick security_tool_manager actions)
            if name in PLUGIN_FUNCTIONS:
                plugin_fn = PLUGIN_FUNCTIONS[name]
                r = await self._run_cancellable(
                    lambda: plugin_fn(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r if isinstance(r, str) else "Plugin executed."

            elif name == "open_app":
                r = await self._run_cancellable(lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."


            elif name == "browser_control":
                r = await self._run_cancellable(lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await self._run_cancellable(lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "reminder":
                r = await self._run_cancellable(lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."


            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "background_status":
                if not self.background_tasks:
                    result = "No background tasks running, sir."
                else:
                    lines = []
                    for task_id, info in self.background_tasks.items():
                        tool   = info.get("tool", "unknown")
                        status = info.get("status", "running")
                        detail = info.get("result") or ""

                        if status == "failed":
                            short = detail[:300] if detail else "Unknown error"
                            lines.append(f"{tool}: FAILED — {short}")
                        elif status == "completed":
                            short = detail[:300] if detail else "Done"
                            lines.append(f"{tool}: completed — {short}")
                        else:
                            lines.append(f"{tool}: running")
                    result = "Background tasks:\n" + "\n".join(lines)

            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "security_mode":
                from actions.security_mode import security_mode as sm

                # Quick actions run synchronously
                action = args.get("action", "full").lower()
                if action in ("list_tools", "update_tools"):
                    result = sm(parameters=args, player=self.ui, speak=self.speak)
                    return types.FunctionResponse(
                        id=fc.id, name=name,
                        response={"result": result}
                    )

                # Full scan requires confirmation before starting background
                confirmed = str(args.get("confirmed", "")).lower()
                if confirmed not in ("yes", "true", "1", "confirm"):
                    self.speak(
                        f"Please confirm you are authorised to test {args.get('target', 'unknown')}, sir."
                    )
                    return types.FunctionResponse(
                        id=fc.id, name=name,
                        response={"result": "Authorisation required."}
                    )

                self._start_background_tool("security_mode", args, sm)
                self._announce_local(
                    "Red team engagement started in background, sir. I will notify you when complete."
                )
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": "ok", "silent": True}
                )

            elif name == "camera_stream":
                threading.Thread(
                    target=camera_stream,
                    kwargs={"parameters": args, "player": self.ui},
                    daemon=True
                ).start()
                # Return silently so the main voice doesn't overlap the vision module
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": "ok", "silent": True}
                )

            elif name == "computer_settings":
                r = await self._run_cancellable(lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await self._run_cancellable(lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await self._run_cancellable(lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                from actions.dev_agent import dev_agent as da
                self._start_background_tool("dev_agent", args, da)
                self._announce_local(
                    "Development agent started in background, sir. I will notify you when complete."
                )
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": "ok", "silent": True}
                )


            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority

                def _run_agent_task(parameters, player=None, stop_event=None):
                    priority_map = {
                        "low": TaskPriority.LOW,
                        "normal": TaskPriority.NORMAL,
                        "high": TaskPriority.HIGH,
                    }
                    priority = priority_map.get(
                        parameters.get("priority", "normal").lower(),
                        TaskPriority.NORMAL,
                    )
                    task_id = get_queue().submit(
                        goal=parameters.get("goal", ""),
                        priority=priority,
                        speak=None,
                    )
                    import time as _time
                    while True:
                        # Check if hard reset is requested
                        if stop_event and stop_event.is_set():
                            # Try to cancel the queued task
                            try:
                                get_queue().cancel(task_id)
                            except Exception:
                                pass
                            return "Cancelled by user."
                        status = get_queue().get_status(task_id)
                        if status and status["status"] in ("completed", "failed", "cancelled"):
                            break
                        _time.sleep(1)
                    final_status = get_queue().get_status(task_id)
                    return (
                        final_status.get("result")
                        or final_status.get("error")
                        or "Done."
                    )

                self._start_background_tool(
                    "agent_task",
                    args,
                    lambda parameters, player=None: _run_agent_task(
                        parameters,
                        player,
                        stop_event=self._stop_background_event
                    )
                )

                self._announce_local(
                    "Agent task started in background, sir. I will notify you when complete."
                )
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": "ok", "silent": True}
                )

            elif name == "self_heal":
                self._start_background_tool("self_heal", args, self_heal)
                self._announce_local(
                    "Self‑healing started in background, sir. I will notify you when complete."
                )
                return types.FunctionResponse(
                    id=fc.id, name=name,
                    response={"result": "ok", "silent": True}
                )

            elif name == "web_search":
                try:
                    func = globals().get('web_search_action')
                    if getattr(func, 'requires_internet', False) and self.llm and not self.llm.is_online():
                        self.speak("I need an internet connection to search the web.")
                        result = "Requires internet"
                        raise RuntimeError("offline")
                except RuntimeError:
                    pass

                r = await self._run_cancellable(lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "computer_control":
                r = await self._run_cancellable(lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."


            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye, sir.")

                def _shutdown():
                    import time, sys, os
                    time.sleep(1)
                    os._exit(0)

                threading.Thread(target=_shutdown, daemon=True).start()

            elif name == "morning_brief":
                # Quick spoken confirmation
                if self.session:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": "Preparing your morning brief, sir."}]},
                        turn_complete=True
                    )
                from actions.morning_brief import morning_brief as mb
                r = await loop.run_in_executor(
                    None,
                    lambda: mb(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Morning brief delivered, sir."
                # Store a snippet for the remote dashboard
                with self._response_lock:
                    self._last_response = result.split('\n')[0]  # first line of report



            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")

        # Log tool output as SYS, not Jarvis speech
        if isinstance(result, str) and result.strip():
            try:
                self.ui.write_log(f"SYS: {name} — {result[:200]}")
            except Exception:
                pass

        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            # Remove any VAD-specific keys before sending
            if isinstance(msg, dict):
                msg = {
                    "data": msg.get("data", b""),
                    "mime_type": msg.get("mime_type", "audio/pcm;rate=16000"),
                }
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if not jarvis_speaking and not self.ui.muted:
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self._enqueue_out,
                    {"data": data, "mime_type": "audio/pcm;rate=16000"}
                )

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[JARVIS] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:


                async for response in self.session.receive():
                    # Hard reset requested → exit the session loop immediately
                    if self._hard_reset_event.is_set():
                        raise HardResetException()

                    # If interrupted, skip processing but keep the connection alive
                    if self.interrupt_flag.is_set():
                        continue

                    if response.data:
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            self.set_speaking(True)
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                in_buf.append(txt)

                        if sc.turn_complete:
                            print(f"[JARVIS] turn_complete: in_buf_len={len(in_buf)} out_buf_len={len(out_buf)}")
                            self.set_speaking(False)

                            full_in = self._clean_transcript(" ".join(in_buf))
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                self.working_memory.add_user(full_in)
                                self._note_proactive_activity(full_in)
                            in_buf = []

                            full_out = self._clean_transcript(" ".join(out_buf))
                            with self._response_lock:
                                self._last_response = full_out

                            if full_out:
                                self.ui.write_log(f"Jarvis: {full_out}")
                                self.working_memory.add_jarvis(full_out)
                            else:
                                self.ui.write_log("Jarvis: (voice response)")

                            out_buf = []

                            if full_in or full_out:
                                self.working_memory.save_checkpoint(
                                    last_task=full_in[:120] if full_in else "",
                                    summary=full_out[:200] if full_out else "",
                                )

                            if full_in and len(full_in) > 5:
                                threading.Thread(
                                    target=_update_memory_async,
                                    args=(full_in, full_out),
                                    daemon=True
                                ).start()

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )

        except Exception as e:
            if isinstance(e, APIError) and getattr(e, "code", None) == 1011:
                print("[JARVIS] ⚠️ Live service unavailable. Reconnecting...")
            else:
                print(f"[JARVIS] ❌ Recv: {e}")
                traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")
        loop = asyncio.get_event_loop()

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()
        try:
            while True:

                if self.interrupt_flag.is_set():
                    # Instantly stop speaking
                    while not self.audio_in_queue.empty():
                        try:
                            self.audio_in_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    self.set_speaking(False)
                    # Wait until the user clicks RESUME
                    while self.interrupt_flag.is_set():
                        await asyncio.sleep(0.1)
                    continue

                chunk = await self.audio_in_queue.get()
                self.set_speaking(True)
                await asyncio.to_thread(stream.write, chunk)
                # If we've finished playing all queued audio, return to listening.
                try:
                    if self.audio_in_queue.empty():
                        self.set_speaking(False)
                except Exception:
                    pass
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    async def run(self):
        if not is_online():
            print("[JARVIS] 📴 Offline mode detected.")
            try:
                self.ui.write_log("SYS: Offline mode detected. Starting local speech and Ollama.")
            except Exception:
                pass

            try:
                from offline_voice import OfflineVoiceAssistant

                assistant = OfflineVoiceAssistant(self.ui)
                await asyncio.to_thread(assistant.run)
            except Exception as exc:
                print(f"[JARVIS] ❌ Offline mode failed: {exc}")
                traceback.print_exc()
            return

        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        while True:
            try:
                print("[JARVIS] 🔌 Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    # Increase out_queue size to buffer more microphone frames and
                    # avoid backpressure causing the InputStream callback to hit
                    # QueueFull frequently. 50 is a reasonable middle ground.
                    self.out_queue = asyncio.Queue()

                    print("[JARVIS] ✅ Connected.")
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: JARVIS online.")

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    # Heartbeat: silent ping every 45s to prevent server-side timeout
                    async def _heartbeat():
                        while True:
                            await asyncio.sleep(45)
                            try:
                                await session.send_client_content(
                                    turns={"parts": [{"text": "."}], "turn_complete": True}
                                )
                            except Exception:
                                break
                    tg.create_task(_heartbeat())

                    self.ui.reset_complete()
                    self._ready_for_text = True
                    self._stop_background_event.clear()
                    self._hard_reset_event.clear()

                    # Force JARVIS back to live microphone listening.
                    # If the UI was muted before STOP, this ensures it is
                    # unmuted for the new session.
                    self.ui.muted = False
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: Microphone active.")
                    self._stop_background_event.clear()

            except HardResetException:
                print("[JARVIS] Hard reset requested. Reconnecting...")
                self._hard_reset_event.clear()   # allow next session to start clean
                # Continue to the common cleanup below

            except HardResetException:
                print("[JARVIS] Hard reset requested. Reconnecting...")
                self._hard_reset_event.clear()
                self._hard_reset_triggered = False
                # Continue to the common cleanup below

            except BaseExceptionGroup as eg:
                # TaskGroup can wrap the HardResetException into a BaseExceptionGroup
                if self._hard_reset_triggered:
                    print("[JARVIS] Hard reset completed.")
                    self._hard_reset_event.clear()
                    self._hard_reset_triggered = False
                else:
                    print(f"[JARVIS] ⚠️ Unhandled BaseExceptionGroup: {eg}")
                    traceback.print_exception(eg)
                    from datetime import datetime
                    from pathlib import Path
                    log_dir = Path(__file__).resolve().parent / "logs"
                    log_dir.mkdir(exist_ok=True)
                    with open(log_dir / "crash.log", "a", encoding="utf-8") as f:
                        f.write(f"[{datetime.now()}] TASKGROUP ERROR\n{traceback.format_exc()}\n")

            except Exception as e:
                print(f"[JARVIS] ⚠️ {e}")
                traceback.print_exc()
                # If the exception happened during a hard reset, don't log it as crash
                if self._hard_reset_triggered:
                    self._hard_reset_event.clear()
                    self._hard_reset_triggered = False
                else:
                    # Log to crash file
                    from datetime import datetime
                    from pathlib import Path
                    log_dir = Path(__file__).resolve().parent / "logs"
                    log_dir.mkdir(exist_ok=True)
                    with open(log_dir / "crash.log", "a", encoding="utf-8") as f:
                        f.write(f"[{datetime.now()}] ASYNCIO ERROR\n{traceback.format_exc()}\n")

            self.set_speaking(False)
            self.ui.set_state("THINKING")
            print("[JARVIS] 🔄 Reconnecting in 3s...")
            await asyncio.sleep(3)

def main():
    ui = JarvisUI("face.png")

    from core.error_handler import setup

    # We don't have the jarvis.speak callback yet, so we'll update it later.
    setup(speak=None, write_log=ui.write_log)

    def runner():
        online = is_online()
        if not online:
            try:
                ui.set_state("Offline mode – local only")
                ui.write_log("SYS: Offline mode – local only")
            except Exception:
                pass

            try:
                from offline_voice import run_offline_mode
                run_offline_mode(ui)
            except Exception as exc:
                print(f"[JARVIS] ❌ Offline mode failed: {exc}")
                traceback.print_exc()
            return

        if online:
            ui.wait_for_api_key()
        load_plugins()
        jarvis = JarvisLive(ui)

        # Connect the UI STOP button to hard reset
        jarvis.ui.on_hard_reset = jarvis.request_hard_reset

        # Now the global error handler can speak through Jarvis
        import core.error_handler as eh
        eh._speak_callback = jarvis.speak

        from server import start_server
        start_server(jarvis)



        # Start automatic local LLM setup in background; assign jarvis.llm when ready.
        def _setup_local():
            try:
                from local_llm_manager import ensure_local_llm_ready
                ready = ensure_local_llm_ready(ui=ui)
            except Exception as e:
                ready = False
                try:
                    ui.write_log(f"Local LLM setup error: {e}")
                except Exception:
                    pass

            try:
                from llm_client import llm as _llm
                # Assign regardless; LLMClient will check availability when used.
                jarvis.llm = _llm
            except Exception:
                jarvis.llm = None

            try:
                if ready:
                    if online:
                        ui.write_log("Local LLM ready (cloud mode with local fallback).")
                    else:
                        ui.write_log("Local LLM ready (offline mode enabled).")
                else:
                    if online:
                        ui.write_log("Local LLM unavailable; using cloud fallback.")
                    else:
                        ui.write_log("Local LLM unavailable; offline mode will keep trying local Ollama.")
            except Exception:
                pass

        from core.proactive import ProactiveAssistant

        # Start proactive assistance after Jarvis begins running
        proactive = ProactiveAssistant()
        proactive.speak_callback = jarvis.speak
        jarvis.proactive = proactive

        # Autonomous workflow scheduler
        from core.workflow_scheduler import WorkflowScheduler
        from plugins.workflow_scheduler import run_tool_by_name

        scheduler = WorkflowScheduler()
        scheduler.set_executor(lambda tool_name, params: run_tool_by_name(tool_name, params, player=jarvis.ui, speak=jarvis.speak))
        scheduler.start()
        jarvis.scheduler = scheduler

        # Connect the UI toggle button to the proactive engine
        ui._win._proactive_toggle_signal.connect(proactive.set_enabled)

        #threading.Thread(target=_setup_local, daemon=True).start()
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()