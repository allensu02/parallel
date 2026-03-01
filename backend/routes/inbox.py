"""Inbox routes — fetch threads for the email selection UI.

Uses Gmail API when OAuth is configured, falls back to Playwright.

Performance:
  - Thread list uses BatchHttpRequest (1 HTTP round-trip for all metadata).
  - After returning the list, a background task pre-fetches ALL thread
    content via another BatchHttpRequest so expansions are instant.
  - Batch endpoint also uses BatchHttpRequest for bulk content fetches.
  - Server-side cache with 10 min TTL avoids redundant fetches.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from backend.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET

router = APIRouter()

_USE_API = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

# ---------------------------------------------------------------------------
# Server-side content cache (thread_id -> {data, ts})
# ---------------------------------------------------------------------------

_content_cache: dict[str, dict[str, Any]] = {}
_CACHE_TTL = 600  # 10 minutes


def _get_cached(thread_id: str) -> dict | None:
    entry = _content_cache.get(thread_id)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _set_cached(thread_id: str, data: dict) -> None:
    _content_cache[thread_id] = {"data": data, "ts": time.time()}


# ---------------------------------------------------------------------------
# Background pre-fetch task
# ---------------------------------------------------------------------------
_prefetch_lock = asyncio.Lock()
_prefetch_running = False


async def _background_prefetch_content(thread_ids: list[str]):
    """Batch-fetch full content for all thread IDs and cache them."""
    global _prefetch_running
    async with _prefetch_lock:
        if _prefetch_running:
            return
        _prefetch_running = True

    try:
        # Only fetch IDs that aren't already cached
        to_fetch = [tid for tid in thread_ids if not _get_cached(tid)]
        if not to_fetch:
            return

        from backend.services.google_api import batch_fetch_thread_contents

        t0 = time.time()
        results = await batch_fetch_thread_contents(to_fetch)
        elapsed = time.time() - t0
        cached_count = 0
        for tid, data in results.items():
            if data:
                _set_cached(tid, data)
                cached_count += 1
        print(
            f"[Inbox] Background pre-fetched {cached_count}/{len(to_fetch)} "
            f"thread contents in {elapsed:.1f}s"
        )
    except Exception as e:
        print(f"[Inbox] Background pre-fetch error: {e}")
    finally:
        _prefetch_running = False


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------


@router.get("/threads")
async def list_inbox_threads(limit: int = 50, background_tasks: BackgroundTasks = None):
    """Return inbox threads with metadata for the selection UI.

    Also kicks off a background batch-fetch of ALL thread contents so
    that expanding any email in the UI is instant.
    """
    if _USE_API:
        result = await _list_via_api(limit)
        # Kick off background pre-fetch of all content
        if background_tasks and result.get("threads"):
            thread_ids = [t["id"] for t in result["threads"]]
            background_tasks.add_task(_background_prefetch_content, thread_ids)
        return result
    else:
        return await _list_via_playwright(limit)


class BatchFetchRequest(BaseModel):
    thread_ids: list[str]


@router.post("/threads/batch")
async def batch_fetch_threads(req: BatchFetchRequest):
    """Pre-fetch content for multiple threads via BatchHttpRequest.

    Returns {results: {thread_id: content_or_null}}
    """
    results: dict[str, Any] = {}
    to_fetch: list[str] = []

    for tid in req.thread_ids:
        cached = _get_cached(tid)
        if cached:
            results[tid] = cached
        else:
            to_fetch.append(tid)

    if to_fetch:
        if _USE_API:
            from backend.services.google_api import batch_fetch_thread_contents

            batch_results = await batch_fetch_thread_contents(to_fetch)
            for tid, data in batch_results.items():
                if data:
                    _set_cached(tid, data)
                results[tid] = data
        else:
            for tid in to_fetch:
                try:
                    data = await _get_via_playwright(tid)
                    _set_cached(tid, data)
                    results[tid] = data
                except Exception:
                    results[tid] = None

    return {"results": results}


@router.get("/threads/{thread_id}")
async def get_thread_content(thread_id: str):
    """Return full content of a single thread (instant if pre-fetched)."""
    cached = _get_cached(thread_id)
    if cached:
        return cached

    if _USE_API:
        data = await _get_via_api(thread_id)
    else:
        data = await _get_via_playwright(thread_id)
    _set_cached(thread_id, data)
    return data


# ---------------------------------------------------------------------------
# Gmail API path
# ---------------------------------------------------------------------------


async def _list_via_api(limit: int):
    try:
        from backend.services.google_api import fetch_inbox_threads_api
        t0 = time.time()
        threads = await fetch_inbox_threads_api(max_results=limit)
        elapsed = time.time() - t0
        print(f"[Inbox] Listed {len(threads)} threads in {elapsed:.1f}s")
        return {"threads": threads}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gmail API error: {e}")


async def _get_via_api(thread_id: str):
    try:
        from backend.services.google_api import fetch_thread_content_api
        return await fetch_thread_content_api(thread_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gmail API error: {e}")


# ---------------------------------------------------------------------------
# Playwright fallback path
# ---------------------------------------------------------------------------


async def _list_via_playwright(limit: int):
    from backend.agent.browser_harness import harness
    from backend.agent.text_extractor import fetch_inbox_threads

    if not harness._started:
        try:
            await harness.start()
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="Browser not ready — please log in first",
            )
    if not harness.authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated with Gmail")

    page = None
    try:
        page = await harness.acquire_page()
        threads = await fetch_inbox_threads(page, max_results=limit)
        return {"threads": threads}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch inbox: {e}")
    finally:
        if page:
            await harness.release_page(page)


async def _get_via_playwright(thread_id: str):
    from backend.agent.browser_harness import harness
    from backend.agent.text_extractor import fetch_thread_preview

    if not harness._started:
        raise HTTPException(status_code=503, detail="Browser not ready")

    page = None
    try:
        page = await harness.acquire_page()
        content = await fetch_thread_preview(page, thread_id)
        return content
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch thread: {e}")
    finally:
        if page:
            await harness.release_page(page)
