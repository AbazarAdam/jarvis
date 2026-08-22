import asyncio
import os
import threading
import concurrent.futures
import platform
import shutil
import subprocess

from core.reset_controller import reset_controller
import time
import random
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout


BASE_DIR = Path(__file__).resolve().parent.parent


def _get_default_browser_id() -> str:
    """Returns raw default browser identifier string for current OS."""
    system = platform.system()
    try:
        if system == "Windows":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice"
            )
            prog_id = winreg.QueryValueEx(key, "ProgId")[0].lower()
            winreg.CloseKey(key)
            return prog_id

        elif system == "Darwin":
            result = subprocess.run(
                ["defaults", "read",
                 "com.apple.LaunchServices/com.apple.launchservices.secure",
                 "LSHandlers"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.lower()

        elif system == "Linux":
            result = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.lower()

    except Exception:
        pass

    return ""


_BROWSER_BINARIES = {
    "Windows": {
        "opera":   ["opera.exe"],
        "brave":   ["brave.exe"],
        "vivaldi": ["vivaldi.exe"],
        "chrome":  ["chrome.exe"],
        "firefox": ["firefox.exe"],
    },
    "Darwin": {
        "opera":   ["opera"],
        "brave":   ["brave browser", "brave"],
        "vivaldi": ["vivaldi"],
        "chrome":  ["google chrome", "google-chrome"],
        "firefox": ["firefox"],
    },
    "Linux": {
        "opera":   ["opera", "opera-stable"],
        "brave":   ["brave-browser", "brave"],
        "vivaldi": ["vivaldi-stable", "vivaldi"],
        "chrome":  ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"],
        "firefox": ["firefox"],
    },
}


def _get_opera_executable() -> str | None:
    if platform.system() != "Windows":
        return None
    try:
        import winreg
        candidate_keys = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\opera.exe",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\launcher.exe",
            r"SOFTWARE\Clients\StartMenuInternet\OperaStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\OperaGXStable\shell\open\command",
        ]
        for key_path in candidate_keys:
            for hive in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
                try:
                    key = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(key, None)
                    winreg.CloseKey(key)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        print(f"[Browser] 🔍 Opera found via registry: {exe}")
                        return exe
                except Exception:
                    continue
    except Exception:
        pass
    return None


def _find_browser_executable(prog_id: str) -> tuple:
    """
    Returns (engine_name, exe_path, channel, is_opera).
    is_opera=True → extra args needed to prevent private-mode launch.
    """
    system  = platform.system()
    os_bins = _BROWSER_BINARIES.get(system, {})

    if any(x in prog_id for x in ["firefox", "mozilla"]):
        return "firefox", None, None, False

    if "safari" in prog_id:
        return "webkit", None, None, False

    if "edge" in prog_id:
        return "chromium", None, "msedge", False

    if "opera" in prog_id:
        exe = _get_opera_executable()
        if exe:
            return "chromium", exe, None, True
        for binary in os_bins.get("opera", []):
            path = shutil.which(binary)
            if path:
                return "chromium", path, None, True

    browser_patterns = {
        "brave":   ["brave"],
        "vivaldi": ["vivaldi"],
        "chrome":  ["chrome"],
    }
    for browser_name, patterns in browser_patterns.items():
        if not any(p in prog_id for p in patterns):
            continue
        binaries = os_bins.get(browser_name, [])
        for binary in binaries:
            path = shutil.which(binary)
            if path:
                print(f"[Browser] 🔍 Found {browser_name} at: {path}")
                return "chromium", path, None, False

    if "chrome" in prog_id or not prog_id:
        return "chromium", None, "chrome", False

    return "chromium", None, None, False


class _BrowserThread:

    def __init__(self):
        self._loop       = None
        self._thread     = None
        self._ready      = threading.Event()
        self._playwright = None
        self._browser    = None
        self._context    = None
        self._page       = None
        self._engine_name = "chromium"
        self._exe_path   = None
        self._channel    = None
        self._is_opera   = False
        self._user_data_dir = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="BrowserThread"
        )
        self._thread.start()
        self._ready.wait(timeout=15)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._init())
        self._ready.set()
        self._loop.run_forever()

    async def _init(self):
        self._playwright = await async_playwright().start()

    def run(self, coro, timeout: int = 30):
        if not self._loop:
            raise RuntimeError("BrowserThread not started.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ── Browser and page management ─────────────────────────────────────────

    async def _launch_browser_if_needed(self):
        if self._browser and self._browser.is_connected():
            return

        prog_id = _get_default_browser_id()
        self._engine_name, self._exe_path, self._channel, self._is_opera = _find_browser_executable(prog_id)
        engine = getattr(self._playwright, self._engine_name)

        chromium_args = [
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        if self._is_opera:
            chromium_args += ["--disable-features=OperaPrivacyMode", "--no-private"]
            print("[Browser] 🎭 Opera detected — disabling private-mode flags")

        # ── 1. Try persistent Chrome profile (if not Opera) ─────────────────
        if self._engine_name == "chromium" and not self._is_opera:
            user_data_dir = self._get_chrome_user_data_dir()
            if user_data_dir:
                try:
                    print(f"[Browser] 🔍 Using dedicated JARVIS Chrome profile: {user_data_dir}")

                    launch_kwargs = {
                        "headless": False,
                        "args": chromium_args,
                        "viewport": None,
                        "user_agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                    }

                    # Use the real installed Chrome, not Playwright's bundled Chromium
                    if self._exe_path:
                        launch_kwargs["executable_path"] = self._exe_path
                    elif self._channel:
                        launch_kwargs["channel"] = self._channel

                    self._context = await engine.launch_persistent_context(
                        user_data_dir,
                        **launch_kwargs,
                    )
                    self._browser = self._context.browser
                    print("[Browser] ✅ Launched dedicated JARVIS Chrome profile")
                    return
                except Exception as e:
                    print(f"[Browser] ⚠️ Persistent profile launch failed ({e}) — falling back to temporary profile")

        # ── 2. Fallback: normal launch with temporary profile ───────────────
        launch_kwargs = {"headless": False}
        if self._engine_name == "chromium":
            launch_kwargs["args"] = chromium_args
        if self._exe_path:
            launch_kwargs["executable_path"] = self._exe_path
        elif self._channel:
            launch_kwargs["channel"] = self._channel

        try:
            self._browser = await engine.launch(**launch_kwargs)
            print(
                f"[Browser] ✅ Launched ({self._engine_name}"
                f"{' / ' + self._channel if self._channel else ''}"
                f"{' / ' + self._exe_path if self._exe_path else ''})"
            )
        except Exception as e:
            print(f"[Browser] ⚠️ Launch failed ({e}), falling back to built-in Chromium")
            self._browser = await self._playwright.chromium.launch(
                headless=False,
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            )

    async def _get_page(self):
        await self._launch_browser_if_needed()
        if self._context is None:
            self._context = await self._browser.new_context(
                viewport=None,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
        if self._page is None or self._page.is_closed():
            pages = self._context.pages
            if pages:
                self._page = pages[-1]
            else:
                self._page = await self._context.new_page()
        return self._page

    # ── New: Tab management ─────────────────────────────────────────────────

    async def _new_tab(self, url: str = None) -> str:
        page = await self._context.new_page()
        self._page = page  # switch to the new tab
        if url:
            if not url.startswith("http"):
                url = "https://" + url
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            return f"Opened new tab: {page.url}"
        return "New tab opened."

    async def _switch_tab(self, index: int = None, title: str = None) -> str:
        pages = self._context.pages
        if not pages:
            return "No tabs open."
        if index is not None:
            if 0 <= index < len(pages):
                self._page = pages[index]
                await self._page.bring_to_front()
                return f"Switched to tab {index}: {self._page.url}"
            else:
                return f"Invalid tab index: {index}. Available: 0-{len(pages)-1}"
        if title:
            for i, page in enumerate(pages):
                if title.lower() in (await page.title()).lower():
                    self._page = page
                    await self._page.bring_to_front()
                    return f"Switched to tab: {await page.title()}"
            return f"No tab found with title containing '{title}'."
        return "Specify tab index or title."

    async def _close_tab(self, index: int = None) -> str:
        pages = self._context.pages
        if not pages:
            return "No tabs open."
        if index is None:
            # Close current page if there are others
            if len(pages) > 1:
                await self._page.close()
                self._page = pages[-1]  # switch to last remaining page
                return "Current tab closed."
            else:
                return "Cannot close the last tab. Use 'close_browser' instead."
        else:
            if 0 <= index < len(pages):
                page_to_close = pages[index]
                await page_to_close.close()
                if page_to_close == self._page:
                    self._page = pages[-1] if pages else None
                return f"Closed tab {index}."
            return f"Invalid tab index: {index}."

    async def _list_tabs(self) -> str:
        pages = self._context.pages
        if not pages:
            return "No tabs open."
        lines = []
        for i, page in enumerate(pages):
            title = await page.title()
            url = page.url
            current = " (active)" if page == self._page else ""
            lines.append(f"[{i}] {title[:50]} — {url[:60]}{current}")
        return "Tabs:\n" + "\n".join(lines)

    # ── Enhanced scrolling ──────────────────────────────────────────────────

    async def _scroll(self, direction: str = "down", amount: int = 500, selector: str = None) -> str:
        page = await self._get_page()
        try:
            if selector:
                locator = page.locator(selector).first
                await locator.scroll_into_view_if_needed()
                return f"Scrolled to element: {selector}"
            y = amount if direction == "down" else -amount
            await page.mouse.wheel(0, y)
            return f"Scrolled {direction}."
        except Exception as e:
            return f"Scroll error: {e}"

    async def _scroll_page(self, direction: str = "down") -> str:
        """Scroll a full page up or down."""
        page = await self._get_page()
        key = "PageDown" if direction == "down" else "PageUp"
        await page.keyboard.press(key)
        return f"Page {direction}."

    # ── Navigation ──────────────────────────────────────────────────────────

    async def _reload(self) -> str:
        page = await self._get_page()
        await page.reload(wait_until="domcontentloaded")
        return "Page reloaded."

    # ── Original actions (unchanged except for minor adjustments) ────────────

    async def _go_to(self, url: str) -> str:
        if not url.startswith("http"):
            url = "https://" + url
        page = await self._get_page()
        for attempt in range(2):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await self._human_delay(0.5, 1.5)
                return f"Opened: {page.url}"
            except PlaywrightTimeout:
                if attempt == 0:
                    await self._human_delay(1, 2)
                    continue
                return f"Timeout loading: {url}"
            except Exception as e:
                return f"Navigation error: {e}"
        return f"Timeout loading: {url}"

    async def _search(self, query: str, engine: str = "google") -> str:
        page = await self._get_page()
        original_engine = engine.lower()
        fallback_engines = ["duckduckgo", "bing", "google"]
        if original_engine in fallback_engines:
            fallback_engines.remove(original_engine)
        engines = [original_engine] + fallback_engines

        last_result = ""
        for eng in engines:
            try:
                url = self._build_search_url(query, eng)
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await self._human_delay(1.0, 2.5)

                if eng == "google" and await self._detect_google_sorry(page):
                    print("[Browser] ⚠️ Google CAPTCHA/sorry detected — trying next engine")
                    continue

                return f"Searched {eng}: {page.url}"

            except Exception as e:
                last_result = f"Search on {eng} failed: {e}"
                print(f"[Browser] {last_result}")
                continue

        return last_result or "Search failed."

    async def _click(self, selector=None, text=None) -> str:
        page = await self._get_page()
        try:
            if text:
                await page.get_by_text(text, exact=False).first.click(timeout=8000)
                return f"Clicked: '{text}'"
            elif selector:
                await page.click(selector, timeout=8000)
                return f"Clicked: {selector}"
            return "No selector or text provided."
        except PlaywrightTimeout:
            return "Element not found or not clickable."
        except Exception as e:
            return f"Click error: {e}"

    async def _type(self, selector=None, text: str = "", clear_first: bool = True) -> str:
        page = await self._get_page()
        try:
            element = page.locator(selector).first if selector else page.locator(":focus")
            if clear_first:
                await element.clear()
            await element.type(text, delay=50)
            return "Text typed."
        except Exception as e:
            return f"Type error: {e}"

    async def _press(self, key: str) -> str:
        page = await self._get_page()
        try:
            await page.keyboard.press(key)
            return f"Pressed: {key}"
        except Exception as e:
            return f"Key error: {e}"

    async def _get_text(self) -> str:
        page = await self._get_page()
        try:
            text = await page.inner_text("body")
            return text[:4000] if len(text) > 4000 else text
        except Exception as e:
            return f"Could not get page text: {e}"

    async def _fill_form(self, fields: dict) -> str:
        page    = await self._get_page()
        results = []
        for selector, value in fields.items():
            try:
                el = page.locator(selector).first
                await el.clear()
                await el.type(str(value), delay=40)
                results.append(f"✓ {selector}")
            except Exception as e:
                results.append(f"✗ {selector}: {e}")
        return "Form filled: " + ", ".join(results)

    async def _smart_click(self, description: str) -> str:
        page       = await self._get_page()
        desc_lower = description.lower()

        role_hints = {
            "button":    ["button", "buton", "btn"],
            "link":      ["link", "bağlantı"],
            "searchbox": ["search", "arama"],
            "textbox":   ["input", "field", "alan"],
        }
        for role, keywords in role_hints.items():
            if any(k in desc_lower for k in keywords):
                try:
                    await page.get_by_role(role).first.click(timeout=5000)
                    return f"Clicked ({role}): '{description}'"
                except Exception:
                    pass

        try:
            await page.get_by_text(description, exact=False).first.click(timeout=5000)
            return f"Clicked (text): '{description}'"
        except Exception:
            pass

        try:
            await page.get_by_placeholder(description, exact=False).first.click(timeout=5000)
            return f"Clicked (placeholder): '{description}'"
        except Exception:
            pass

        return f"Could not find: '{description}'"

    async def _smart_type(self, description: str, text: str) -> str:
        page = await self._get_page()

        for method, locator in [
            ("placeholder", page.get_by_placeholder(description, exact=False)),
            ("label",       page.get_by_label(description, exact=False)),
            ("role",        page.get_by_role("textbox")),
        ]:
            try:
                el = locator.first
                await el.clear()
                await el.type(text, delay=50)
                return f"Typed into ({method}): '{description}'"
            except Exception:
                continue

        return f"Could not find input: '{description}'"

    async def _close_browser(self) -> str:
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._context = None
            self._page    = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        return "Browser closed."


    def _get_chrome_user_data_dir(self) -> str | None:
        """Return a dedicated JARVIS Chrome profile directory.

        This avoids conflicts with the user's normal Chrome profile and
        preserves logins/cookies across sessions.
        """
        jarvis_profile = BASE_DIR / "chrome_profile"
        jarvis_profile.mkdir(parents=True, exist_ok=True)
        return str(jarvis_profile)

    async def _human_delay(self, min_sec: float = 0.5, max_sec: float = 2.0):
        """Wait a random human-like amount of time."""
        await asyncio.sleep(random.uniform(min_sec, max_sec))

    def _build_search_url(self, query: str, engine: str) -> str:
        engines = {
            "google":     f"https://www.google.com/search?q={query.replace(' ', '+')}",
            "bing":       f"https://www.bing.com/search?q={query.replace(' ', '+')}",
            "duckduckgo": f"https://duckduckgo.com/?q={query.replace(' ', '+')}",
        }
        return engines.get(engine, engines["google"])

    async def _detect_google_sorry(self, page) -> bool:
        """Detect Google CAPTCHA / sorry index pages."""
        try:
            content = await page.content()
            text = content.lower()
            if "sorry/index" in page.url:
                return True
            if "unusual traffic" in text and "sorry" in text:
                return True
            if "recaptcha" in text or "captcha" in text:
                return True
        except Exception:
            pass
        return False

# ── Singleton browser thread ─────────────────────────────────────────────────

_bt         = _BrowserThread()
_bt_started = False
_bt_lock    = threading.Lock()

def _reset_browser_state():
    """Reset in-memory browser state on hard reset."""
    try:
        _bt._browser = None
        _bt._context = None
        _bt._page = None
    except Exception:
        pass


reset_controller.register("browser_control", _reset_browser_state)


def _ensure_started():
    global _bt_started
    with _bt_lock:
        if not _bt_started:
            _bt.start()
            _bt_started = True


# ── Public API (updated parameter list) ──────────────────────────────────────

def browser_control(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None
) -> str:
    """
    Browser controller — auto-detects and uses system default browser.
    Supports full tab management, scrolling, and navigation.

    parameters:
        action      : go_to | search | click | type | scroll | fill_form |
                      smart_click | smart_type | get_text | press | close |
                      new_tab | switch_tab | close_tab | list_tabs |
                      scroll_page | reload
        url         : URL for go_to / new_tab
        query       : search query
        engine      : google | bing | duckduckgo
        selector    : CSS selector for click/type/scroll into view
        text        : text to click or type
        description : element description for smart_click/smart_type
        direction   : up | down (for scroll / scroll_page)
        amount      : scroll pixels (default: 500)
        key         : key name for press
        fields      : {selector: value} dict for fill_form
        clear_first : bool, clear input before typing (default: True)
        index       : tab index for switch_tab/close_tab
        title       : tab title fragment for switch_tab
    """
    _ensure_started()

    action = (parameters or {}).get("action", "").lower().strip()
    result = "Unknown action."

    try:
        if action == "go_to":
            result = _bt.run(_bt._go_to(parameters.get("url", "")))

        elif action == "search":
            result = _bt.run(_bt._search(
                parameters.get("query", ""),
                parameters.get("engine", "duckduckgo"),
            ))

        elif action == "click":
            result = _bt.run(_bt._click(
                selector=parameters.get("selector"),
                text=parameters.get("text"),
            ))

        elif action == "type":
            result = _bt.run(_bt._type(
                selector=parameters.get("selector"),
                text=parameters.get("text", ""),
                clear_first=parameters.get("clear_first", True),
            ))

        elif action == "scroll":
            result = _bt.run(_bt._scroll(
                direction=parameters.get("direction", "down"),
                amount=parameters.get("amount", 500),
                selector=parameters.get("selector"),
            ))

        elif action == "scroll_page":
            result = _bt.run(_bt._scroll_page(
                direction=parameters.get("direction", "down"),
            ))

        elif action == "fill_form":
            result = _bt.run(_bt._fill_form(parameters.get("fields", {})))

        elif action == "smart_click":
            result = _bt.run(_bt._smart_click(parameters.get("description", "")))

        elif action == "smart_type":
            result = _bt.run(_bt._smart_type(
                parameters.get("description", ""),
                parameters.get("text", ""),
            ))

        elif action == "get_text":
            result = _bt.run(_bt._get_text())

        elif action == "press":
            result = _bt.run(_bt._press(parameters.get("key", "Enter")))

        elif action == "reload":
            result = _bt.run(_bt._reload())

        # ── New tab management actions ────────────────────────────────────
        elif action == "new_tab":
            result = _bt.run(_bt._new_tab(
                url=parameters.get("url"),
            ))

        elif action == "switch_tab":
            result = _bt.run(_bt._switch_tab(
                index=parameters.get("index"),
                title=parameters.get("title"),
            ))

        elif action == "close_tab":
            result = _bt.run(_bt._close_tab(
                index=parameters.get("index"),
            ))

        elif action == "list_tabs":
            result = _bt.run(_bt._list_tabs())

        elif action == "close":
            result = _bt.run(_bt._close_browser())

        else:
            result = f"Unknown action: {action}"

    except concurrent.futures.TimeoutError:
        result = "Browser action timed out."
    except Exception as e:
        result = f"Browser error: {e}"

    print(f"[Browser] {result[:80]}")
    if player:
        player.write_log(f"[browser] {result[:60]}")

    return result