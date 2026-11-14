"""Bounded read-only Windows installed-application discovery for Milo.

Explicit owner requests only. Searches Windows App Paths/Uninstall registry and
normal per-user/system application installation folders. Never scans arbitrary
drives, executes shortcuts, runs scripts or guesses a target from LLM output.
Candidates must be real local .exe files and are approved individually in UI.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys

MAX_VISITED = 24000
MAX_DEPTH = 5
MAX_RESULTS = 12
MAX_REGISTRY_KEYS = 4500
_EXE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+\-]{0,79}$")
_UNSAFE = frozenset((
    "cmd", "command prompt", "powershell", "pwsh", "terminal",
    "regedit", "registry editor", "task manager", "taskmgr",
    "windows security", "security", "administrator", "admin",
    "system", "control panel", "settings", "startup", "run",
    "explorer shell", "file", "app", "application", "exe",
))
_KNOWN_EXES = {
    "obs": ("obs64", "obs32"),
    "obs studio": ("obs64", "obs32"),
    "vroid": ("vroidstudio",),
    "vroid studio": ("vroidstudio",),
    "blender": ("blender",),
    "firefox": ("firefox",),
    "chrome": ("chrome",),
    "discord": ("discord",),
    "steam": ("steam",),
    "gimp": ("gimp",),
    "krita": ("krita",),
}


@dataclass(frozen=True)
class AppMatch:
    path: Path
    label: str
    source: str
    score: int


def normalise_name(raw: str) -> str:
    if not isinstance(raw, str) or not _EXE_NAME.fullmatch(raw.strip()):
        raise ValueError("Enter a short installed app name, not an executable path or command")
    name = raw.strip().casefold()
    if name.endswith(".exe"):
        name = name[:-4].strip()
    if (not name or name in _UNSAFE or len(name.split()) > 7
            or name.startswith(".") or ".." in name):
        raise ValueError("Unsupported executable search name")
    return name


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


def _score(name: str, target: Path, label: str) -> int:
    aliases = _KNOWN_EXES.get(name, ())
    stem = target.stem.casefold()
    compact = "".join(_tokens(name))
    exe = "".join(_tokens(stem))
    label_norm = "".join(_tokens(label))
    if exe == compact or stem in aliases:
        return 120
    if label_norm == compact:
        return 110
    if compact and (compact in exe or compact in label_norm):
        return 92
    tokens = _tokens(name)
    if not tokens:
        return 0
    parts = " ".join(target.parts[-5:]).casefold()
    haystack = " ".join(_tokens(parts + " " + label))
    if all(token in haystack for token in tokens):
        return 72
    # For queries like OBS Studio, parent obs-studio + obs64.exe qualifies.
    if all(any(part.startswith(t) for part in _tokens(parts)) for t in tokens):
        return 62
    return 0


def _validate_exe(candidate: Path) -> Path | None:
    try:
        path = Path(candidate)
        if (path.is_symlink() or path.suffix.casefold() != ".exe"
                or not path.is_file() or path.stat().st_size == 0):
            return None
        resolved = path.resolve(strict=True)
        if resolved.is_symlink() or resolved.suffix.casefold() != ".exe":
            return None
        return resolved
    except (OSError, ValueError, RuntimeError):
        return None


def default_roots() -> tuple[Path, ...]:
    if sys.platform != "win32":
        return ()
    environment = os.environ
    roots = (
        environment.get("ProgramFiles"),
        environment.get("ProgramFiles(x86)"),
        str(Path(environment.get("LOCALAPPDATA", "")) / "Programs")
        if environment.get("LOCALAPPDATA") else None,
    )
    found: list[Path] = []
    for raw in roots:
        if not raw:
            continue
        folder = Path(raw)
        try:
            if folder.is_dir() and not folder.is_symlink() and folder not in found:
                found.append(folder)
        except OSError:
            continue
    return tuple(found)


def _registry_entries():
    """Read only Windows' existing app registration, never mutate the registry."""
    if sys.platform != "win32":
        return
    try:
        import winreg
    except ImportError:
        return
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            for parent, source in (
                (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths", "Windows App Paths"),
                (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "Installed apps"),
            ):
                try:
                    base = winreg.OpenKey(hive, parent, 0, winreg.KEY_READ | view)
                except OSError:
                    continue
                with base:
                    for index in range(MAX_REGISTRY_KEYS):
                        try:
                            subname = winreg.EnumKey(base, index)
                        except OSError:
                            break
                        try:
                            with winreg.OpenKey(base, subname, 0, winreg.KEY_READ | view) as sub:
                                def field(name):
                                    try:
                                        data = winreg.QueryValueEx(sub, name)[0]
                                        return data if isinstance(data, str) else ""
                                    except OSError:
                                        return ""
                                display = field("DisplayName") or subname
                                if source == "Windows App Paths":
                                    target = field("")
                                else:
                                    target = field("DisplayIcon")
                                    if not target:
                                        # InstallLocation is a directory, not an
                                        # executable; the bounded folder scan
                                        # handles normal installation directories.
                                        continue
                                if not target:
                                    continue
                                # DisplayIcon is often "C:\app\app.exe",0.
                                target = target.strip().strip('"')
                                target = re.sub(r",\s*-?\d+\s*$", "", target).strip('"')
                                if target.casefold().endswith(".exe"):
                                    yield Path(os.path.expandvars(target)), display, source
                        except (OSError, ValueError):
                            continue


def find_installed_apps(
    query: str, *, roots: tuple[Path, ...] | None = None,
    registry_entries=None, max_visited: int = MAX_VISITED
) -> list[AppMatch]:
    """Deterministic bounded discovery. Results are NOT permission to launch."""
    name = normalise_name(query)
    if not 1 <= max_visited <= 100000:
        raise ValueError("Search size out of bounds")
    if roots is None:
        roots = default_roots()
    found: dict[str, AppMatch] = {}

    def accept(candidate: Path, label: str, source: str):
        path = _validate_exe(candidate)
        if path is None:
            return
        score = _score(name, path, label)
        if score < 62:
            return
        key = os.path.normcase(str(path)).casefold()
        match = AppMatch(path, label[:100], source, score)
        if key not in found or score > found[key].score:
            found[key] = match

    registry_iter = _registry_entries() if registry_entries is None else registry_entries
    for path, label, source in registry_iter:
        if isinstance(label, str) and isinstance(source, str):
            accept(Path(path), label, source)

    visited = 0
    for root in roots:
        root = Path(root)
        if root.is_symlink() or not root.is_dir():
            continue
        pending = [(root, 0)]
        while pending and visited < max_visited:
            current, depth = pending.pop()
            try:
                entries = list(current.iterdir())
            except (PermissionError, OSError):
                continue
            for item in entries:
                visited += 1
                if visited > max_visited:
                    break
                try:
                    if item.is_symlink():
                        continue
                    if item.is_file() and item.suffix.casefold() == ".exe":
                        accept(item, item.stem, "Installed files")
                    elif item.is_dir() and depth < MAX_DEPTH:
                        pending.append((item, depth+1))
                except (PermissionError, OSError):
                    continue
    return sorted(
        found.values(), key=lambda entry: (
            -entry.score, entry.path.name.casefold(), str(entry.path).casefold())
    )[:MAX_RESULTS]
