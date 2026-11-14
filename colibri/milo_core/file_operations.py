"""Explicit, preview-before-approval local asset file organisation.

This is NOT an agentic shell or LLM-driven filesystem mutation. Owners choose
both folders and a literal filename fragment. Plans list every file, carry
metadata snapshots, are revalidated before execution and never overwrite.
The first version moves regular supported project assets on the SAME volume.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re


MAX_SCAN = 20000
MAX_FILES = 100
ALLOWED = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tga",
    ".obj", ".fbx", ".glb", ".gltf", ".mtl", ".blend", ".md3",
    ".wav", ".ogg", ".mp3", ".txt", ".md", ".json", ".csv",
    ".pdf", ".docx", ".ase", ".aseprite", ".psd",
})


class FileMoveError(RuntimeError):
    pass


@dataclass(frozen=True)
class FileMove:
    source: Path
    target: Path
    size: int
    mtime_ns: int
    inode: int


@dataclass(frozen=True)
class MovePlan:
    source_root: Path
    destination_root: Path
    literal_match: str
    files: tuple[FileMove, ...]


def _folder(folder: str | Path) -> Path:
    raw = Path(folder).expanduser()
    if raw.is_symlink() or not raw.is_dir():
        raise FileMoveError("Choose an existing, regular local folder")
    return raw.resolve(strict=True)


def plan_file_moves(source: str | Path, destination: str | Path,
                    contains: str) -> MovePlan:
    if (not isinstance(contains, str) or not 2 <= len(contains) <= 100
            or any(ch in contains for ch in "/\\:*?\"<>|")
            or contains.strip() != contains):
        raise FileMoveError("Enter a literal filename fragment (2–100 characters)")
    src = _folder(source)
    dst = _folder(destination)
    if src == dst:
        raise FileMoveError("Source and destination must be different folders")
    if src.stat().st_dev != dst.stat().st_dev:
        raise FileMoveError("This safe move tool only supports folders on one drive")
    # On Windows even literal path names are case-insensitive.
    reserved = {p.name.casefold() for p in dst.iterdir()}
    matches: list[FileMove] = []
    count = 0
    for file in src.iterdir():
        count += 1
        if count > MAX_SCAN:
            raise FileMoveError("Too many source entries: narrow the selected folder")
        if (file.is_symlink() or not file.is_file()
                or file.suffix.lower() not in ALLOWED
                or contains.casefold() not in file.name.casefold()):
            continue
        if file.name.casefold() in reserved:
            raise FileMoveError(
                "Destination already contains a matching filename; "
                "nothing was moved")
        if len(matches) >= MAX_FILES:
            raise FileMoveError("Preview limit is 100 matching files")
        info = file.stat()
        matches.append(FileMove(
            source=file, target=dst / file.name, size=info.st_size,
            mtime_ns=info.st_mtime_ns, inode=info.st_ino))
    return MovePlan(src, dst, contains,
                    tuple(sorted(matches, key=lambda x: x.source.name.casefold())))


def _recheck(plan: MovePlan) -> None:
    if _folder(plan.source_root) != plan.source_root or \
            _folder(plan.destination_root) != plan.destination_root:
        raise FileMoveError("One of the selected folders has changed")
    if plan.source_root.stat().st_dev != plan.destination_root.stat().st_dev:
        raise FileMoveError("Source and target are no longer on the same drive")
    reserved = {p.name.casefold() for p in plan.destination_root.iterdir()}
    for item in plan.files:
        if (item.source.is_symlink() or not item.source.is_file() or
                item.source.parent != plan.source_root or
                item.target.parent != plan.destination_root or
                item.target.name.casefold() in reserved):
            raise FileMoveError("A planned path changed or now conflicts")
        info = item.source.stat()
        if (info.st_size != item.size or info.st_mtime_ns != item.mtime_ns
                or info.st_ino != item.inode):
            raise FileMoveError("A planned file was modified since preview")


def execute_file_moves(plan: MovePlan, *, approved: bool = False) -> int:
    if not approved:
        raise PermissionError("Files cannot move without explicit approval")
    if not isinstance(plan, MovePlan) or not plan.files:
        raise FileMoveError("There are no files in this move plan")
    _recheck(plan)
    moved = 0
    for item in plan.files:
        # Each file is linked only if target does NOT exist, then unlinked.
        # This avoids overwrites and keeps source intact on link failure.
        # NTFS hardlinks require both folders on the same volume.
        try:
            _recheck(MovePlan(plan.source_root, plan.destination_root,
                              plan.literal_match, (item,)))
            os.link(item.source, item.target, follow_symlinks=False)
            try:
                item.source.unlink()
            except OSError:
                item.target.unlink(missing_ok=True)
                raise
            moved += 1
        except OSError as exc:
            raise FileMoveError(
                f"Stopped after moving {moved} file(s). "
                "The next file could not be moved without overwriting or "
                "risking data. Review the folders before retrying.") from exc
    return moved
