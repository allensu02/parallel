"""Playwright browser harness — manages browser lifecycle and page pool.

All Gmail interaction is delegated to the Claude computer use agent
(computer_use.py). This module only handles:
- Browser launch / shutdown
- Auth state persistence (login via visible browser, save cookies)
- Page pool for concurrent agent work
- Screenshot saving to disk
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

# Tell Playwright to look for browsers inside the package directory
# (where `playwright install` with PLAYWRIGHT_BROWSERS_PATH=0 placed them)
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

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

GMAIL_INBOX = "https://mail.google.com/mail/u/0/#inbox"


# ---------------------------------------------------------------------------
# Browser Harness
# ---------------------------------------------------------------------------


class BrowserHarness:
    """Manages a Playwright Chromium browser and a pool of pages."""

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page_pool: asyncio.Queue[Page] = asyncio.Queue()
        self._pool_size: int = BROWSER_POOL_SIZE
        self._started: bool = False
        self._authenticated: bool = False
        self._email: str = ""

        # Login browser refs (temporary, closed after login)
        self._login_browser: Browser | None = None
        self._login_context: BrowserContext | None = None
        self._login_page: Page | None = None

    @property
    def authenticated(self) -> bool:
        return self._authenticated and STORAGE_STATE_PATH.exists()

    @property
    def email(self) -> str:
        return self._email

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, headless: bool = True) -> None:
        """Launch browser and populate the page pool."""
        if self._started:
            return

        self._pw = await async_playwright().start()

        storage = str(STORAGE_STATE_PATH) if STORAGE_STATE_PATH.exists() else None

        self._browser = await self._pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=SkiaGraphite",
            ],
        )
        self._context = await self._browser.new_context(
            storage_state=storage,
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
        )

        for _ in range(self._pool_size):
            page = await self._context.new_page()
            await self._page_pool.put(page)

        self._started = True

        # Check if already authenticated
        if storage:
            try:
                test_page = await self.acquire_page()
                await test_page.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=15000)
                await test_page.wait_for_timeout(3000)
                url = test_page.url
                if "mail.google.com" in url and "ServiceLogin" not in url:
                    self._authenticated = True
                    self._email = await test_page.evaluate("""
                        () => {
                            const el = document.querySelector('[data-email]');
                            return el ? el.getAttribute('data-email') : '';
                        }
                    """) or ""
                await self.release_page(test_page)
            except Exception:
                pass

    async def stop(self) -> None:
        """Shut down browser."""
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._started = False

    # ------------------------------------------------------------------
    # Authentication — visible browser for manual Gmail login
    # ------------------------------------------------------------------

    async def start_login(self) -> None:
        """Open a visible Chromium window for the user to log into Gmail."""
        if not self._pw:
            self._pw = await async_playwright().start()

        self._login_browser = await self._pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._login_context = await self._login_browser.new_context(
            viewport={"width": 1100, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
        )
        self._login_page = await self._login_context.new_page()
        await self._login_page.goto(GMAIL_INBOX)

    async def check_login_complete(self) -> bool:
        """Check if the user has finished logging in."""
        if not self._login_page:
            return False
        try:
            url = self._login_page.url
            if "mail.google.com" in url and "ServiceLogin" not in url:
                # Save storage state
                state = await self._login_context.storage_state()
                STORAGE_STATE_PATH.write_text(json.dumps(state))

                self._email = await self._login_page.evaluate("""
                    () => {
                        const el = document.querySelector('[data-email]');
                        return el ? el.getAttribute('data-email') : '';
                    }
                """) or "authenticated"
                self._authenticated = True

                # Close login browser
                await self._login_browser.close()
                self._login_page = None
                self._login_browser = None
                self._login_context = None

                # Restart headless browser with saved auth
                if self._started:
                    await self.stop()
                await self.start(headless=True)

                return True
        except Exception:
            pass
        return False

    async def finish_login(self) -> dict:
        """Poll until the user completes Gmail login (2 min timeout)."""
        for _ in range(120):
            if await self.check_login_complete():
                return {"authenticated": True, "email": self._email}
            await asyncio.sleep(1)
        return {"authenticated": False, "email": ""}

    # ------------------------------------------------------------------
    # Page pool
    # ------------------------------------------------------------------

    async def acquire_page(self) -> Page:
        """Get a live page from the pool (blocks if none available).

        If the page pulled from the pool is closed/crashed, discard it
        and create a fresh one from the current context.
        """
        while True:
            page = await asyncio.wait_for(self._page_pool.get(), timeout=120)
            try:
                # Quick liveness check — evaluate a trivial expression
                if page.is_closed():
                    raise RuntimeError("page is closed")
                await page.evaluate("1+1")
                return page
            except Exception:
                # Page is dead — create a replacement
                try:
                    if self._context:
                        new_page = await self._context.new_page()
                        return new_page
                except Exception:
                    pass
                # If context is also dead, try to restart
                if self._context is None or self._browser is None:
                    await self.stop()
                    await self.start(headless=True)
                    page = await asyncio.wait_for(self._page_pool.get(), timeout=120)
                    return page

    async def release_page(self, page: Page) -> None:
        """Return a page to the pool. If the page is dead, replace it."""
        try:
            if page.is_closed():
                raise RuntimeError("page is closed")
            await page.goto("about:blank", timeout=5000)
            await self._page_pool.put(page)
        except Exception:
            # Page is dead — create a replacement if context is alive
            try:
                if self._context:
                    new_page = await self._context.new_page()
                    await self._page_pool.put(new_page)
            except Exception:
                pass  # Pool shrinks by 1; will be replenished on next start

    # ------------------------------------------------------------------
    # Screenshot helpers
    # ------------------------------------------------------------------

    async def save_screenshot(self, page: Page, job_id: str, step: str) -> str:
        """Take a screenshot, save to disk, return the URL path."""
        filename = f"{job_id}_{step}_{int(time.time() * 1000)}.png"
        filepath = SCREENSHOTS_DIR / filename
        await page.screenshot(path=str(filepath), full_page=False)
        return f"/screenshots/{filename}"

    def save_screenshot_b64(self, b64_data: str, job_id: str, step: str) -> str:
        """Save a base64 screenshot to disk, return the URL path."""
        import base64 as b64mod
        filename = f"{job_id}_{step}_{int(time.time() * 1000)}.png"
        filepath = SCREENSHOTS_DIR / filename
        filepath.write_bytes(b64mod.b64decode(b64_data))
        return f"/screenshots/{filename}"

    # ------------------------------------------------------------------
    # Inbox thread listing (uses JS extraction — simple and reliable)
    # ------------------------------------------------------------------

    async def fetch_thread_list(self, max_results: int = 100) -> list[dict]:
        """Navigate to Gmail inbox and extract thread list via JS."""
        print(f"[Harness] fetch_thread_list called, started={self._started}, authenticated={self._authenticated}")
        page = await self.acquire_page()
        try:
            await page.goto(GMAIL_INBOX, wait_until="domcontentloaded", timeout=20000)
            url = page.url
            print(f"[Harness] Navigated to: {url}")
            await page.wait_for_selector("tr.zA", timeout=15000)
            await page.wait_for_timeout(2000)
            print("[Harness] Found tr.zA rows, extracting...")

            threads = await page.evaluate("""
                () => {
                    const rows = document.querySelectorAll('tr.zA');
                    const threads = [];
                    const seen = new Set();
                    for (const row of rows) {
                        // Thread ID: span.bqe has data-thread-id like "#thread-f:NUMBER"
                        const threadSpan = row.querySelector('span.bqe[data-thread-id]');
                        const rawId = threadSpan ? threadSpan.getAttribute('data-thread-id') : '';
                        const match = rawId.match(/thread-f:(\\d+)/);
                        const threadId = match ? match[1] : '';
                        if (!threadId || seen.has(threadId)) continue;
                        seen.add(threadId);

                        // Subject: span.bog
                        const subjectEl = row.querySelector('span.bog');
                        const subject = subjectEl ? subjectEl.textContent.trim() : '(no subject)';

                        // Sender: span.zF
                        const senderEl = row.querySelector('span.zF');
                        const sender = senderEl ? senderEl.textContent.trim() : '';

                        // Snippet: span.y2
                        const snippetEl = row.querySelector('span.y2');
                        const snippet = snippetEl ? snippetEl.textContent.trim() : '';

                        threads.push({ id: threadId, subject, snippet, sender });
                    }
                    return threads;
                }
            """)
            print(f"[Harness] Extracted {len(threads)} threads")
            return threads[:max_results]
        except Exception as e:
            print(f"[Harness] fetch_thread_list ERROR: {type(e).__name__}: {e}")
            url = page.url
            print(f"[Harness] Current URL at error: {url}")
            return []
        finally:
            await self.release_page(page)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

harness = BrowserHarness()
