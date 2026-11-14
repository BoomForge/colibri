"""Deterministic, non-LLM filename-prefix search.

The caller is responsible for choosing an approved search root. Only metadata
is read: matching files are not opened, executed, uploaded, or memorised.
"""
from __future__ import annotations

from pathlib import Path


def find_files_by_prefix(
    directory: str | Path,
    prefix: str,
    *,
    recursive: bool = False,
    limit: int = 200,
) -> list[Path]:
    if not isinstance(prefix, str) or not prefix or "/" in prefix or "\\" in prefix:
        raise ValueError("prefix must be a nonempty filename fragment")
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    root = Path(directory).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("search root must be an existing directory, not a symlink")
    iterator = root.rglob("*") if recursive else root.iterdir()
    matches: list[Path] = []
    for candidate in iterator:
        if candidate.is_symlink() or not candidate.is_file():
            continue
        if candidate.name.casefold().startswith(prefix.casefold()):
            matches.append(candidate.relative_to(root))
            if len(matches) > limit:
                raise ValueError("too many matching files; narrow the search")
    return sorted(matches, key=lambda path: (str(path).casefold(), str(path)))
