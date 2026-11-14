"""Explicit local SQLite conversation transcripts and user-approved memory notes.

There is no Kindroid export import, embedding process, background write, or
automatic promotion from chat messages to trusted personal memory.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3


@dataclass(frozen=True)
class MemoryNote:
    id: int
    text: str
    source_kind: str
    source_ref: str
    created_at: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_text(text: str, *, max_chars: int = 20000) -> None:
    if not isinstance(text, str) or not text.strip() or len(text) > max_chars:
        raise ValueError(f"text must be 1 to {max_chars} characters")


class LocalMemoryStore:
    """One local Milo store; no import path to other persona databases."""

    def __init__(self, path: str | Path) -> None:
        target = Path(path).expanduser()
        if target.is_symlink() or target.parent.is_symlink():
            raise ValueError("memory path and parent must not be symlinks")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            raise ValueError("memory database must not be a symlink")
        self.path = target
        self.db = sqlite3.connect(target)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA trusted_schema=OFF")
        with self.db:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS conversations("
                "id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL "
                "CHECK(role IN ('user','assistant')), content TEXT NOT NULL, "
                "created_at TEXT NOT NULL)"
            )
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS conversations_chat_order "
                "ON conversations(chat_id, id)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS approved_notes("
                "id INTEGER PRIMARY KEY, text TEXT NOT NULL, source_kind TEXT NOT NULL, "
                "source_ref TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS approved_note_edits("
                "id INTEGER PRIMARY KEY, "
                "note_id INTEGER NOT NULL REFERENCES approved_notes(id) ON DELETE CASCADE, "
                "previous_text TEXT NOT NULL, "
                "previous_source_kind TEXT NOT NULL, "
                "previous_source_ref TEXT NOT NULL, "
                "edited_at TEXT NOT NULL)"
            )

    def append_turn(self, chat_id: str, user_text: str, assistant_text: str) -> None:
        _validate_text(chat_id, max_chars=128)
        _validate_text(user_text)
        _validate_text(assistant_text)
        with self.db:
            self.db.executemany(
                "INSERT INTO conversations(chat_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                [(chat_id, "user", user_text, _utc_now()),
                 (chat_id, "assistant", assistant_text, _utc_now())],
            )

    def recent(self, chat_id: str, *, limit: int = 12) -> list[dict[str, str]]:
        _validate_text(chat_id, max_chars=128)
        if not 0 <= limit <= 40:
            raise ValueError("history limit must be between 0 and 40")
        if limit == 0:
            return []
        rows = self.db.execute(
            "SELECT role, content FROM conversations WHERE chat_id=? "
            "ORDER BY id DESC LIMIT ?", (chat_id, limit)
        ).fetchall()
        return [{"role": role, "content": text} for role, text in reversed(rows)]

    def add_approved_note(
        self, text: str, *, source_kind: str, source_ref: str,
        approved: bool = False,
    ) -> int:
        if not approved:
            raise PermissionError("memory notes require explicit user approval")
        _validate_text(text, max_chars=4000)
        _validate_text(source_kind, max_chars=64)
        _validate_text(source_ref, max_chars=256)
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO approved_notes(text,source_kind,source_ref,created_at) "
                "VALUES(?,?,?,?)", (text, source_kind, source_ref, _utc_now())
            )
        return int(cursor.lastrowid)

    def find_notes(self, query: str, *, limit: int = 5) -> list[MemoryNote]:
        _validate_text(query, max_chars=256)
        if not 1 <= limit <= 12:
            raise ValueError("limit must be between 1 and 12")
        stopwords = {"about", "what", "where", "when", "with", "have", "remember",
                     "that", "this", "there", "milo", "from", "your", "know"}
        terms = {w.casefold() for w in re.findall(r"\w+", query)
                 if len(w) >= 3 and w.casefold() not in stopwords}
        if not terms:
            return []
        # Cap scanning before adding a proper private search index.
        rows = self.db.execute(
            "SELECT id,text,source_kind,source_ref,created_at "
            "FROM approved_notes ORDER BY id DESC LIMIT 2000"
        ).fetchall()
        ranked = []
        for row in rows:
            found = {w.casefold() for w in re.findall(r"\w+", row[1])}
            score = len(terms & found)
            if score:
                ranked.append((score, row[0], MemoryNote(*row)))
        ranked.sort(key=lambda x: (-x[0], -x[1]))
        return [note for _score, _id, note in ranked[:limit]]

    def revise_note(self, note_id: int, corrected_text: str, *,
                    approved: bool = False) -> bool:
        """Explicit user correction with original source saved as provenance.

        Never silently edit or infer a correction from ordinary conversation.
        """
        if not approved:
            raise PermissionError("correcting permanent memory requires approval")
        _validate_text(corrected_text, max_chars=4000)
        row = self.db.execute(
            "SELECT text,source_kind,source_ref FROM approved_notes WHERE id=?",
            (note_id,)).fetchone()
        if row is None:
            raise ValueError("memory note no longer exists")
        corrected_text = corrected_text.strip()
        if row[0] == corrected_text:
            return False
        with self.db:
            self.db.execute(
                "INSERT INTO approved_note_edits("
                "note_id,previous_text,previous_source_kind,"
                "previous_source_ref,edited_at) VALUES(?,?,?,?,?)",
                (note_id, row[0], row[1], row[2], _utc_now()))
            self.db.execute(
                "UPDATE approved_notes SET text=?, source_kind=? WHERE id=?",
                (corrected_text, "owner_corrected", note_id))
        return True

    def note_provenance(self, note_id: int) -> dict | None:
        row = self.db.execute(
            "SELECT id,text,source_kind,source_ref,created_at "
            "FROM approved_notes WHERE id=?", (note_id,)).fetchone()
        if row is None:
            return None
        first = self.db.execute(
            "SELECT previous_source_kind,previous_source_ref,edited_at "
            "FROM approved_note_edits WHERE note_id=? ORDER BY id ASC LIMIT 1",
            (note_id,)).fetchone()
        edits = self.db.execute(
            "SELECT COUNT(*) FROM approved_note_edits WHERE note_id=?",
            (note_id,)).fetchone()[0]
        return {
            "id": row[0], "text": row[1], "source_kind": row[2],
            "source_ref": row[3], "created_at": row[4],
            "original_source_kind": first[0] if first else row[2],
            "original_source_ref": first[1] if first else row[3],
            "first_corrected_at": first[2] if first else None,
            "revision_count": edits,
        }

    def delete_note(self, note_id: int) -> bool:
        with self.db:
            cursor = self.db.execute("DELETE FROM approved_notes WHERE id=?", (note_id,))
        return cursor.rowcount > 0

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "LocalMemoryStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
