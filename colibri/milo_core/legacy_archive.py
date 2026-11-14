"""Milo original-chat archival import and evidence-backed recall.

Private data stays on the owner's computer. The repository contains only an
importer, never the owner's source messages or personal persona configuration.
Original Kindroid messages are preserved in a separate history namespace,
not blended into newly generated conversations or fictional Afterlight canon.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any
import zipfile


class LegacyArchiveError(ValueError):
    """Imported archive failed validation, integrity, or persona isolation."""


@dataclass(frozen=True)
class LegacyOverview:
    source_sha256: str
    messages: int
    user_messages: int
    milo_messages: int
    first_date: str
    last_date: str
    attachment_references: int
    image_references: int
    has_persona: bool


MAX_ZIP_FILES = 6
MAX_ITEM_BYTES = 24 * 1024 * 1024
MAX_IMPORT_MESSAGES = 50000
MAX_FILE_BYTES = 24 * 1024 * 1024
ALLOWED_ENTRIES = {"chat.json", "persona.json", "manifest.json",
                   "Ghost_Milo_Legacy_Memory.md"}
PERSONA_FORMAT = "milo-conversational-persona"
BUNDLE_FORMAT = "milo-private-continuity-bundle"


def _hash(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _load_bytes(path: str | Path) -> tuple[bytes, dict[str, Any] | None]:
    source = Path(path).expanduser()
    if not source.is_file() or source.is_symlink():
        raise LegacyArchiveError("choose a regular existing backup file")
    if source.stat().st_size > MAX_FILE_BYTES:
        raise LegacyArchiveError("this preview supports backup files up to 24 MiB")
    if source.suffix.lower() == ".json":
        return source.read_bytes(), None
    if source.suffix.lower() != ".zip":
        raise LegacyArchiveError("choose a Milo private continuity ZIP or chat.json")
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            names = [e.filename for e in entries]
            if (len(entries) > MAX_ZIP_FILES or len(names) != len(set(names))
                    or set(names) - ALLOWED_ENTRIES
                    or "chat.json" not in names or "manifest.json" not in names):
                raise LegacyArchiveError("unexpected or incomplete private archive contents")
            for item in entries:
                if (item.file_size > MAX_ITEM_BYTES or item.is_dir()
                        or (item.external_attr >> 16) & 0o170000 == 0o120000):
                    raise LegacyArchiveError("unsupported archive entry")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("schema") != 1 or manifest.get("format") != BUNDLE_FORMAT:
                raise LegacyArchiveError("unrecognised Milo private archive manifest")
            recorded = manifest.get("files", {})
            if not isinstance(recorded, dict) or set(recorded) != set(names) - {"manifest.json"}:
                raise LegacyArchiveError("archive inventory does not match manifest")
            for name, meta in recorded.items():
                blob = archive.read(name)
                if len(blob) != meta.get("size") or _hash(blob) != meta.get("sha256"):
                    raise LegacyArchiveError("archive integrity check failed")
            persona = json.loads(archive.read("persona.json")) if "persona.json" in names else None
            return archive.read("chat.json"), persona
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError,
            KeyError, TypeError, OverflowError) as exc:
        raise LegacyArchiveError("private archive is corrupt or invalid") from exc


def _validate_persona(persona: object) -> dict[str, Any] | None:
    if persona is None:
        return None
    if not isinstance(persona, dict):
        raise LegacyArchiveError("persona must be an object")
    if persona.get("format") != PERSONA_FORMAT or persona.get("schema") != 1:
        raise LegacyArchiveError("unknown persona version")
    if persona.get("name") != "Milo":
        raise LegacyArchiveError("this import is only for conversational Milo")
    instructions = persona.get("instructions")
    notes = persona.get("context_notes")
    if (not isinstance(instructions, str) or not 100 <= len(instructions) <= 12000
            or not isinstance(notes, str) or len(notes) > 5000):
        raise LegacyArchiveError("persona is empty or exceeds bounded prompt limits")
    return persona


def _validated_rows(blob: bytes) -> list[dict[str, Any]]:
    if len(blob) > MAX_ITEM_BYTES:
        raise LegacyArchiveError("message export exceeds supported size")
    try:
        data = json.loads(blob.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LegacyArchiveError("backup chat JSON could not be parsed") from exc
    if not isinstance(data, list) or not data or len(data) > MAX_IMPORT_MESSAGES:
        raise LegacyArchiveError("chat JSON must be a nonempty, bounded message array")
    validated = []
    ids: set[str] = set()
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise LegacyArchiveError("invalid message object")
        msg_id = item.get("id")
        role = item.get("role")
        content = item.get("message")
        stamp = item.get("timestamp")
        original_order = item.get("originalOrder", index)
        if (not isinstance(msg_id, str) or not 1 <= len(msg_id) <= 128
                or msg_id in ids):
            raise LegacyArchiveError("missing or duplicated stable message ID")
        if role not in ("user", "assistant"):
            raise LegacyArchiveError("unknown sender role in private archive")
        if not isinstance(content, str) or len(content) > 30000:
            raise LegacyArchiveError("message text is invalid or too long")
        if (not isinstance(stamp, int) or isinstance(stamp, bool)
                or stamp < 946684800000 or stamp > 4102444800000):
            raise LegacyArchiveError("message timestamp is invalid")
        if (not isinstance(original_order, int) or isinstance(original_order, bool)
                or original_order < 0):
            raise LegacyArchiveError("message sequence is invalid")
        attachments = item.get("attachments", [])
        images = item.get("imageUrls", [])
        if not isinstance(attachments, list) or not isinstance(images, list):
            raise LegacyArchiveError("unexpected attachment metadata shape")
        ids.add(msg_id)
        validated.append({
            "id": msg_id, "role": role, "text": content,
            "timestamp": stamp, "order": original_order,
            "attachments": len(attachments), "images": len(images),
        })
    validated.sort(key=lambda x: (x["timestamp"], x["order"], x["id"]))
    return validated


def _parse_archive(path: str | Path):
    blob, persona = _load_bytes(path)
    rows = _validated_rows(blob)
    persona = _validate_persona(persona)
    return blob, rows, persona


def inspect_legacy_archive(path: str | Path) -> LegacyOverview:
    """Read-only preview; nothing imported, transmitted, or modified."""
    blob, rows, persona = _parse_archive(path)
    first, last = rows[0], rows[-1]
    def date(stamp):
        return datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).date().isoformat()
    return LegacyOverview(
        source_sha256=_hash(blob),
        messages=len(rows),
        user_messages=sum(x["role"] == "user" for x in rows),
        milo_messages=sum(x["role"] == "assistant" for x in rows),
        first_date=date(first["timestamp"]),
        last_date=date(last["timestamp"]),
        attachment_references=sum(x["attachments"] for x in rows),
        image_references=sum(x["images"] for x in rows),
        has_persona=persona is not None,
    )


def _ensure_tables(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS legacy_imports("
        "source_sha TEXT PRIMARY KEY, count INTEGER NOT NULL, "
        "first_stamp INTEGER NOT NULL, last_stamp INTEGER NOT NULL, "
        "imported_at TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS legacy_messages("
        "source_sha TEXT NOT NULL, message_id TEXT NOT NULL, "
        "role TEXT NOT NULL CHECK(role IN ('user','assistant')), "
        "body TEXT NOT NULL, timestamp_ms INTEGER NOT NULL, sequence INTEGER NOT NULL, "
        "attachment_refs INTEGER NOT NULL, image_refs INTEGER NOT NULL, "
        "PRIMARY KEY(source_sha,message_id), "
        "FOREIGN KEY(source_sha) REFERENCES legacy_imports(source_sha))"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS legacy_messages_time "
        "ON legacy_messages(timestamp_ms DESC, sequence DESC)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS local_milo_persona("
        "slot TEXT PRIMARY KEY CHECK(slot='milo'), "
        "source_sha TEXT NOT NULL, profile_json TEXT NOT NULL, "
        "installed_at TEXT NOT NULL)"
    )


def import_legacy_archive(
    db: sqlite3.Connection,
    path: str | Path,
    *,
    user_approved: bool = False,
    replace_profile: bool = False,
) -> LegacyOverview:
    """Transactionally add primary-source messages and optional private persona.

    Same source SHA is idempotent. Existing persona is never changed without an
    explicit separate replace_profile approval. Does not change recent chat.
    """
    if not user_approved:
        raise PermissionError("importing personal history requires owner approval")
    blob, rows, persona = _parse_archive(path)
    overview = inspect_legacy_archive(path)
    source_sha = overview.source_sha256
    when = datetime.now(tz=timezone.utc).isoformat()
    try:
        db.execute("BEGIN IMMEDIATE")
        _ensure_tables(db)
        old = db.execute(
            "SELECT source_sha FROM local_milo_persona WHERE slot='milo'"
        ).fetchone()
        if persona is not None and old and old[0] != source_sha and not replace_profile:
            raise LegacyArchiveError("existing persona differs; explicit replacement required")
        already = db.execute(
            "SELECT count FROM legacy_imports WHERE source_sha=?", (source_sha,)
        ).fetchone()
        if already:
            if already[0] != len(rows):
                raise LegacyArchiveError("existing source import count conflicts")
        else:
            db.execute(
                "INSERT INTO legacy_imports VALUES(?,?,?,?,?)",
                (source_sha, len(rows), rows[0]["timestamp"],
                 rows[-1]["timestamp"], when),
            )
            db.executemany(
                "INSERT INTO legacy_messages VALUES(?,?,?,?,?,?,?,?)",
                [(source_sha, x["id"], x["role"], x["text"], x["timestamp"],
                  x["order"], x["attachments"], x["images"]) for x in rows],
            )
        if persona is not None:
            db.execute(
                "INSERT INTO local_milo_persona(slot,source_sha,profile_json,installed_at) "
                "VALUES('milo',?,?,?) ON CONFLICT(slot) DO UPDATE SET "
                "source_sha=excluded.source_sha, profile_json=excluded.profile_json, "
                "installed_at=excluded.installed_at",
                (source_sha, json.dumps(persona, ensure_ascii=False), when),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return overview


def loaded_persona(db: sqlite3.Connection) -> dict[str, Any] | None:
    try:
        found = db.execute(
            "SELECT profile_json FROM local_milo_persona WHERE slot='milo'"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not found:
        return None
    try:
        return _validate_persona(json.loads(found[0]))
    except (LegacyArchiveError, json.JSONDecodeError):
        return None


_STOP = frozenset({
    "the", "and", "for", "are", "have", "has", "with", "this", "that",
    "what", "when", "which", "where", "will", "would", "could", "should",
    "remember", "milo", "they", "them", "you", "your", "mine", "about",
    "from", "into", "just", "there", "like", "been", "very", "really",
    "were", "was", "can", "how", "why", "our", "some", "any", "did",
})


def recall_legacy(
    db: sqlite3.Connection, query: str, *, limit: int = 3,
) -> list[dict[str, str]]:
    """Lexical evidence lookup. Never treats historical instructions as commands."""
    if not isinstance(query, str) or len(query) > 2000 or not query.strip():
        return []
    if not 1 <= limit <= 5:
        raise ValueError("invalid memory retrieval limit")
    terms = {word.casefold() for word in re.findall(r"\w+", query)
             if len(word) >= 4 and word.casefold() not in _STOP}
    if not terms:
        return []
    try:
        matches = db.execute(
            "SELECT source_sha, role, body, timestamp_ms FROM legacy_messages "
            "WHERE body <> '' ORDER BY timestamp_ms DESC LIMIT 3000"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    ranked = []
    for source, role, body, stamp in matches:
        body_terms = set(w.casefold() for w in re.findall(r"\w+", body))
        overlap = terms & body_terms
        if not overlap:
            continue
        score = 10 * len(overlap) + (12 if overlap == terms else 0)
        # Keep a short primary-source excerpt rather than injecting whole chats.
        lowered = body.casefold()
        starts = [lowered.find(w) for w in overlap]
        first = min(x for x in starts if x >= 0)
        start = max(0, first - 75)
        excerpt = body[start:start + 360].replace("\r", " ").replace("\n", " ")
        if start:
            excerpt = "…" + excerpt
        if start + 360 < len(body):
            excerpt += "…"
        date = datetime.fromtimestamp(stamp / 1000, tz=timezone.utc).date().isoformat()
        ranked.append((score, stamp, {
            "archive": "original Milo chat export",
            "source_sha_prefix": source[:12],
            "date": date,
            "speaker": "earlier Milo" if role == "assistant" else "user",
            "excerpt": excerpt,
        }))
    ranked.sort(key=lambda x: (-x[0], -x[1]))
    return [x[2] for x in ranked[:limit]]
