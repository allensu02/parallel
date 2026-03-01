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
        try:
            await _db.execute("ALTER TABLE jobs ADD COLUMN draft_text TEXT DEFAULT ''")
            await _db.commit()
        except Exception:
            pass
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
    idemp = f"{run_id}:{thread_id}"
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
    try:
        await d.execute("ALTER TABLE jobs ADD COLUMN draft_text TEXT DEFAULT ''")
        await d.commit()
    except Exception:
        pass  # Column already exists
