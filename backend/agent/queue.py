"""Async job queue — dispatches jobs to workers with concurrency control."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from backend.config import MAX_CONCURRENT_JOBS


class JobQueue:
    """Simple asyncio-based job queue with bounded concurrency."""

    def __init__(self, max_workers: int = MAX_CONCURRENT_JOBS) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_workers)
        self._workers: list[asyncio.Task] = []
        self._handler: Callable[..., Coroutine] | None = None
        self._running = False

    def set_handler(self, handler: Callable[..., Coroutine]) -> None:
        """Set the async function that processes each job."""
        self._handler = handler

    async def put(self, job: dict[str, Any]) -> None:
        await self._queue.put(job)

    async def start(self, num_workers: int | None = None) -> None:
        """Start worker tasks."""
        if self._running:
            return
        self._running = True
        n = num_workers or MAX_CONCURRENT_JOBS
        for _ in range(n):
            task = asyncio.create_task(self._worker())
            self._workers.append(task)

    async def _worker(self) -> None:
        while self._running:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                if self._queue.empty():
                    break
                continue
            except asyncio.CancelledError:
                break

            async with self._semaphore:
                if self._handler:
                    try:
                        await self._handler(job)
                    except Exception as exc:
                        # Handler should catch its own errors, but just in case
                        print(f"[Queue] Unhandled error in job handler: {exc}")
                self._queue.task_done()

    async def wait_done(self) -> None:
        """Wait for all queued jobs to complete."""
        await self._queue.join()
        self._running = False
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
