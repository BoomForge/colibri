"""Explicit, bounded local 'open file' routing for the Milo Windows app.

Search only standard user folders, never the full filesystem. Files must be
regular and not symlinks. Safe text/image previews are rendered inside Milo;
no shell execution, process launch, file writes, or external transmission.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .chat_files import known_folder

IMAGE_TYPES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})
TEXT_TYPES = frozenset({".txt", ".md", ".markdown", ".rst", ".csv", ".json",
                        ".yaml", ".yml", ".py", ".js", ".ts", ".log",
                        ".pdf", ".docx", ".html", ".htm"})
ALLOWED_TYPES = IMAGE_TYPES | TEXT_TYPES
MAX_OPEN_BYTES = 15 * 1024 * 1024
MAX_VISITED = 16000
MAX_DEPTH = 3
_STANDARD_FOLDERS = ("downloads", "desktop", "pictures", "documents")
_FILENAME = re.compile(
    r"""(?:["']([^"'\r\n]{1,180}\.(?:png|jpe?g|webp|gif|bmp|txt|md|markdown|rst|csv|json|yaml|yml|py|js|ts|log|pdf|docx|html?))["']|([A-Za-z0-9_().+ -]{1,180}\.(?:png|jpe?g|webp|gif|bmp|txt|md|markdown|rst|csv|json|yaml|yml|py|js|ts|log|pdf|docx|html?)))""",
    re.I
)
_OPEN_ACTION = re.compile(r"\b(?:open|view|preview|display|show me|look at)\b", re.I)


class OpenFileError(ValueError):
    pass


@dataclass(frozen=True)
class OpenFileRequest:
    name: str
    folder: str | None = None


def parse_open_request(prompt: str) -> OpenFileRequest | None:
    """Don't mistake ordinary conversation, web URLs, or executables for files."""
    if not isinstance(prompt, str) or len(prompt) > 2000:
        return None
    if not _OPEN_ACTION.search(prompt):
        return None
    # URLs are opened by the explicit HTTPS link action, never interpreted
    # as disk filenames extracted from a remote address.
    if re.search(r"\b(?:https?://|www\.)", prompt, re.I):
        return None
    result = _FILENAME.search(prompt)
    if not result:
        return None
    name = (result.group(1) or result.group(2)).strip()
    # Unquoted captures sometimes include 'open ' or 'view '. Strip the verb
    # and surrounding natural-language articles, not filename characters.
    name = re.sub(
        r"^(?:can you |could you |please |would you |just |the |an? |my |"
        r"open |view |preview |display |show me |look at )+", "", name, flags=re.I)
    name = name.strip(" \t.'\"")
    if (not name or len(name) > 180 or
            "/" in name or "\\" in name or ":" in name or
            name in {".", ".."} or ".." in Path(name).parts):
        return None
    if Path(name).suffix.lower() not in ALLOWED_TYPES:
        return None
    folder = next((n for n in _STANDARD_FOLDERS
                   if re.search(r"\b" + n + r"\b", prompt, re.I)), None)
    return OpenFileRequest(name=name, folder=folder)


def validate_preview_path(path: Path) -> Path:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise OpenFileError("Selected file isn't a regular local file")
    if path.suffix.lower() not in ALLOWED_TYPES:
        raise OpenFileError("This file type cannot be previewed safely")
    size = path.stat().st_size
    if not 0 < size <= MAX_OPEN_BYTES:
        raise OpenFileError("File must be under 15 MB for a local preview")
    return path


def locate_file(request: OpenFileRequest, *,
                folder_roots: dict[str, Path] | None = None) -> Path:
    """Bounded case-insensitive filename matching; reject ambiguous matches."""
    if request.name != Path(request.name).name:
        raise OpenFileError("Use a filename, not a path")
    if Path(request.name).suffix.lower() not in ALLOWED_TYPES:
        raise OpenFileError("File type is not eligible for safe previews")
    roots: dict[str, Path] = {}
    for name in ([request.folder] if request.folder else _STANDARD_FOLDERS):
        if not name:
            continue
        try:
            root = folder_roots[name] if folder_roots is not None else known_folder(name)
            if not root.is_dir() or root.is_symlink():
                continue
            roots[name] = root
        except (OSError, KeyError):
            continue
    if not roots:
        raise OpenFileError("None of the standard folders could be accessed")
    matches: list[Path] = []
    visited = 0
    for _name, root in roots.items():
        pending = [(root, 0)]
        while pending:
            directory, depth = pending.pop()
            try:
                children = list(directory.iterdir())
            except (PermissionError, OSError):
                continue
            for child in children:
                if child.is_symlink():
                    continue
                visited += 1
                if visited > MAX_VISITED:
                    raise OpenFileError("Too many files to search; choose a folder")
                try:
                    if child.is_dir():
                        if depth < MAX_DEPTH:
                            pending.append((child, depth + 1))
                    elif child.is_file() and child.name.casefold() == request.name.casefold():
                        matches.append(validate_preview_path(child))
                except (OSError, OpenFileError):
                    continue
    if not matches:
        raise OpenFileError(
            "I couldn't find " + request.name +
            " in Downloads, Desktop, Pictures or Documents. "
            "Use Open file… to choose its location manually.")
    # Explicit folder search takes precedence. If different files share the
    # same name, require disambiguation instead of opening a random one.
    unique = {str(path.resolve()).casefold(): path for path in matches}
    if len(unique) != 1:
        raise OpenFileError(
            "Several files share that name. Specify the folder or use Open file…")
    return next(iter(unique.values()))


def preview_type(path: Path) -> str:
    return "image" if validate_preview_path(path).suffix.lower() in IMAGE_TYPES else "text"
