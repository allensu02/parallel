"""Browser harness — launches REAL Chrome with remote debugging for Gmail access.

Strategy:
  - Launch the user's actual Chrome installation (/Applications/Google Chrome.app)
    with --remote-debugging-port and a dedicated persistent profile directory.
  - Real Chrome is NOT flagged as an automated browser, so Google sign-in works.
  - Playwright connects via CDP for page management, screenshots, and screencast.
  - The persistent profile (~/.hive-chrome-profile/) retains cookies across restarts,
    so the user only needs to sign in to Gmail ONCE.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
    Playwright,
)

from backend.config import BROWSER_POOL_SIZE

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BASE = Path(__file__).resolve().parent.parent
STORAGE_STATE_PATH = _BASE / "gmail_auth_state.json"
SCREENSHOTS_DIR = _BASE / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)

# Persistent Chrome profile — survives restarts, keeps cookies
CHROME_PROFILE_DIR = Path.home() / ".hive-chrome-profile"
CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

GMAIL_INBOX = "https://mail.google.com/mail/u/0/#inbox"

# Find the real Chrome executable
_CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    shutil.which("google-chrome") or "",
    shutil.which("google-chrome-stable") or "",
]


def _find_chrome() -> str | None:
    for p in _CHROME_PATHS:
        if p and Path(p).exists():
            return p
    return None


# ---------------------------------------------------------------------------
# Browser Harness
# ---------------------------------------------------------------------------

_CDP_PORT = 9222


class BrowserHarness:
    """Manages a real Chrome browser with Playwright page pool."""

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page_pool: asyncio.Queue[Page] = asyncio.Queue()
        self._pool_size: int = BROWSER_POOL_SIZE
        self._pool_created: int = 0
        self._started: bool = False
        self._authenticated: bool = False
        self._email: str = ""
        self._chrome_proc: subprocess.Popen | None = None


    @property
    def authenticated(self) -> bool:
        return self._authenticated

    @property
    def email(self) -> str:
        return self._email

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start headless Chrome (or connect to existing) for background browser work.
        No visible windows — everything is streamed via CDP screencast to the hex UI."""
        if self._started:
            return

        import urllib.request
        cdp_url = f"http://127.0.0.1:{_CDP_PORT}"

        # --- Try connecting to already-running Chrome ---
        chrome_running = False
        try:
            resp = urllib.request.urlopen(f"{cdp_url}/json/version", timeout=2)
            import json as _json
            info = _json.loads(resp.read())
            browser_str = info.get("Browser", "")

            if "headless" in browser_str.lower() or "HeadlessChrome" in browser_str:
                chrome_running = True
                print(f"[Harness] Reusing headless Chrome on port {_CDP_PORT}")
            else:
                # Visible Chrome is running — kill it so we can relaunch headless
                print(f"[Harness] Found visible Chrome ({browser_str}) — restarting as headless...")
                await self._kill_stale_chrome()
                chrome_running = False
        except Exception:
            pass

        if not chrome_running:
            chrome_path = _find_chrome()
            if not chrome_path:
                raise RuntimeError("Chrome not found. Install Google Chrome.")

            chrome_args = [
                chrome_path,
                f"--remote-debugging-port={_CDP_PORT}",
                f"--user-data-dir={CHROME_PROFILE_DIR}",
                "--headless=new",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-default-apps",
                "--disable-popup-blocking",
                "--disable-translate",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--noerrdialogs",
                "--disable-session-crashed-bubble",
                "--window-size=1280,900",
            ]

            print(f"[Harness] Starting headless Chrome (profile: {CHROME_PROFILE_DIR})")
            self._chrome_proc = subprocess.Popen(
                chrome_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

            for _ in range(30):
                try:
                    urllib.request.urlopen(f"{cdp_url}/json/version", timeout=1)
                    break
                except Exception:
                    await asyncio.sleep(0.5)
            else:
                raise RuntimeError(f"Chrome failed to start on port {_CDP_PORT}")

        # --- Connect Playwright via CDP ---
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.connect_over_cdp(cdp_url)

        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
            # Close leftover tabs from previous sessions
            for p in list(self._context.pages):
                try:
                    await p.close()
                except Exception:
                    pass
        else:
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 900},
            )

        # One page ready; rest created on-demand up to pool_size
        page = await self._context.new_page()
        await self._page_pool.put(page)
        self._pool_created = 1
        self._started = True
        print(f"[Harness] Ready (headless, pool 1/{self._pool_size})")

    async def _kill_stale_chrome(self) -> None:
        """Kill any Chrome process using our debug port."""
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{_CDP_PORT}"],
                capture_output=True, text=True, timeout=5,
            )
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                if pid.strip():
                    os.kill(int(pid.strip()), 9)
                    print(f"[Harness] Killed stale Chrome process {pid.strip()}")
            if any(p.strip() for p in pids):
                await asyncio.sleep(1)  # Give OS time to release port
        except Exception:
            pass

    async def stop(self) -> None:
        """Disconnect Playwright. NEVER kill Chrome — it persists across restarts."""
        # Close pages we created (so they don't pile up)
        while not self._page_pool.empty():
            try:
                p = self._page_pool.get_nowait()
                await p.close()
            except Exception:
                pass

        # Disconnect Playwright only (Chrome stays alive with session cookies)
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass

        # Do NOT kill Chrome — it must persist across server restarts
        self._browser = None
        self._context = None
        self._pw = None
        self._chrome_proc = None
        self._pool_created = 0
        self._started = False
        # Keep _authenticated — Chrome session persists

    # ------------------------------------------------------------------
    # Authentication — ensure Gmail access
    # ------------------------------------------------------------------

    async def ensure_gmail_auth(self) -> bool:
        """Quick check: does the persistent Chrome profile have Gmail cookies?
        No visible windows, no user interaction needed. Just a headless check."""
        if self._authenticated:
            return True
        if not self._started:
            return False

        page = await self.acquire_page()
        try:
            await page.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)
            url = page.url

            if ("mail.google.com" in url
                    and "ServiceLogin" not in url
                    and "accounts.google.com" not in url
                    and "workspace.google.com" not in url):
                self._authenticated = True
                self._email = await page.evaluate("""
                    () => {
                        const el = document.querySelector('[data-email]');
                        return el ? el.getAttribute('data-email') : '';
                    }
                """) or ""
                print(f"[Harness] Authenticated as: {self._email}")
                return True

            # Try clicking account chooser if available
            if "accounts.google.com" in url:
                try:
                    has_accounts = await page.evaluate(
                        "() => document.querySelectorAll('[data-identifier]').length > 0"
                    )
                    if has_accounts:
                        el = await page.wait_for_selector('div[data-identifier]', timeout=2000)
                        if el:
                            await el.click()
                            await page.wait_for_url("**/mail.google.com/**", timeout=15000)
                            self._authenticated = True
                            print("[Harness] Authenticated via account chooser")
                            return True
                except Exception:
                    pass

            print(f"[Harness] Not authenticated (at: {url[:60]})")
            return False
        except Exception as e:
            print(f"[Harness] Auth check error: {e}")
            return False
        finally:
            if page:
                await self.release_page(page)


    # ------------------------------------------------------------------
    # Page pool
    # ------------------------------------------------------------------

    async def acquire_page(self) -> Page:
        """Get a page from the pool, creating on-demand up to pool_size. Never exceeds cap."""
        # Try to get from pool first (non-blocking)
        while not self._page_pool.empty():
            page = self._page_pool.get_nowait()
            try:
                if not page.is_closed():
                    await page.evaluate("1+1")
                    return page
            except Exception:
                self._pool_created = max(0, self._pool_created - 1)
                try:
                    await page.close()
                except Exception:
                    pass

        # Pool empty — create a new page if under cap
        if self._pool_created < self._pool_size and self._context:
            new_page = await self._context.new_page()
            self._pool_created += 1
            return new_page

        # At cap — wait for a page to be released
        page = await asyncio.wait_for(self._page_pool.get(), timeout=120)
        try:
            if not page.is_closed():
                await page.evaluate("1+1")
                return page
        except Exception:
            self._pool_created = max(0, self._pool_created - 1)

        # Last resort — create replacement if one died
        if self._context:
            new_page = await self._context.new_page()
            self._pool_created += 1
            return new_page
        raise RuntimeError("No browser context available")

    async def release_page(self, page: Page) -> None:
        """Return a page to the pool. Navigate to about:blank to free memory."""
        try:
            if page.is_closed():
                self._pool_created = max(0, self._pool_created - 1)
                return
            await page.goto("about:blank", timeout=5000)
            await self._page_pool.put(page)
        except Exception:
            self._pool_created = max(0, self._pool_created - 1)
            try:
                await page.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Screenshot helpers
    # ------------------------------------------------------------------

    async def save_screenshot(self, page: Page, job_id: str, step: str) -> str:
        filename = f"{job_id}_{step}_{int(time.time() * 1000)}.png"
        filepath = SCREENSHOTS_DIR / filename
        await page.screenshot(path=str(filepath), full_page=False)
        return f"/screenshots/{filename}"

    def save_screenshot_b64(self, b64_data: str, job_id: str, step: str) -> str:
        import base64 as b64mod
        filename = f"{job_id}_{step}_{int(time.time() * 1000)}.png"
        filepath = SCREENSHOTS_DIR / filename
        filepath.write_bytes(b64mod.b64decode(b64_data))
        return f"/screenshots/{filename}"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

harness = BrowserHarness()
