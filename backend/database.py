"""SQLite database layer using aiosqlite."""

from __future__ import annotations

import aiosqlite
import uuid
from datetime import datetime, timezone

from backend.config import DATABASE_PATH

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _uuid() -> str:
    return uuid.uuid4().hex[:12]

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'queued',
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    total_jobs     INTEGER NOT NULL DEFAULT 0,
    completed_jobs INTEGER NOT NULL DEFAULT 0,
    failed_jobs    INTEGER NOT NULL DEFAULT 0,
    skipped_jobs   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES runs(id),
    thread_id       TEXT NOT NULL,
    subject         TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'queued',
    current_step    TEXT NOT NULL DEFAULT '',
    attempt         INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT UNIQUE,
    error_msg       TEXT,
    intent          TEXT,
    confidence      REAL,
    draft_id        TEXT,
    summary         TEXT,
    tokens_used     INTEGER NOT NULL DEFAULT 0,
    started_at      TEXT,
    finished_at     TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES jobs(id),
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    started_at  TEXT,
    finished_at TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error_msg   TEXT
);

CREATE TABLE IF NOT EXISTS questions (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES jobs(id),
    run_id      TEXT NOT NULL,
    question    TEXT NOT NULL,
    context     TEXT NOT NULL DEFAULT '',
    answer      TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TEXT NOT NULL,
    answered_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_questions_job ON questions(job_id);
CREATE INDEX IF NOT EXISTS idx_questions_run ON questions(run_id);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    id            TEXT PRIMARY KEY DEFAULT 'default',
    access_token  TEXT,
    refresh_token TEXT,
    token_uri     TEXT,
    client_id     TEXT,
    client_secret TEXT,
    expiry        TEXT,
    email         TEXT
);

CREATE TABLE IF NOT EXISTS user_profiles (
    id          TEXT PRIMARY KEY DEFAULT 'default',
    email       TEXT,
    style_profile TEXT DEFAULT '{}',
    preferences TEXT DEFAULT '{}',
    edit_diffs  TEXT DEFAULT '[]',
    created_at  TEXT,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_run ON jobs(run_id);
CREATE INDEX IF NOT EXISTS idx_steps_job ON steps(job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_idemp ON jobs(idempotency_key);

CREATE TABLE IF NOT EXISTS swarms (
    id               TEXT PRIMARY KEY,
    status           TEXT NOT NULL DEFAULT 'queued',
    created_at       TEXT NOT NULL,
    finished_at      TEXT,
    total_agents     INTEGER NOT NULL DEFAULT 0,
    completed_agents INTEGER NOT NULL DEFAULT 0,
    failed_agents    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS swarm_agents (
    id               TEXT PRIMARY KEY,
    swarm_id         TEXT NOT NULL REFERENCES swarms(id),
    task_url         TEXT NOT NULL DEFAULT '',
    task_instruction TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'queued',
    current_action   TEXT NOT NULL DEFAULT '',
    session_id       TEXT NOT NULL DEFAULT '',
    live_view_url    TEXT NOT NULL DEFAULT '',
    result           TEXT,
    error_msg        TEXT,
    actions_taken    INTEGER NOT NULL DEFAULT 0,
    started_at       TEXT,
    finished_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_swarm_agents_swarm ON swarm_agents(swarm_id);

CREATE TABLE IF NOT EXISTS kv_store (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_demos (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL DEFAULT '',
    instruction_summary TEXT NOT NULL DEFAULT '',
    raw_events          TEXT,
    session_id          TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_demos_created ON task_demos(created_at);
"""

# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DATABASE_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.executescript(_SCHEMA)
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        await _db.commit()
        # Run migrations for new columns
        await _ensure_columns()
    return _db


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


# ---------------------------------------------------------------------------
# Run CRUD
# ---------------------------------------------------------------------------

async def create_run() -> dict:
    db = await get_db()
    run_id = _uuid()
    now = _now()
    await db.execute(
        "INSERT INTO runs (id, status, created_at) VALUES (?, ?, ?)",
        (run_id, "queued", now),
    )
    await db.commit()
    return {"id": run_id, "status": "queued", "created_at": now}


async def get_run(run_id: str) -> dict | None:
    db = await get_db()
    cur = await db.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def list_runs() -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM runs ORDER BY created_at DESC")
    return [dict(r) for r in await cur.fetchall()]


async def update_run(run_id: str, **fields) -> None:
    db = await get_db()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [run_id]
    await db.execute(f"UPDATE runs SET {sets} WHERE id = ?", vals)
    await db.commit()


async def increment_run_counter(run_id: str, column: str, amount: int = 1) -> None:
    db = await get_db()
    await db.execute(
        f"UPDATE runs SET {column} = {column} + ? WHERE id = ?",
        (amount, run_id),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Job CRUD
# ---------------------------------------------------------------------------

async def create_job(run_id: str, thread_id: str, subject: str = "") -> dict:
    db = await get_db()
    job_id = _uuid()
    # For Gmail jobs, idempotency is per run+thread (one job per email thread).
    # For non-Gmail jobs (thread_id is empty), each call creates a unique job.
    idemp = f"{run_id}:{thread_id}" if thread_id else f"{run_id}:{job_id}"
    try:
        await db.execute(
            """INSERT INTO jobs (id, run_id, thread_id, subject, idempotency_key)
               VALUES (?, ?, ?, ?, ?)""",
            (job_id, run_id, thread_id, subject, idemp),
        )
        await db.commit()
    except aiosqlite.IntegrityError:
        # Duplicate — already exists for this run+thread
        cur = await db.execute(
            "SELECT * FROM jobs WHERE idempotency_key = ?", (idemp,)
        )
        row = await cur.fetchone()
        return dict(row) if row else {"id": job_id, "status": "skipped"}
    return {
        "id": job_id,
        "run_id": run_id,
        "thread_id": thread_id,
        "subject": subject,
        "status": "queued",
    }


async def get_job(job_id: str) -> dict | None:
    db = await get_db()
    cur = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def list_jobs(run_id: str) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM jobs WHERE run_id = ? ORDER BY rowid", (run_id,)
    )
    return [dict(r) for r in await cur.fetchall()]


async def update_job(job_id: str, **fields) -> None:
    db = await get_db()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [job_id]
    await db.execute(f"UPDATE jobs SET {sets} WHERE id = ?", vals)
    await db.commit()


# ---------------------------------------------------------------------------
# Step CRUD
# ---------------------------------------------------------------------------

async def create_step(job_id: str, name: str) -> dict:
    db = await get_db()
    step_id = _uuid()
    await db.execute(
        "INSERT INTO steps (id, job_id, name) VALUES (?, ?, ?)",
        (step_id, job_id, name),
    )
    await db.commit()
    return {"id": step_id, "job_id": job_id, "name": name, "status": "pending"}


async def update_step(step_id: str, **fields) -> None:
    db = await get_db()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [step_id]
    await db.execute(f"UPDATE steps SET {sets} WHERE id = ?", vals)
    await db.commit()


async def list_steps(job_id: str) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM steps WHERE job_id = ? ORDER BY rowid", (job_id,)
    )
    return [dict(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# OAuth token storage
# ---------------------------------------------------------------------------

async def save_oauth_token(token_data: dict) -> None:
    db = await get_db()
    await db.execute(
        """INSERT OR REPLACE INTO oauth_tokens
           (id, access_token, refresh_token, token_uri, client_id, client_secret, expiry, email)
           VALUES ('default', ?, ?, ?, ?, ?, ?, ?)""",
        (
            token_data.get("access_token", ""),
            token_data.get("refresh_token", ""),
            token_data.get("token_uri", ""),
            token_data.get("client_id", ""),
            token_data.get("client_secret", ""),
            token_data.get("expiry", ""),
            token_data.get("email", ""),
        ),
    )
    await db.commit()


async def get_oauth_token() -> dict | None:
    db = await get_db()
    cur = await db.execute("SELECT * FROM oauth_tokens WHERE id = 'default'")
    row = await cur.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Question CRUD
# ---------------------------------------------------------------------------

async def create_question(job_id: str, run_id: str, question: str, context: str = "") -> dict:
    db = await get_db()
    qid = _uuid()
    now = _now()
    await db.execute(
        """INSERT INTO questions (id, job_id, run_id, question, context, status, created_at)
           VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
        (qid, job_id, run_id, question, context, now),
    )
    await db.commit()
    return {
        "id": qid, "job_id": job_id, "run_id": run_id,
        "question": question, "context": context,
        "answer": None, "status": "pending", "created_at": now,
    }


async def answer_question(question_id: str, answer: str) -> dict | None:
    db = await get_db()
    now = _now()
    await db.execute(
        "UPDATE questions SET answer = ?, status = 'answered', answered_at = ? WHERE id = ?",
        (answer, now, question_id),
    )
    await db.commit()
    cur = await db.execute("SELECT * FROM questions WHERE id = ?", (question_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def get_pending_question(job_id: str) -> dict | None:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM questions WHERE job_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
        (job_id,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def list_questions(run_id: str) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM questions WHERE run_id = ? ORDER BY created_at", (run_id,)
    )
    return [dict(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# User Profile CRUD
# ---------------------------------------------------------------------------

async def get_user_profile(profile_id: str = "default") -> dict | None:
    d = await get_db()
    cur = await d.execute("SELECT * FROM user_profiles WHERE id = ?", (profile_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def save_user_profile(
    profile_id: str = "default",
    email: str = "",
    style_profile: str = "{}",
    preferences: str = "{}",
) -> dict:
    d = await get_db()
    now = _now()
    await d.execute(
        """INSERT OR REPLACE INTO user_profiles
           (id, email, style_profile, preferences, edit_diffs, created_at, updated_at)
           VALUES (?, ?, ?, ?,
                   COALESCE((SELECT edit_diffs FROM user_profiles WHERE id = ?), '[]'),
                   COALESCE((SELECT created_at FROM user_profiles WHERE id = ?), ?),
                   ?)""",
        (profile_id, email, style_profile, preferences, profile_id, profile_id, now, now),
    )
    await d.commit()
    return {
        "id": profile_id,
        "email": email,
        "style_profile": style_profile,
        "preferences": preferences,
        "updated_at": now,
    }


async def update_user_profile(profile_id: str = "default", **fields) -> None:
    d = await get_db()
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [profile_id]
    await d.execute(f"UPDATE user_profiles SET {sets} WHERE id = ?", vals)
    await d.commit()


async def append_edit_diff(profile_id: str, diff: dict) -> None:
    """Append an edit diff to the user profile for ongoing learning."""
    import json
    d = await get_db()
    cur = await d.execute("SELECT edit_diffs FROM user_profiles WHERE id = ?", (profile_id,))
    row = await cur.fetchone()
    if row:
        existing = json.loads(row["edit_diffs"] or "[]")
    else:
        existing = []
    existing.append(diff)
    # Keep last 50 diffs
    existing = existing[-50:]
    await d.execute(
        "UPDATE user_profiles SET edit_diffs = ?, updated_at = ? WHERE id = ?",
        (json.dumps(existing), _now(), profile_id),
    )
    await d.commit()


# ---------------------------------------------------------------------------
# Schema migration helpers (add columns if missing)
# ---------------------------------------------------------------------------

async def _ensure_columns():
    """Add new columns to existing tables if they don't exist."""
    d = await get_db()
    _migrations = [
        "ALTER TABLE jobs ADD COLUMN draft_text TEXT DEFAULT ''",
        "ALTER TABLE jobs ADD COLUMN pipeline_type TEXT DEFAULT 'gmail'",
        "ALTER TABLE jobs ADD COLUMN task_instruction TEXT DEFAULT ''",
        "ALTER TABLE jobs ADD COLUMN live_view_url TEXT DEFAULT ''",
        "ALTER TABLE jobs ADD COLUMN artifacts TEXT DEFAULT '[]'",
    ]
    for sql in _migrations:
        try:
            await d.execute(sql)
            await d.commit()
        except Exception:
            pass  # Column already exists


# ---------------------------------------------------------------------------
# Swarm CRUD
# ---------------------------------------------------------------------------

async def create_swarm() -> dict:
    d = await get_db()
    swarm_id = _uuid()
    now = _now()
    await d.execute(
        "INSERT INTO swarms (id, status, created_at) VALUES (?, ?, ?)",
        (swarm_id, "queued", now),
    )
    await d.commit()
    return {"id": swarm_id, "status": "queued", "created_at": now,
            "total_agents": 0, "completed_agents": 0, "failed_agents": 0}


async def get_swarm(swarm_id: str) -> dict | None:
    d = await get_db()
    cur = await d.execute("SELECT * FROM swarms WHERE id = ?", (swarm_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def list_swarms() -> list[dict]:
    d = await get_db()
    cur = await d.execute("SELECT * FROM swarms ORDER BY created_at DESC")
    return [dict(r) for r in await cur.fetchall()]


async def update_swarm(swarm_id: str, **fields) -> None:
    d = await get_db()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [swarm_id]
    await d.execute(f"UPDATE swarms SET {sets} WHERE id = ?", vals)
    await d.commit()


async def increment_swarm_counter(swarm_id: str, column: str, amount: int = 1) -> None:
    d = await get_db()
    await d.execute(
        f"UPDATE swarms SET {column} = {column} + ? WHERE id = ?",
        (amount, swarm_id),
    )
    await d.commit()


# ---------------------------------------------------------------------------
# Swarm Agent CRUD
# ---------------------------------------------------------------------------

async def create_swarm_agent(swarm_id: str, task_url: str, task_instruction: str) -> dict:
    d = await get_db()
    agent_id = _uuid()
    await d.execute(
        """INSERT INTO swarm_agents (id, swarm_id, task_url, task_instruction)
           VALUES (?, ?, ?, ?)""",
        (agent_id, swarm_id, task_url, task_instruction),
    )
    await d.commit()
    return {
        "id": agent_id, "swarm_id": swarm_id,
        "task_url": task_url, "task_instruction": task_instruction,
        "status": "queued",
    }


async def get_swarm_agent(agent_id: str) -> dict | None:
    d = await get_db()
    cur = await d.execute("SELECT * FROM swarm_agents WHERE id = ?", (agent_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def list_swarm_agents(swarm_id: str) -> list[dict]:
    d = await get_db()
    cur = await d.execute(
        "SELECT * FROM swarm_agents WHERE swarm_id = ? ORDER BY rowid", (swarm_id,)
    )
    return [dict(r) for r in await cur.fetchall()]


async def update_swarm_agent(agent_id: str, **fields) -> None:
    d = await get_db()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [agent_id]
    await d.execute(f"UPDATE swarm_agents SET {sets} WHERE id = ?", vals)
    await d.commit()


# ---------------------------------------------------------------------------
# Key-Value Store (for Browserbase context ID, etc.)
# ---------------------------------------------------------------------------

async def kv_get(key: str) -> str | None:
    d = await get_db()
    cur = await d.execute("SELECT value FROM kv_store WHERE key = ?", (key,))
    row = await cur.fetchone()
    return row["value"] if row else None


async def kv_set(key: str, value: str) -> None:
    d = await get_db()
    await d.execute(
        "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
        (key, value),
    )
    await d.commit()


async def kv_delete(key: str) -> None:
    d = await get_db()
    await d.execute("DELETE FROM kv_store WHERE key = ?", (key,))
    await d.commit()


# ---------------------------------------------------------------------------
# Task demos (recorded user flows, synthesized for agent context)
# ---------------------------------------------------------------------------

async def create_task_demo(
    name: str,
    instruction_summary: str,
    session_id: str,
    raw_events: str | None = None,
) -> dict:
    d = await get_db()
    demo_id = _uuid()
    now = _now()
    await d.execute(
        """INSERT INTO task_demos (id, name, instruction_summary, raw_events, session_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (demo_id, name or "Untitled demo", instruction_summary, raw_events or "", session_id, now),
    )
    await d.commit()
    return {
        "id": demo_id,
        "name": name or "Untitled demo",
        "instruction_summary": instruction_summary,
        "session_id": session_id,
        "created_at": now,
    }


async def get_task_demo(demo_id: str) -> dict | None:
    d = await get_db()
    cur = await d.execute("SELECT * FROM task_demos WHERE id = ?", (demo_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def list_task_demos() -> list[dict]:
    d = await get_db()
    cur = await d.execute("SELECT * FROM task_demos ORDER BY created_at DESC")
    return [dict(r) for r in await cur.fetchall()]


async def delete_task_demo(demo_id: str) -> None:
    d = await get_db()
    await d.execute("DELETE FROM task_demos WHERE id = ?", (demo_id,))
    await d.commit()
