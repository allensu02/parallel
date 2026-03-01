"""FastAPI application — entrypoint for the Parallel backend."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import FRONTEND_URL
from backend.database import get_db, close_db
from backend.routes.auth import router as auth_router
from backend.routes.runs import router as runs_router
from backend.routes.events import router as events_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure DB is initialised
    await get_db()
    yield
    # Shutdown
    await close_db()


app = FastAPI(
    title="Parallel Agent API",
    version="0.1.0",
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

# Mount route groups
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(runs_router, prefix="/api/runs", tags=["runs"])
app.include_router(events_router, prefix="/api/events", tags=["events"])


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "parallel-agent-backend"}
