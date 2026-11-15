"""Milo Persistent Life schema v1 (PL-03).

**Dormant / source-only.** No import, app startup, or scheduler calls this
module. A future owner-reviewed integration must verify the private backup and
staging-restore gate before calling initialize_life_schema.

The schema lives inside the existing Milo SQLite database. It makes NO edits
to conversations, historical archives, approved notes, bot profiles, model
paths, or other existing tables. Reflections are NEVER approved memories.
"""
from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

LIFE_SCHEMA_VERSION = 1

# These are deliberately distinct from Milo's existing conversation/history
# tables. All tables are created as one savepoint-protected transaction.
_TABLE_SQL = (
    """CREATE TABLE life_schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at_utc TEXT NOT NULL
    )""",
    """CREATE TABLE life_preferences (
        singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
        autonomy_mode TEXT NOT NULL DEFAULT 'off'
          CHECK (autonomy_mode IN ('off', 'quiet', 'active')),
        background_enabled INTEGER NOT NULL DEFAULT 0
          CHECK (background_enabled IN (0, 1)),
        user_confirmed_at_utc TEXT,
        updated_at_utc TEXT NOT NULL
    )""",
    """CREATE TABLE life_goals (
        goal_id TEXT PRIMARY KEY NOT NULL,
        title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 240),
        state TEXT NOT NULL DEFAULT 'draft'
          CHECK (state IN ('draft', 'approved', 'paused', 'completed', 'cancelled')),
        created_by TEXT NOT NULL CHECK (created_by = 'owner_ui'),
        scope_json TEXT NOT NULL DEFAULT '[]',
        owner_approved_at_utc TEXT,
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE
    )""",
    """CREATE TABLE life_task_runs (
        run_id TEXT PRIMARY KEY NOT NULL,
        goal_id TEXT NOT NULL REFERENCES life_goals(goal_id) ON DELETE RESTRICT,
        task_kind TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'queued'
          CHECK (state IN ('queued', 'running', 'waiting_approval',
                           'succeeded', 'failed', 'interrupted', 'cancelled')),
        risk_class TEXT NOT NULL,
        origin TEXT NOT NULL CHECK (origin IN ('owner_ui', 'scheduler')),
        attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
        approval_id TEXT,
        created_at_utc TEXT NOT NULL,
        started_at_utc TEXT,
        finished_at_utc TEXT,
        idempotency_key TEXT NOT NULL UNIQUE
    )""",
    """CREATE TABLE life_events (
        event_id TEXT PRIMARY KEY NOT NULL,
        schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version = 1),
        timestamp_utc TEXT NOT NULL,
        goal_id TEXT REFERENCES life_goals(goal_id) ON DELETE RESTRICT,
        task_id TEXT REFERENCES life_task_runs(run_id) ON DELETE RESTRICT,
        kind TEXT NOT NULL CHECK (kind IN (
            'goal_created', 'task_queued', 'task_started',
            'task_waiting_approval', 'approval_granted', 'approval_denied',
            'task_succeeded', 'task_failed', 'task_interrupted',
            'task_cancelled', 'reflection_proposed',
            'notification_queued', 'room_state')),
        actor_id TEXT NOT NULL,
        origin TEXT NOT NULL
          CHECK (origin IN ('owner_ui', 'scheduler', 'untrusted_content')),
        state_before TEXT,
        state_after TEXT,
        evidence_refs_json TEXT NOT NULL DEFAULT '[]',
        approval_id TEXT,
        status TEXT NOT NULL,
        summary TEXT NOT NULL CHECK (length(summary) <= 1000),
        idempotency_key TEXT NOT NULL UNIQUE
    )""",
    """CREATE TABLE life_experiences (
        experience_id TEXT PRIMARY KEY NOT NULL,
        source_event_id TEXT NOT NULL UNIQUE
          REFERENCES life_events(event_id) ON DELETE RESTRICT,
        outcome TEXT NOT NULL
          CHECK (outcome IN ('observed', 'failed', 'interrupted', 'cancelled')),
        summary TEXT NOT NULL CHECK (length(summary) <= 1000),
        created_at_utc TEXT NOT NULL
    )""",
    """CREATE TABLE life_reflections (
        reflection_id TEXT PRIMARY KEY NOT NULL,
        source_event_id TEXT NOT NULL
          REFERENCES life_events(event_id) ON DELETE RESTRICT,
        text TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 4000),
        review_state TEXT NOT NULL DEFAULT 'proposed'
          CHECK (review_state IN ('proposed', 'rejected', 'acknowledged')),
        created_at_utc TEXT NOT NULL,
        reviewed_at_utc TEXT
    )""",
    """CREATE TABLE life_pending_notifications (
        notification_id TEXT PRIMARY KEY NOT NULL,
        source_event_id TEXT NOT NULL
          REFERENCES life_events(event_id) ON DELETE RESTRICT,
        channel TEXT NOT NULL DEFAULT 'in_app' CHECK (channel = 'in_app'),
        state TEXT NOT NULL DEFAULT 'pending'
          CHECK (state IN ('pending', 'read', 'dismissed')),
        dedupe_key TEXT NOT NULL UNIQUE,
        created_at_utc TEXT NOT NULL,
        read_at_utc TEXT
    )""",
    """CREATE TABLE life_consent_audit (
        audit_id TEXT PRIMARY KEY NOT NULL,
        task_id TEXT REFERENCES life_task_runs(run_id) ON DELETE RESTRICT,
        operation_kind TEXT NOT NULL,
        decision TEXT NOT NULL
          CHECK (decision IN ('requested', 'granted', 'denied', 'expired')),
        origin TEXT NOT NULL
          CHECK (origin IN ('owner_ui', 'system')),
        scope_digest TEXT NOT NULL,
        approval_id TEXT,
        created_at_utc TEXT NOT NULL,
        request_id TEXT NOT NULL UNIQUE
    )""",
)

_INDEX_SQL = (
    "CREATE INDEX life_goals_by_state ON life_goals(state, updated_at_utc)",
    "CREATE INDEX life_task_runs_by_goal ON life_task_runs(goal_id, created_at_utc)",
    "CREATE INDEX life_task_runs_by_state ON life_task_runs(state, created_at_utc)",
    "CREATE INDEX life_events_by_time ON life_events(timestamp_utc)",
    "CREATE INDEX life_events_by_task ON life_events(task_id, timestamp_utc)",
    "CREATE INDEX life_reflections_by_event ON life_reflections(source_event_id)",
    "CREATE INDEX life_notifications_by_state ON life_pending_notifications(state, created_at_utc)",
    "CREATE INDEX life_consent_by_task ON life_consent_audit(task_id, created_at_utc)",
)

_EXPECTED_COLUMNS = {
    "life_schema_migrations": {"version", "applied_at_utc"},
    "life_preferences": {"singleton_id", "autonomy_mode", "background_enabled", "user_confirmed_at_utc", "updated_at_utc"},
    "life_goals": {"goal_id", "title", "state", "created_by", "scope_json", "owner_approved_at_utc", "created_at_utc", "updated_at_utc", "idempotency_key"},
    "life_task_runs": {"run_id", "goal_id", "task_kind", "state", "risk_class", "origin", "attempt", "approval_id", "created_at_utc", "started_at_utc", "finished_at_utc", "idempotency_key"},
    "life_events": {"event_id", "schema_version", "timestamp_utc", "goal_id", "task_id", "kind", "actor_id", "origin", "state_before", "state_after", "evidence_refs_json", "approval_id", "status", "summary", "idempotency_key"},
    "life_experiences": {"experience_id", "source_event_id", "outcome", "summary", "created_at_utc"},
    "life_reflections": {"reflection_id", "source_event_id", "text", "review_state", "created_at_utc", "reviewed_at_utc"},
    "life_pending_notifications": {"notification_id", "source_event_id", "channel", "state", "dedupe_key", "created_at_utc", "read_at_utc"},
    "life_consent_audit": {"audit_id", "task_id", "operation_kind", "decision", "origin", "scope_digest", "approval_id", "created_at_utc", "request_id"},
}
_INDEX_NAMES = {sql.split()[2] for sql in _INDEX_SQL}


def _objects(db: sqlite3.Connection) -> dict[str, str]:
    """All reserved objects, including indexes, to detect name collisions."""
    return {
        name: kind for name, kind in db.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE substr(name, 1, 5) = 'life_'"
        )
    }


def life_schema_version(db: sqlite3.Connection) -> int:
    """Return 0 for pristine installs; reject unknown/inconsistent versions."""
    objects = _objects(db)
    if "life_schema_migrations" not in objects:
        if objects:
            raise RuntimeError("existing life_* objects without a migration ledger")
        return 0
    if objects["life_schema_migrations"] != "table":
        raise RuntimeError("life migration ledger has an unexpected object type")
    try:
        versions = [row[0] for row in db.execute(
            "SELECT version FROM life_schema_migrations ORDER BY version"
        )]
    except sqlite3.Error as exc:
        raise RuntimeError("invalid life migration ledger") from exc
    if versions != [LIFE_SCHEMA_VERSION]:
        raise RuntimeError("unsupported or incomplete life schema version")
    return LIFE_SCHEMA_VERSION


def verify_life_schema(db: sqlite3.Connection) -> None:
    """Check all required objects without changing the database."""
    if life_schema_version(db) != LIFE_SCHEMA_VERSION:
        raise RuntimeError("life schema is not installed")
    objects = _objects(db)
    for name, expected in _EXPECTED_COLUMNS.items():
        if objects.get(name) != "table":
            raise RuntimeError("life schema missing table: " + name)
        columns = {row[1] for row in db.execute("PRAGMA table_info(" + name + ")")}
        if columns != expected:
            raise RuntimeError("life schema has unexpected columns: " + name)
    if any(objects.get(name) != "index" for name in _INDEX_NAMES):
        raise RuntimeError("life schema missing a required index")
    row = db.execute(
        "SELECT autonomy_mode, background_enabled, user_confirmed_at_utc "
        "FROM life_preferences WHERE singleton_id = 1"
    ).fetchone()
    if row is None or row[0] not in {"off", "quiet", "active"}:
        raise RuntimeError("life preferences missing or invalid")


def initialize_life_schema(
    db: sqlite3.Connection, *, backup_gate_verified: bool = False
) -> bool:
    """Create additive tables; return True only on first migration.

    A trusted caller must verify a private backup and inactive restore FIRST.
    The explicit gate defaults to False. This function is intentionally not
    wired to desktop startup, scheduler, module activation or installers.
    """
    if not backup_gate_verified:
        raise PermissionError("PL-01 backup and staging restore gate not verified")
    if db.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("enable SQLite foreign keys before life migration")
    existing = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if not {"conversations", "approved_notes"} <= existing:
        raise RuntimeError("expected existing Milo memory tables are missing")
    if life_schema_version(db) == LIFE_SCHEMA_VERSION:
        verify_life_schema(db)
        return False

    # SAVEPOINT works even when the caller has an existing transaction.
    db.execute("SAVEPOINT milo_life_v1")
    try:
        for sql in _TABLE_SQL:
            db.execute(sql)
        for sql in _INDEX_SQL:
            db.execute(sql)
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO life_preferences"
            "(singleton_id, autonomy_mode, background_enabled, updated_at_utc) "
            "VALUES (1, 'off', 0, ?)", (now,)
        )
        db.execute(
            "INSERT INTO life_schema_migrations(version, applied_at_utc) VALUES(?, ?)",
            (LIFE_SCHEMA_VERSION, now)
        )
        verify_life_schema(db)
        db.execute("RELEASE SAVEPOINT milo_life_v1")
    except BaseException:
        db.execute("ROLLBACK TO SAVEPOINT milo_life_v1")
        db.execute("RELEASE SAVEPOINT milo_life_v1")
        raise
    return True
