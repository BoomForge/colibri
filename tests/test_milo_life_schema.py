"""PL-03 synthetic tests for an opt-in, dormant Milo life schema.

No user's actual Milo database, archives, credentials or model files are used.
Successful tests here do not authorise applying migrations to an owner's PC.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from colibri.milo_core.store import LocalMemoryStore
from colibri.milo_core.bots import Bot, init_bot_schema, save_bot
from colibri.milo_core.life_store import (
    LIFE_SCHEMA_VERSION,
    initialize_life_schema,
    life_schema_version,
    verify_life_schema,
)


class LifeSchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = Path(self.tempdir.name) / "synthetic-milo.sqlite3"
        self.store = LocalMemoryStore(self.path)
        self.addCleanup(self.store.close)
        self.db = self.store.db
        self.store.append_turn("main", "hello synthetic Milo", "synthetic reply")
        self.store.add_approved_note(
            "Keep the legacy persona source separate",
            source_kind="synthetic_test",
            source_ref="PL03",
            approved=True,
        )
        init_bot_schema(self.db)
        save_bot(self.db, Bot(
            id="research", name="Sage",
            instructions="Research synthetic test material",
            model_location="", avatar="",
        ))
        self.db.execute(
            "CREATE TABLE synthetic_legacy_archive("
            "source_id TEXT PRIMARY KEY, original_text TEXT NOT NULL)"
        )
        self.db.execute(
            "INSERT INTO synthetic_legacy_archive VALUES('A1', 'private history fixture')"
        )
        self.db.commit()

    def original_snapshot(self):
        tables = ("conversations", "approved_notes", "bot_profiles",
                  "synthetic_legacy_archive")
        return {
            table: (
                self.db.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()[0],
                self.db.execute("SELECT * FROM " + table).fetchall(),
            )
            for table in tables
        }

    def test_explicit_backup_gate_blocks_all_db_writes(self):
        before = self.original_snapshot()
        self.assertEqual(life_schema_version(self.db), 0)
        with self.assertRaises(PermissionError):
            initialize_life_schema(self.db)
        self.assertEqual(life_schema_version(self.db), 0)
        self.assertEqual(before, self.original_snapshot())

    def test_additive_migration_keeps_original_records_and_is_idempotent(self):
        before = self.original_snapshot()
        created = initialize_life_schema(self.db, backup_gate_verified=True)
        self.assertTrue(created)
        self.assertEqual(life_schema_version(self.db), LIFE_SCHEMA_VERSION)
        verify_life_schema(self.db)
        self.assertEqual(self.original_snapshot(), before)
        self.assertEqual(
            self.db.execute(
                "SELECT autonomy_mode, background_enabled, "
                "user_confirmed_at_utc FROM life_preferences WHERE singleton_id=1"
            ).fetchone(),
            ("off", 0, None),
        )
        self.assertFalse(initialize_life_schema(self.db, backup_gate_verified=True))
        self.assertEqual(self.original_snapshot(), before)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM life_schema_migrations"
        ).fetchone()[0], 1)

    def test_new_life_tables_persist_after_database_reopen(self):
        initialize_life_schema(self.db, backup_gate_verified=True)
        self.db.commit()
        with closing(sqlite3.connect(self.path)) as reopened:
            reopened.execute("PRAGMA foreign_keys=ON")
            verify_life_schema(reopened)
            self.assertEqual(reopened.execute(
                "SELECT COUNT(*) FROM conversations"
            ).fetchone()[0], 2)
            self.assertEqual(reopened.execute(
                "SELECT name FROM bot_profiles WHERE id='research'"
            ).fetchone()[0], "Sage")

    def test_event_idempotency_and_foreign_key_enforcement(self):
        initialize_life_schema(self.db, backup_gate_verified=True)
        self.db.execute(
            "INSERT INTO life_goals "
            "(goal_id, title, state, created_by, created_at_utc, updated_at_utc, idempotency_key) "
            "VALUES ('goal-1','Synthetic approved goal','approved','owner_ui','z','z','g:1')"
        )
        self.db.execute(
            "INSERT INTO life_task_runs "
            "(run_id,goal_id,task_kind,risk_class,origin,created_at_utc,idempotency_key) "
            "VALUES ('run-1','goal-1','recall','internal_read','owner_ui','z','r:1')"
        )
        self.db.execute(
            "INSERT INTO life_events "
            "(event_id,timestamp_utc,goal_id,task_id,kind,actor_id,origin,"
            "status,summary,idempotency_key) "
            "VALUES ('event-1','z','goal-1','run-1','task_queued','milo',"
            "'scheduler','queued','Synthetic only','e:1')"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO life_events "
                "(event_id,timestamp_utc,goal_id,task_id,kind,actor_id,origin,"
                "status,summary,idempotency_key) "
                "VALUES ('event-2','z','goal-1','run-1','task_queued','milo',"
                "'scheduler','queued','duplicate','e:1')"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO life_task_runs "
                "(run_id,goal_id,task_kind,risk_class,origin,created_at_utc,idempotency_key) "
                "VALUES ('run-bad','goal-unknown','recall','internal_read','owner_ui','z','r:bad')"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO life_events "
                "(event_id,timestamp_utc,goal_id,task_id,kind,actor_id,origin,"
                "status,summary,idempotency_key) "
                "VALUES ('event-3','z','goal-1','run-1','task_succeeded','milo',"
                "'pretend_owner','success','forged origin','e:3')"
            )
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM life_events"
        ).fetchone()[0], 1)

    def test_reflections_cannot_be_silent_approved_memories(self):
        initialize_life_schema(self.db, backup_gate_verified=True)
        self.db.execute(
            "INSERT INTO life_events "
            "(event_id,timestamp_utc,kind,actor_id,origin,status,summary,idempotency_key) "
            "VALUES ('event-1','z','reflection_proposed','milo','scheduler','pending',"
            "'Synthetic reflection','e:1')"
        )
        self.db.execute(
            "INSERT INTO life_reflections "
            "(reflection_id,source_event_id,text,created_at_utc) "
            "VALUES ('reflect-1','event-1','Maybe a user preference','z')"
        )
        self.assertEqual(self.db.execute(
            "SELECT review_state FROM life_reflections"
        ).fetchone()[0], "proposed")
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM approved_notes"
        ).fetchone()[0], 1)

    def test_partial_or_unknown_life_schema_refused(self):
        self.db.execute("CREATE TABLE life_goals (wrong TEXT)")
        self.db.commit()
        with self.assertRaises(RuntimeError):
            initialize_life_schema(self.db, backup_gate_verified=True)
        self.assertEqual(self.db.execute(
            "SELECT name FROM sqlite_master WHERE name='life_schema_migrations'"
        ).fetchone(), None)

    def test_denied_index_creation_rolls_back_all_new_tables(self):
        before = self.original_snapshot()
        failed = {"seen": 0}
        def authorizer(action, arg1, arg2, dbname, source):
            if action == sqlite3.SQLITE_CREATE_INDEX:
                failed["seen"] += 1
                if failed["seen"] >= 2:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.db.set_authorizer(authorizer)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                initialize_life_schema(self.db, backup_gate_verified=True)
        finally:
            self.db.set_authorizer(None)
        self.assertEqual(life_schema_version(self.db), 0)
        self.assertEqual(self.original_snapshot(), before)
        self.assertTrue(initialize_life_schema(self.db, backup_gate_verified=True))

    def test_foreign_keys_must_be_enforced(self):
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE TABLE conversations(id INTEGER)")
            db.execute("CREATE TABLE approved_notes(id INTEGER)")
            with self.assertRaises(RuntimeError):
                initialize_life_schema(db, backup_gate_verified=True)
            self.assertEqual(life_schema_version(db), 0)


if __name__ == "__main__":
    unittest.main()
