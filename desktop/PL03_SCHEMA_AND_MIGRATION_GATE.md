# PL-03 — Dormant Persistent Life schema v1

Status: **SOURCE IMPLEMENTED, OWNER BACKUP + INDEPENDENT TESTS PENDING**.
Branch: `feature/milo-persistent-life-pl03-schema-20261116`
Plan: https://docs.google.com/document/d/1Rp4ZiWArCyjpLNE0ey0NaNHVTskGFYKvJlwlUWy54mI/edit
Prior PL-01/PL-02 draft PRs #2/#3 are not merged or accepted. This source-only branch inherits them. There is **no application entrypoint** invoking this migration.

## Purpose

Add versioned, additive persistence for real experiences, goals, task history, reflections, notifications and consent evidence **inside Milo's existing SQLite file** without editing any of the prior chats, Kindroid archive, approved notes, bot profiles, specialist chats, model locations or Drive reference data.

Implementation: `colibri/milo_core/life_store.py`.
Synthetic regression suite: `tests/test_milo_life_schema.py`.

## New tables

- `life_schema_migrations`: separately tracked version v1; no alteration of `PRAGMA user_version`.
- `life_preferences`: one configuration row with `autonomy_mode='off'`, `background_enabled=0`, no owner consent timestamp. Creating schema **never** activates autonomy.
- `life_goals`: owner-created, scoped durable intentions and approval timestamps.
- `life_task_runs`: keyed runs, states, origins, attempts, timestamps and approval references.
- `life_events`: source-labelled event ledger with goal/task IDs, provenance, evidence reference IDs and idempotency keys.
- `life_experiences`: one outcome record per observed source event.
- `life_reflections`: tentative separate notes with `review_state='proposed'`; no path silently inserts into `approved_notes`.
- `life_pending_notifications`: in-app-only records with deduplication.
- `life_consent_audit`: descriptive consent records; not permission tokens or reusable grants.

Secondary indexes cover task/goal state, event chronology, reflection source, notification queue and consent audit. Foreign keys and unique idempotency constraints enforce referential integrity and reject duplicate work.

## Migration entrypoint — NOT AUTOMATIC

`initialize_life_schema(db, backup_gate_verified=True)` creates the schema only if:
1. The trusted app/UI has verified a real owner-private encrypted backup and an independent staging restore from PL-01 (and an owner-approved integration is subsequently reviewed).
2. The SQLite connection has `PRAGMA foreign_keys=ON`.
3. The database already contains Milo's `conversations` and `approved_notes` tables.
4. There is no unknown/malformed existing `life_*` schema.

The gate is an API safety default, **not a complete cryptographic proof of the owner's backup**; a later UI integration must enforce the actual owner confirmation. **No existing app, startup, update service, scheduler or installer calls this function in PL-03.**

The migration uses a single SQLite `SAVEPOINT` so an exception rolls back all its new tables and indexes even inside a caller transaction. Completed migrations validate required columns, indexes and default preferences; repeat calls are idempotent and do not rewrite data. Unknown or partial versions fail closed rather than overwriting data.

## Verification goals and rollback

Synthetic tests assert:
- Calling without the PL-01 backup-gate flag raises `PermissionError` and leaves pre-existing schema untouched.
- Migration preserves existing conversations, approved memory, specialist profile, and synthetic historical source data byte-for-byte at the DB row/schema level.
- Schema persists across DB restart; rerunning a completed migration is a no-op.
- Default autonomy remains Off and no background worker is launched.
- Unique event IDs, unique idempotency keys, enum constraints and foreign keys reject unsupported records.
- Reflections remain separate and cannot silently become permanent approved memory.
- Partial `life_*` collisions are refused; an injected DDL failure restores the original schema atomically.

**Current independent verification:** tests are committed to GitHub, but a GitHub Actions run must be observed before any success claim. Owner-machine migration testing remains gated by an actual backup and restore acceptance.

If a future trial is unsatisfactory, disable the new feature (Off); the new tables are untouched by any legacy chat UI. For pre-release development, discard the synthetic test database, or restore a separately authenticated *owner-approved* backup into a new staging directory. Never perform a destructive `DROP TABLE` rollback on the owner's active Milo database, and never copy staging data over the active profile without a separate reviewed migration.

## What PL-03 does NOT yet implement

No actual events are emitted; no scheduler runs; no goals UI; no specialist delegation; no proactive messages; no companion-room renderer; no runtime permission broker; no UI toggles; no automatic database migration; no new executable/installer. These belong to later tracked stages and cannot be claimed as part of PL-03.

## Planned later integration

Only after PL-01 owner backup and PL-02 permission review:
- introduce an explicit feature activation/migration gate in the native application;
- show the migration scope and validated backup confirmation before touching the active database;
- re-run synthetic and native Windows tests with preserved legacy conversations and model paths;
- verify shutdown and installer-upgrade behaviour; keep secure rollback path;
- implement future `life_events` and `life_goals` adapters under an auditable policy layer (PL-04 onward).

Do not count this stage as VERIFIED COMPLETE unless source CI and owner acceptance, where applicable, have evidence recorded in the canonical Google Doc.
