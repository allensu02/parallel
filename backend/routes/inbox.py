"""Inbox routes — fetch threads for the email selection UI.

Uses Gmail API when OAuth is configured, falls back to Playwright.
Includes a server-side content cache and batch pre-fetch endpoint.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException
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
# Public routes
# ---------------------------------------------------------------------------


@router.get("/threads")
async def list_inbox_threads(limit: int = 50):
    """Return inbox threads with metadata for the selection UI."""
    if _USE_API:
        return await _list_via_api(limit)
    else:
        return await _list_via_playwright(limit)


class BatchFetchRequest(BaseModel):
    thread_ids: list[str]


@router.post("/threads/batch")
async def batch_fetch_threads(req: BatchFetchRequest):
    """Pre-fetch content for multiple threads in parallel.

    Returns {results: {thread_id: content_or_null}}
    Used to warm the cache when the user selects emails for swarming.
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
            # Gmail API supports parallel fetches
            tasks = [_get_via_api_safe(tid) for tid in to_fetch]
            fetched = await asyncio.gather(*tasks)
            for tid, data in zip(to_fetch, fetched):
                if data:
                    _set_cached(tid, data)
                results[tid] = data
        else:
            # Playwright: sequential (single page), but still faster than on-demand
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
    """Return full content of a single thread for preview (uses cache)."""
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
        threads = await fetch_inbox_threads_api(max_results=limit)
        return {"threads": threads}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gmail API error: {e}")


async def _get_via_api(thread_id: str):
    try:
        from backend.services.google_api import fetch_thread_content_api
        return await fetch_thread_content_api(thread_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gmail API error: {e}")


async def _get_via_api_safe(thread_id: str) -> dict | None:
    """Non-throwing version for batch operations."""
    try:
        from backend.services.google_api import fetch_thread_content_api
        return await fetch_thread_content_api(thread_id)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Playwright fallback path
# ---------------------------------------------------------------------------


async def _list_via_playwright(limit: int):
    from backend.agent.browser_harness import harness
    from backend.agent.text_extractor import fetch_inbox_threads

    if not harness._started:
        try:
            await harness.start(headless=True)
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
