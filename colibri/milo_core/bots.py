"""Private SQLite bot registry and per-conversation inference preferences.

Milo's historical identity is reserved and cannot be replaced by user bot
profiles. Specialist prompts are isolated from Milo's private original archive
and approved memories. The shared room retains explicit speaker attribution.
No bot is installed, downloaded, or granted tools automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3

_SLUG = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")
_AVATAR = re.compile(r"^[a-f0-9]{32}\.(?:png|jpg|jpeg|webp|gif)$")
_MAX_BOTS = 48


@dataclass(frozen=True)
class Bot:
    id: str
    name: str
    instructions: str
    model_location: str
    avatar: str
    active: bool = True


@dataclass(frozen=True)
class ChatSettings:
    chat_id: str
    kind: str = "room"   # room or private
    bot_id: str = "milo"
    model_location: str = ""


def _validate_bot_id(bot_id: str, *, allow_milo: bool = False):
    if not isinstance(bot_id, str) or not _SLUG.fullmatch(bot_id):
        raise ValueError("bot handle must be 2–31 characters, lowercase letters/numbers/_/-")
    if bot_id == "milo" and not allow_milo:
        raise ValueError("@milo is reserved for the main assistant")


def _validate_model_location(value: str):
    if not isinstance(value, str) or len(value) > 360:
        raise ValueError("invalid model location")
    if value:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("model must be a relative checkpoint under Models")


def _validate_bot(bot: Bot):
    _validate_bot_id(bot.id)
    if not isinstance(bot.name, str) or not 1 <= len(bot.name.strip()) <= 48:
        raise ValueError("bot name must contain 1–48 characters")
    if not isinstance(bot.instructions, str) or not 1 <= len(bot.instructions.strip()) <= 8000:
        raise ValueError("specialist instructions must contain 1–8000 characters")
    _validate_model_location(bot.model_location)
    if bot.avatar and (not isinstance(bot.avatar, str) or not _AVATAR.fullmatch(bot.avatar)):
        raise ValueError("bot avatar must be a Milo-managed image filename")


def init_bot_schema(db: sqlite3.Connection):
    """Additive migration, leaves existing chats, notes and Milo persona alone."""
    with db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS bot_profiles("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, instructions TEXT NOT NULL, "
            "model_location TEXT NOT NULL DEFAULT '', "
            "avatar TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS chat_settings("
            "chat_id TEXT PRIMARY KEY, kind TEXT NOT NULL "
            "CHECK(kind IN ('room','private')), bot_id TEXT NOT NULL DEFAULT 'milo', "
            "model_location TEXT NOT NULL DEFAULT '')"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS assistant_provenance("
            "message_id INTEGER PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE, "
            "bot_id TEXT NOT NULL, bot_name TEXT NOT NULL, avatar TEXT NOT NULL DEFAULT '')"
        )


def save_bot(db: sqlite3.Connection, bot: Bot):
    _validate_bot(bot)
    init_bot_schema(db)
    count = db.execute("SELECT count(*) FROM bot_profiles").fetchone()[0]
    exists = db.execute("SELECT 1 FROM bot_profiles WHERE id=?", (bot.id,)).fetchone()
    if not exists and count >= _MAX_BOTS:
        raise ValueError("maximum custom bot count reached")
    timestamp = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            "INSERT INTO bot_profiles(id,name,instructions,model_location,avatar,active,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name,"
            "instructions=excluded.instructions, model_location=excluded.model_location,"
            "avatar=excluded.avatar, active=excluded.active,"
            "updated_at=excluded.updated_at",
            (bot.id, bot.name.strip(), bot.instructions.strip(),
             bot.model_location, bot.avatar, int(bot.active), timestamp, timestamp)
        )


def get_bot(db: sqlite3.Connection, bot_id: str) -> Bot | None:
    _validate_bot_id(bot_id, allow_milo=True)
    if bot_id == "milo":
        return None
    init_bot_schema(db)
    row = db.execute("SELECT id,name,instructions,model_location,avatar,active "
                     "FROM bot_profiles WHERE id=?", (bot_id,)).fetchone()
    return Bot(*row[:5], bool(row[5])) if row else None


def list_bots(db: sqlite3.Connection, *, include_disabled: bool = False) -> list[Bot]:
    init_bot_schema(db)
    sql = ("SELECT id,name,instructions,model_location,avatar,active FROM bot_profiles "
           + ("" if include_disabled else "WHERE active=1 ")
           + "ORDER BY name COLLATE NOCASE")
    return [Bot(*row[:5], bool(row[5])) for row in db.execute(sql).fetchall()]


def set_bot_active(db: sqlite3.Connection, bot_id: str, active: bool):
    _validate_bot_id(bot_id)
    init_bot_schema(db)
    with db:
        result = db.execute(
            "UPDATE bot_profiles SET active=?,updated_at=? WHERE id=?",
            (int(active), datetime.now(timezone.utc).isoformat(), bot_id)
        )
    if not result.rowcount:
        raise ValueError("unknown bot")


def chat_settings(db: sqlite3.Connection, chat_id: str) -> ChatSettings:
    if not isinstance(chat_id, str) or not 1 <= len(chat_id) <= 128:
        raise ValueError("invalid chat ID")
    init_bot_schema(db)
    row = db.execute(
        "SELECT chat_id,kind,bot_id,model_location FROM chat_settings WHERE chat_id=?",
        (chat_id,)).fetchone()
    return ChatSettings(*row) if row else ChatSettings(chat_id)


def set_chat_settings(db: sqlite3.Connection, settings: ChatSettings):
    if not isinstance(settings.chat_id, str) or not 1 <= len(settings.chat_id) <= 128:
        raise ValueError("invalid chat ID")
    if settings.kind not in {"room", "private"}:
        raise ValueError("unknown chat kind")
    _validate_bot_id(settings.bot_id, allow_milo=True)
    _validate_model_location(settings.model_location)
    if settings.kind == "room" and settings.bot_id != "milo":
        raise ValueError("main room must be owned by Milo")
    if settings.kind == "private" and settings.bot_id != "milo":
        bot = get_bot(db, settings.bot_id)
        if bot is None or not bot.active:
            raise ValueError("private chat bot unavailable")
    init_bot_schema(db)
    with db:
        db.execute(
            "INSERT INTO chat_settings(chat_id,kind,bot_id,model_location) VALUES(?,?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET kind=excluded.kind,"
            "bot_id=excluded.bot_id,model_location=excluded.model_location",
            (settings.chat_id, settings.kind, settings.bot_id, settings.model_location)
        )


_MENTION = re.compile(r"^\s*@([a-z][a-z0-9_-]{1,30})(?=\s|[:,!?]|$)", re.I)


def resolve_target(db: sqlite3.Connection, prompt: str,
                   settings: ChatSettings) -> tuple[str, str]:
    """Return (bot_id, clean_prompt). Unknown @handles are not sent to Milo."""
    if settings.kind == "private":
        if settings.bot_id != "milo":
            bot = get_bot(db, settings.bot_id)
            if bot is None or not bot.active:
                raise ValueError("This bot was disabled; reopen or edit it to resume.")
        return settings.bot_id, prompt
    match = _MENTION.match(prompt)
    if not match:
        return "milo", prompt
    handle = match.group(1).lower()
    if handle == "milo":
        return "milo", prompt[match.end():].lstrip(" \t,:")
    bot = get_bot(db, handle)
    if bot is None or not bot.active:
        raise ValueError(
            f"@{handle} does not match an enabled bot. Add or enable it in the right panel.")
    remainder = prompt[match.end():].lstrip(" \t,:")
    if not remainder.strip():
        raise ValueError("Add a message after the @bot handle")
    return handle, remainder


def model_for_turn(settings: ChatSettings, bot: Bot | None,
                   default_model: str) -> str:
    """An explicit bot preference wins, then chat choice, then Milo default."""
    return (bot.model_location if bot and bot.model_location
            else settings.model_location or default_model)


def append_attributed_turn(db: sqlite3.Connection, chat_id: str,
                           user_text: str, answer: str, *,
                           bot_id: str, bot_name: str, avatar: str = ""):
    """Add both messages atomically and bind assistant ID to its named bot."""
    from .store import _utc_now, _validate_text
    if not isinstance(chat_id, str) or not 1 <= len(chat_id) <= 128:
        raise ValueError("invalid chat id")
    _validate_text(user_text)
    _validate_text(answer)
    _validate_bot_id(bot_id, allow_milo=True)
    if not 1 <= len(bot_name) <= 48:
        raise ValueError("invalid author name")
    if avatar and not _AVATAR.fullmatch(avatar):
        raise ValueError("invalid avatar")
    init_bot_schema(db)
    with db:
        db.execute(
            "INSERT INTO conversations(chat_id,role,content,created_at) "
            "VALUES(?,'user',?,?)", (chat_id, user_text, _utc_now())
        )
        cursor = db.execute(
            "INSERT INTO conversations(chat_id,role,content,created_at) "
            "VALUES(?,'assistant',?,?)", (chat_id, answer, _utc_now())
        )
        db.execute(
            "INSERT INTO assistant_provenance(message_id,bot_id,bot_name,avatar)"
            "VALUES(?,?,?,?)", (cursor.lastrowid, bot_id, bot_name, avatar)
        )


def attributed_messages(db: sqlite3.Connection, chat_id: str, *,
                        limit: int = 1000):
    if not isinstance(chat_id, str) or not 1 <= len(chat_id) <= 128:
        raise ValueError("invalid chat ID")
    if not 1 <= limit <= 5000:
        raise ValueError("history limit outside range")
    init_bot_schema(db)
    return db.execute(
        "SELECT c.role,c.content,c.created_at,COALESCE(p.bot_id,'milo'),"
        "COALESCE(p.bot_name,'Milo'),COALESCE(p.avatar,'') "
        "FROM conversations c LEFT JOIN assistant_provenance p ON c.id=p.message_id "
        "WHERE c.chat_id=? ORDER BY c.id DESC LIMIT ?",
        (chat_id, limit)
    ).fetchall()[::-1]


def recent_for_bot(db: sqlite3.Connection, chat_id: str, *, limit: int = 12):
    """In room chats, annotate which assistant said each historical reply."""
    if not 0 <= limit <= 40:
        raise ValueError("history limit outside range")
    if not limit:
        return []
    return [
        {"role": role,
         "content": (f"[Reply by @{author_id} ({author_name})]\n{content}"
                     if role == "assistant" and author_id != "milo" else content)}
        for role, content, _date, author_id, author_name, _avatar
        in attributed_messages(db, chat_id, limit=limit)
    ]
