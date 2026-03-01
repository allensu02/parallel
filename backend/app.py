"""FastAPI application — entrypoint for the Hive backend."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import FRONTEND_URL
from backend.database import get_db, close_db
from backend.agent.browser_harness import harness, SCREENSHOTS_DIR
from backend.routes.auth import router as auth_router
from backend.routes.runs import router as runs_router
from backend.routes.events import router as events_router
from backend.routes.inbox import router as inbox_router
from backend.routes.profile import router as profile_router
from backend.routes.swarm import router as swarm_router
from backend.routes.demo import router as demo_router


async def _recover_stale_runs():
    """Mark any runs stuck in 'running'/'queued' as failed on startup.

    This happens when the server restarts mid-run — the background tasks
    are lost, so the runs would stay in 'running' forever.
    """
    from datetime import datetime, timezone
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    # Mark stale runs
    await db.execute(
        "UPDATE runs SET status = 'failed', finished_at = ? WHERE status IN ('running', 'queued')",
        (now,),
    )
    # Mark stale jobs
    await db.execute(
        "UPDATE jobs SET status = 'skipped', current_step = 'done', finished_at = ? "
        "WHERE status IN ('running', 'queued', 'pending_approval')",
        (now,),
    )
    # Mark stale swarms
    await db.execute(
        "UPDATE swarms SET status = 'failed', finished_at = ? WHERE status IN ('running', 'queued')",
        (now,),
    )
    await db.execute(
        "UPDATE swarm_agents SET status = 'skipped', finished_at = ? "
        "WHERE status IN ('running', 'queued')",
        (now,),
    )
    await db.commit()
    print("[Startup] Recovered any stale runs/jobs/swarms from previous session.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure DB is initialised
    await get_db()
    # Recover any runs that were interrupted by a server restart
    await _recover_stale_runs()
    # Start headless browser (no visible windows — streamed to hex UI)
    try:
        await harness.start()
        await harness.ensure_gmail_auth()
    except Exception as e:
        print(f"[Startup] Browser harness: {e}")
    yield
    # Shutdown
    await harness.stop()
    await close_db()


app = FastAPI(
    title="Hive — Email Swarm API",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS — allow the Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve screenshots as static files
SCREENSHOTS_DIR.mkdir(exist_ok=True)
app.mount("/screenshots", StaticFiles(directory=str(SCREENSHOTS_DIR)), name="screenshots")

# Mount route groups
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(inbox_router, prefix="/api/inbox", tags=["inbox"])
app.include_router(runs_router, prefix="/api/runs", tags=["runs"])
app.include_router(events_router, prefix="/api/events", tags=["events"])
app.include_router(profile_router, prefix="/api/profile", tags=["profile"])
app.include_router(swarm_router, prefix="/api/swarm", tags=["swarm"])
app.include_router(demo_router, prefix="/api/demo", tags=["demo"])


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "hive-backend",
        "browser_ready": harness._started,
        "authenticated": harness.authenticated,
    }


@app.get("/api/pipelines")
async def list_pipelines():
    """List all available task pipelines."""
    from backend.agent.pipelines import list_pipeline_types
    return list_pipeline_types()
