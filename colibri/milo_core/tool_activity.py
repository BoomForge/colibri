"""Minimal local, source-labelled activity history for real tool operations.

Store only fixed tool labels and outcomes, never prompts, search queries,
document text, filenames, paths, web tokens or screenshots.
"""
from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

TOOLS = frozenset({
    "web_research", "local_file_search", "local_preview",
    "screen_capture", "quick_chat", "windows_settings", "vision_inference", "file_organize",
    "pc_launch_app", "pc_open_folder", "pc_open_url", "pc_folder_summary",
    "pc_list_windows", "pc_focus_window", "pc_type_text", "pc_rename_file",
    "pc_create_note", "pc_move_assets", "pc_app_registration",
    "pc_app_search", "pc_launch_discovered_app",
    "drive_memory_connect", "drive_memory_refresh",
    "drive_memory_disconnect", "drive_memory_scope",
})
OUTCOMES = frozenset({
    "approved", "denied", "started", "completed", "failed", "cancelled", "revoked",
})


def init_activity(db: sqlite3.Connection) -> None:
    with db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS tool_activity ("
            "id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL, "
            "tool TEXT NOT NULL, outcome TEXT NOT NULL)"
        )


def record_activity(db: sqlite3.Connection, tool: str, outcome: str) -> None:
    if tool not in TOOLS or outcome not in OUTCOMES:
        raise ValueError("Tool activity must use known safe labels")
    with db:
        db.execute(
            "INSERT INTO tool_activity(occurred_at, tool, outcome) "
            "VALUES(?,?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"),
             tool, outcome))
        # Bounded retention, independent of private conversation/memory data.
        db.execute(
            "DELETE FROM tool_activity WHERE id NOT IN "
            "(SELECT id FROM tool_activity ORDER BY id DESC LIMIT 300)"
        )


def list_activity(db: sqlite3.Connection, limit: int = 80) -> list[tuple]:
    if not isinstance(limit, int) or not 1 <= limit <= 300:
        raise ValueError("Activity limit must be between 1 and 300")
    return db.execute(
        "SELECT occurred_at, tool, outcome FROM tool_activity "
        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def clear_activity(db: sqlite3.Connection) -> None:
    with db:
        db.execute("DELETE FROM tool_activity")
