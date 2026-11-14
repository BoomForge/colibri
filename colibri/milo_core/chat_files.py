"""Deterministic, opt-in local filename browsing from the Milo chat.

Only listings/metadata are read; never execute files, traverse symlinks,
read file contents, or let an LLM invent directory listings. A direct request
for Downloads/Documents/Desktop grants one read-only listing of that folder.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Iterable

_ALIASES = {
    "downloads": "Downloads",
    "documents": "Documents",
    "desktop": "Desktop",
    "pictures": "Pictures",
    "videos": "Videos",
    "music": "Music",
}
_ACTION = re.compile(r"\b(list|show|browse|search|find|check|look|what(?:'s| is| are)|tell)\b", re.I)
_OBJECT = re.compile(r"\b(files?|folders?|directory|downloads|documents|desktop|pictures|videos|music)\b", re.I)
_CONTENT = re.compile(r"\b(?:inside|content|read|open)\s+(?:of\s+)?(?:the\s+)?(?:files?|documents?|pdfs?)\b", re.I)
_EXTENSION = re.compile(r"\b(?:pdf|png|jpg|jpeg|webp|txt|md|json|py|zip|mp4|docx?|xlsx?|gguf)s?\b", re.I)
_FILECOUNT_LIMIT = 12000
_OUTPUT_LIMIT = 70


@dataclass(frozen=True)
class FileQuery:
    folder: str | None
    keyword: str = ""
    extension: str = ""
    recursive: bool = False


def parse_file_query(prompt: str) -> FileQuery | None:
    """Opt in only on an explicit action + file-related object."""
    if not isinstance(prompt, str) or len(prompt) > 2000:
        return None
    normal = prompt.casefold()
    if not _ACTION.search(normal) or not _OBJECT.search(normal):
        return None
    # Reading whole documents is a future capability, not a metadata listing.
    if _CONTENT.search(normal):
        return None
    folder = next((name for name in _ALIASES if
                   re.search(r"\b" + name + r"\b", normal)), None)
    if folder is None and not re.search(r"\b(my files|file system|filesystem|computer files)\b", normal):
        # Ambiguous requests for a particular filename without a folder still
        # deserve a clarification rather than scanning an entire hard disk.
        if re.search(r"\b(files?|folders?|directory)\b", normal):
            return FileQuery(folder=None)
        return None
    ext = ""
    matched = re.search(r"\*\.(\w{1,8})\b", normal)
    if matched:
        ext = "." + matched.group(1)
    else:
        match = _EXTENSION.search(normal)
        if match:
            ext = "." + match.group(0).rstrip("s")
    keyword = ""
    named = re.search(r"\b(?:named|called|containing|matches?)\s+[\"']?([-\w. ]{2,60})", prompt, re.I)
    if named:
        keyword = named.group(1).strip(" .\"'").split(" please")[0][:60]
    recursive = bool(re.search(r"\b(subfolders?|subdirectories|recursiv\w*|all levels)\b", normal))
    return FileQuery(folder=folder, keyword=keyword, extension=ext, recursive=recursive)


def known_folder(name: str) -> Path:
    """Resolve standard folders, including Windows Downloads shell redirect."""
    if name not in _ALIASES:
        raise ValueError("unknown standard folder")
    default = Path.home() / _ALIASES[name]
    if os.name == "nt":
        try:
            import winreg
            key_name = (r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders")
            names = {"downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
                     "documents": "Personal", "desktop": "Desktop",
                     "pictures": "My Pictures", "music": "My Music",
                     "videos": "My Video"}
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name) as key:
                raw, _kind = winreg.QueryValueEx(key, names[name])
            if isinstance(raw, str) and raw:
                default = Path(os.path.expandvars(raw)).expanduser()
        except (OSError, ImportError):
            pass
    if not default.is_dir() or default.is_symlink():
        raise FileNotFoundError(
            f"{_ALIASES[name]} folder was not found at {default}. "
            "Check its location or use Setup & modules → Find files.")
    return default


def list_folder(root: Path, query: FileQuery, *, max_items: int = _OUTPUT_LIMIT) -> tuple[str, int]:
    """Bounded, non-following directory enumeration; names only."""
    if not 1 <= max_items <= 100:
        raise ValueError("invalid result count")
    if not root.is_dir() or root.is_symlink():
        raise ValueError("search folder unavailable")
    items: list[tuple[str, bool]] = []
    inspected = 0
    too_many = False
    pending: list[tuple[Path,int]] = [(root,0)]
    while pending:
        directory, depth = pending.pop()
        try:
            children = list(directory.iterdir())
        except (OSError, PermissionError):
            # Some subfolders may be inaccessible. Do not expose errors or crash.
            if directory == root:
                raise
            continue
        for child in children:
            if child.is_symlink():
                continue
            inspected += 1
            if inspected > _FILECOUNT_LIMIT:
                too_many = True
                break
            try:
                is_dir = child.is_dir()
                is_file = child.is_file()
            except (OSError, PermissionError):
                continue
            if not is_dir and not is_file:
                continue
            if is_dir and query.recursive and depth < 5:
                pending.append((child, depth + 1))
            if query.extension and (is_dir or child.suffix.casefold() != query.extension):
                continue
            relative = str(child.relative_to(root))
            if query.keyword and query.keyword.casefold() not in child.name.casefold():
                continue
            items.append((relative, is_dir))
        if too_many:
            break
    items.sort(key=lambda x: (not x[1], x[0].casefold(), x[0]))
    visible = items[:max_items]
    prefix = (f"Local file listing — {_ALIASES[query.folder]} ({root})"
              if query.folder in _ALIASES else f"Local file listing — {root}")
    header = [
        prefix,
        "Read-only filename/metadata search. I have not opened any files.",
        f"Found {len(items)} matching items" +
        (" (partial scan; too many entries)" if too_many else "") + ".",
    ]
    if not visible:
        header.append("No matching files or folders found.")
    else:
        header.append("")
        header += [
            ("[Folder] " if is_dir else "[File] ") + relative
            for relative, is_dir in visible
        ]
    if len(items) > len(visible):
        header.append(f"\nShowing first {len(visible)} results; narrow your search for more.")
    return "\n".join(header), len(items)


def answer_file_query(query: FileQuery) -> str:
    if query.folder is None:
        return ("Which folder would you like me to check? I can list filenames "
                "in Downloads, Documents, Desktop, Pictures, Videos or Music. "
                "For other locations use Setup & modules → Find files. "
                "I only read file and folder names, not their contents.")
    try:
        root = known_folder(query.folder)
        result, _count = list_folder(root, query)
        return result
    except (OSError, ValueError) as exc:
        return ("I couldn't list that folder: " + str(exc)[:220] +
                "\nYou can choose a different location in Setup & modules → Find files.")
