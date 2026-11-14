"""Typed, owner-approved Windows actions for Milo's local desktop.

The model never receives shell access. Only an explicit owner message is parsed
into one bounded operation. UI approval is a separate required signal, and
the broker revalidates the operation before using Windows APIs. No elevated
permissions, shell command strings, browser DOM automation, stealth input,
background surveillance, or stored broad action grants.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit
import webbrowser

from .chat_files import known_folder
from .app_discovery import AppMatch, normalise_name, _validate_exe
from .file_operations import (
    MovePlan, plan_file_moves, execute_file_moves, FileMoveError,
)


class PCActionError(RuntimeError):
    pass


@dataclass(frozen=True)
class PCPlan:
    kind: str
    title: str
    preview: str
    args: tuple[str, ...]
    prepared: MovePlan | None = None


KNOWN_APPS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
}
def _trusted_builtin(name: str) -> str:
    exe = KNOWN_APPS[name]
    if sys.platform != "win32":
        return exe  # Synthetic CI never launches the program itself.
    winroot = Path(os.environ.get("SystemRoot") or os.environ.get("WINDIR") or "C:\\Windows")
    location = (winroot / "explorer.exe" if exe == "explorer.exe"
                else winroot / "System32" / exe)
    if not location.is_file() or location.is_symlink():
        raise PCActionError("The trusted Windows app is unavailable: "+exe)
    return str(location)


FOLDERS = frozenset(("desktop", "documents", "downloads", "pictures"))
ASSET_TYPES = frozenset((
    ".txt", ".md", ".markdown", ".json", ".csv", ".pdf", ".docx",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp",
    ".blend", ".fbx", ".obj", ".gltf", ".glb", ".wav", ".mp3",
    ".py", ".js", ".ts", ".html", ".css", ".log",
))
_NAME = re.compile(r"^[^\x00-\x1f<>:\"/\\|?*]{1,120}$")
_RESERVED = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.I)
_NO_ACTION = re.compile(r"^\s*(?:how|why|what if|explain|tutorial|should i|would it)\b", re.I)
_PREFIX = re.compile(r"^\s*(?:(?:hey\s+)?milo[,:]?\s+)?(?:(?:can|could|would)\s+you\s+|please\s+)*", re.I)
_ACTION = re.compile(r"^(open|launch|start|run|show|list|summari[sz]e|rename|create|make|switch to|focus|type|move|organise|organize)\b", re.I)
_QUOTES = r"""["']([^"'\r\n]{1,500})["']"""
_WINDOWS_BLOCK = ("powershell", "command prompt", "terminal", "administrator",
                  "registry editor", "password", "credential", "windows security",
                  "sign in", "log in", "login", "task manager")


def _basename(value: str) -> str:
    value = value.strip()
    if not _NAME.fullmatch(value) or value in (".", "..") or _RESERVED.search(value):
        raise PCActionError("Use a simple local filename without path characters")
    if value.startswith("."):
        raise PCActionError("Hidden dotfiles are not supported by this tool")
    return value


def _folder(name: str, *, roots: dict[str, Path] | None = None) -> Path:
    if name not in FOLDERS:
        raise PCActionError("Choose Desktop, Documents, Downloads or Pictures")
    raw = (roots[name] if roots is not None else known_folder(name))
    if raw.is_symlink() or not raw.is_dir():
        raise PCActionError("The selected standard folder is unavailable")
    resolved = raw.resolve(strict=True)
    if not resolved.is_dir():
        raise PCActionError("Folder is inaccessible")
    return resolved


def _regular_file(folder: Path, name: str) -> Path:
    name = _basename(name)
    candidate = folder / name
    if candidate.is_symlink() or not candidate.is_file():
        raise PCActionError("The named file does not exist as a regular file")
    if candidate.suffix.casefold() not in ASSET_TYPES:
        raise PCActionError("This file type is not eligible for an approved rename")
    return candidate


def _safe_url(url: str) -> str:
    if len(url) > 1000 or any(c.isspace() or ord(c) < 32 for c in url):
        raise PCActionError("Use a single short HTTPS URL without whitespace")
    parsed = urlsplit(url)
    host = parsed.hostname
    if (parsed.scheme != "https" or not host or parsed.username or parsed.password
            or parsed.port not in (None, 443)
            or host in ("localhost",) or host.endswith(".local")
            or host.startswith("127.") or host == "::1"
            or any(part == ".." for part in parsed.path.split("/"))):
        raise PCActionError("Only public HTTPS URLs without credentials are allowed")
    if not re.fullmatch(r"[A-Za-z0-9.-]{3,253}", host) or "." not in host:
        raise PCActionError("The HTTPS host is invalid")
    return url


def _app_name(text: str, registered: dict[str, str]) -> tuple[str, str] | None:
    value = re.sub(r"\s+(?:app|application|program)\s*$", "", text, flags=re.I).strip()
    value = re.sub(r"^(?:the|my)\s+", "", value, flags=re.I)
    value = re.sub(r"\s+(?:for me|please)$", "", value, flags=re.I).strip().casefold()
    if value in KNOWN_APPS:
        return value, "builtin"
    if value in registered:
        return value, "registered"
    return None


def parse_pc_request(message: str, *, registered: dict[str, str] | None = None) -> PCPlan | None:
    """Parse only explicit *user* commands, never text from a model or webpage."""
    if not isinstance(message, str) or not 0 < len(message) <= 1500:
        return None
    text = message.strip()
    if _NO_ACTION.match(text):
        return None
    text = _PREFIX.sub("", text, count=1).strip()
    if not _ACTION.match(text):
        return None
    text = re.sub(r"\s+(?:please|for me)\??$", "", text, flags=re.I).strip()
    text = text.rstrip("?").strip()
    registered = registered or {}

    m = re.fullmatch(r"(?:open|launch|start|run)\s+(https://[^\s]+)", text, re.I)
    if m:
        try:
            url = _safe_url(m.group(1))
        except (PCActionError, ValueError):
            return None
        return PCPlan("open_url", "Open website", "Open this exact URL in your default browser:\n"+url, (url,))

    m = re.fullmatch(r"(?:open|launch|start|run|show)\s+(?:(?:my|the)\s+)?(desktop|documents|downloads|pictures)(?:\s+folder)?", text, re.I)
    if m:
        name = m.group(1).lower()
        return PCPlan("open_folder", "Open "+name.title(),
                      "Open the local "+name.title()+" folder in Windows File Explorer.", (name,))

    m = re.fullmatch(r"(?:open|launch|start|run)\s+(.{1,100})", text, re.I)
    if m:
        app = _app_name(m.group(1), registered)
        if app:
            name, origin = app
            exe = _trusted_builtin(name) if origin == "builtin" else registered[name]
            return PCPlan("launch_app", "Launch "+name.title(),
                          "Launch the approved "+origin+" application:\n"+str(exe), (name, origin, str(exe)))
        # Unknown installed application names are *looked up locally*.
        # No executable path is invented, and nothing launches at this stage.
        query = re.sub(
            r"\s+(?:app|application|program)\s*$", "",
            m.group(1), flags=re.I).strip()
        query = re.sub(r"^(?:the|my)\s+", "", query, flags=re.I)
        if (Path(query).suffix.casefold() not in ASSET_TYPES
                and Path(query).suffix.casefold() not in (
                    ".lnk", ".url", ".bat", ".cmd", ".ps1", ".vbs", ".js",
                    ".msi", ".scr", ".dll", ".pyd", ".py")
                and not query.casefold().endswith(" settings")):
            try:
                app_name = normalise_name(query)
            except ValueError:
                pass
            else:
                return PCPlan(
                    "discover_app", "Find "+app_name.title(),
                    "Search only installed application entries and standard "
                    "Windows application folders for "+app_name+". "
                    "Searching does not execute anything.", (app_name,))

    m = re.fullmatch(r"(?:list|show|summari[sz]e)\s+(?:the\s+)?(?:files|contents|items)?\s*(?:in|of|from)?\s*(?:my|the)?\s*(desktop|downloads|documents|pictures)(?:\s+folder)?", text, re.I)
    if m:
        name = m.group(1).lower()
        return PCPlan("folder_summary", "Summarise "+name.title(),
                      "Read filenames and sizes in your "+name.title()+" folder; no file contents.", (name,))

    if re.fullmatch(r"(?:list|show)\s+(?:my\s+)?(?:open|running)\s+windows", text, re.I):
        return PCPlan("list_windows", "List open windows",
                      "Read titles of visible Windows desktop windows (may contain personal data).", ())

    m = re.fullmatch(r"""(?:switch to|focus)\s+["']?([^"']{2,90})["']?""", text, re.I)
    if m:
        title = m.group(1).strip()
        return PCPlan("focus_window", "Switch active window",
                      "Bring a single matching visible window to the foreground:\n"+title, (title,))

    m = re.fullmatch(r"""type\s+["']([^"'\r\n]{1,500})["']\s+(?:into|in)\s+(?:window\s+)?["']?([^"']{2,90})["']?""", text, re.I)
    if m:
        content, title = m.group(1), m.group(2).strip()
        if any(ord(c) < 32 for c in content):
            return None
        return PCPlan("type_text", "Type in "+title,
                      "Type EXACTLY this text into a single matching, non-sensitive window. "
                      "No Enter is pressed:\n\n"+content+"\n\nTarget: "+title, (title, content))

    m = re.fullmatch(
        r"""(?:move|organise|organize)\s+(?:all\s+)?files\s+(?:containing|with name containing)\s+["']([^"']{2,100})["']\s+from\s+(?:my\s+)?(desktop|documents|downloads|pictures)\s+to\s+(?:my\s+)?(desktop|documents|downloads|pictures)""",
        text, re.I)
    if m:
        needle, src, dst = m.group(1), m.group(2).lower(), m.group(3).lower()
        if src == dst:
            return None
        return PCPlan(
            "move_assets", "Move selected project assets",
            "Review matching local files before moving them. No overwrite.",
            (src, dst, needle))

    m = re.fullmatch(r"""rename\s+["']([^"']{1,120})["']\s+to\s+["']([^"']{1,120})["']\s+in\s+(?:my\s+)?(desktop|documents|downloads|pictures)""", text, re.I)
    if m:
        old, new, folder = m.group(1), m.group(2), m.group(3).lower()
        try:
            _basename(old); _basename(new)
        except PCActionError:
            return None
        return PCPlan("rename_file", "Rename a local file",
                      "Rename one file without overwriting:\n"+folder+"/"+old+"\n→ "+folder+"/"+new,
                      (folder, old, new))

    m = re.fullmatch(r"""(?:create|make)\s+(?:a\s+)?(?:text\s+)?(?:note|document|file)\s+["']([^"']{1,120})["']\s+in\s+(?:my\s+)?(documents|desktop)\s+with\s+(?:text|content)\s+["']([^"'\r\n]{1,500})["']""", text, re.I)
    if m:
        name, folder, content = m.group(1), m.group(2).lower(), m.group(3)
        try:
            _basename(name)
        except PCActionError:
            return None
        if Path(name).suffix.casefold() not in (".txt", ".md"):
            return None
        return PCPlan("create_note", "Create a text document",
                      "Create a NEW "+folder+"/"+name+" with these exact contents:\n\n"+content+
                      "\n\nExisting files will never be overwritten.", (folder, name, content))
    return None


def load_apps(registry_path: Path) -> dict[str, str]:
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in list(data.items())[:30]:
        if (isinstance(key, str) and isinstance(value, str) and
                re.fullmatch(r"[a-z][a-z0-9 _-]{1,48}", key) and
                0 < len(value) < 1000):
            out[key] = value
    return out


def register_app(registry_path: Path, path: Path, *, name: str) -> None:
    """Owner-only file-picker operation; no model-created executable paths."""
    label = name.casefold().strip()
    if (not re.fullmatch(r"[a-z][a-z0-9 _-]{1,48}", label)
            or label in KNOWN_APPS):
        raise PCActionError("Choose a unique app name (2–49 letters, numbers or spaces)")
    exe = Path(path)
    if (not exe.is_file() or exe.is_symlink() or exe.suffix.lower() != ".exe"
            or str(exe).startswith("\\\\")):
        raise PCActionError("Select an existing local Windows .exe (not a shortcut)")
    apps = load_apps(registry_path)
    if len(apps) >= 30 and label not in apps:
        raise PCActionError("Maximum of 30 registered applications")
    apps[label] = str(exe.resolve(strict=True))
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    temp = registry_path.with_suffix(".tmp")
    temp.write_text(json.dumps(apps, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(registry_path)


def remove_app(registry_path: Path, name: str) -> bool:
    apps = load_apps(registry_path)
    if name not in apps:
        return False
    del apps[name]
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    temp = registry_path.with_suffix(".tmp")
    temp.write_text(json.dumps(apps, indent=2), encoding="utf-8")
    temp.replace(registry_path)
    return True


def visible_windows() -> list[tuple[int, str]]:
    """Read visible window titles only when the owner explicitly requests it."""
    if sys.platform != "win32":
        raise PCActionError("Windows window control requires Windows")
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    found: list[tuple[int, str]] = []
    callback_t = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _param):
        if len(found) >= 120:
            return False
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if 0 < length < 500:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, len(buf))
                name = buf.value.strip()
                if name:
                    found.append((int(hwnd), name))
        return True
    callback = callback_t(visit)
    user32.EnumWindows(callback, 0)
    return found


def _unique_window(title: str, *, enumerator=visible_windows) -> int:
    windows = enumerator()
    target = title.casefold()
    exact = [(h, n) for h, n in windows if n.casefold() == target]
    matches = exact or [(h, n) for h, n in windows if target in n.casefold()]
    if len(matches) == 0:
        raise PCActionError("No visible window matches that title. Try 'list open windows'.")
    if len(matches) != 1:
        raise PCActionError("Several windows match. Supply a more specific window title.")
    if any(word in matches[0][1].casefold() for word in _WINDOWS_BLOCK):
        raise PCActionError("This window is protected from automated typing/focus")
    if matches[0][1].casefold().startswith("milo"):
        raise PCActionError("Use Milo's own chat directly, not automated text input")
    return matches[0][0]


def _focus_hwnd(hwnd: int) -> None:
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    if not user32.IsWindow(hwnd):
        raise PCActionError("The target window is no longer available")
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    if not user32.SetForegroundWindow(hwnd):
        raise PCActionError("Windows denied foreground focus; select the app manually")


def _type_unicode(value: str) -> None:
    """Send Unicode keystrokes to the already verified foreground window."""
    from ctypes import wintypes
    ULONG_PTR = ctypes.c_size_t
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]
    class UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]
    class INPUT(ctypes.Structure):
        _anonymous_ = ("data",)
        _fields_ = [("type", wintypes.DWORD), ("data", UNION)]
    user32 = ctypes.windll.user32
    # SendInput requires UTF-16 code units; no clipboard, shell or Enter.
    units = value.encode("utf-16-le")
    events = []
    for pos in range(0, len(units), 2):
        unit = int.from_bytes(units[pos:pos + 2], "little")
        events.append(INPUT(type=1, ki=KEYBDINPUT(0, unit, 0x0004, 0, 0)))
        events.append(INPUT(type=1, ki=KEYBDINPUT(0, unit, 0x0006, 0, 0)))
    arr = (INPUT * len(events))(*events)
    sent = user32.SendInput(len(arr), arr, ctypes.sizeof(INPUT))
    if sent != len(arr):
        raise PCActionError("Windows did not accept all keystrokes; check the target manually")


def plan_discovered_launch(query: str, match: AppMatch) -> PCPlan:
    """Create a frozen candidate; owner still approves this exact executable."""
    name = normalise_name(query)
    if not isinstance(match, AppMatch) or match.score < 62:
        raise PCActionError("Not a verified installed application match")
    path = _validate_exe(match.path)
    if path is None or path != match.path:
        raise PCActionError("The discovered executable is no longer available")
    info = path.stat()
    return PCPlan(
        "launch_discovered_app", "Launch "+name.title(),
        "Milo found this installed program:\n\n"+str(path)+
        "\n\nFound via: "+match.source+
        "\nMatched name: "+match.label+
        "\n\nLaunch only this exact executable? No permission is saved.",
        (name, str(path), str(info.st_size), str(info.st_mtime_ns)))


def prepare_pc_plan(plan: PCPlan, *, roots: dict[str, Path] | None = None) -> PCPlan:
    """Preflight file moves so the confirmation shows EVERY actual filename."""
    if plan.kind != "move_assets":
        return plan
    src, dst, needle = plan.args
    source = _folder(src, roots=roots)
    destination = _folder(dst, roots=roots)
    prepared = plan_file_moves(source, destination, needle)
    if not prepared.files:
        raise PCActionError("No eligible matching project files were found")
    lines = [
        "Move exactly these "+str(len(prepared.files))+" file(s):",
        *[x.source.name+" ("+str(x.size)+" bytes)" for x in prepared.files],
        "",
        "FROM: "+str(source),
        "TO: "+str(destination),
        "Every file will be rechecked immediately before moving.",
        "No files will be overwritten; no recursive traversal.",
    ]
    # The askyesno dialog is finite; MAX_FILES remains 100 but the UI
    # declines huge reviews rather than truncating essential filenames.
    preview = "\n".join(lines)
    if len(preview) > 4500:
        raise PCActionError("Too many filenames for a complete approval preview")
    return replace(plan, preview=preview, prepared=prepared)


def execute_pc_plan(plan: PCPlan, *, approved: bool = False,
                    registered: dict[str, str] | None = None,
                    roots: dict[str, Path] | None = None,
                    windows=None,
                    app_launcher=None,
                    folder_launcher=None,
                    browser_opener=None,
                    focus_window=None,
                    type_writer=None,
                    platform: str | None = None) -> str:
    if not approved:
        raise PermissionError("Milo needs fresh, explicit owner approval for this operation")
    if not isinstance(plan, PCPlan) or plan.kind not in (
        "launch_app", "open_folder", "open_url", "folder_summary",
        "list_windows", "focus_window", "type_text", "rename_file", "create_note",
        "move_assets", "launch_discovered_app"
    ):
        raise PCActionError("Invalid proposed Windows operation")
    if (platform or sys.platform) != "win32":
        raise PCActionError("PC control is available only in the Windows desktop app")
    registered = registered or {}
    windows = windows or visible_windows
    folder_launcher = folder_launcher or (lambda value: os.startfile(value))
    app_launcher = app_launcher or (lambda exe: subprocess.Popen([exe], shell=False,
                                                                 stdin=subprocess.DEVNULL,
                                                                 stdout=subprocess.DEVNULL,
                                                                 stderr=subprocess.DEVNULL))
    browser_opener = browser_opener or webbrowser.open
    focus_window = focus_window or _focus_hwnd
    type_writer = type_writer or _type_unicode
    kind = plan.kind
    if kind == "launch_discovered_app":
        name, raw, size, mtime = plan.args
        normalise_name(name)
        path = _validate_exe(Path(raw))
        if path is None or str(path) != raw:
            raise PCActionError("Approved app executable path changed")
        info = path.stat()
        if info.st_size != int(size) or info.st_mtime_ns != int(mtime):
            raise PCActionError("The discovered executable changed since approval")
        app_launcher(str(path))
        return "Launched "+name+" from the owner-approved discovered executable."
    if kind == "launch_app":
        name, origin, expected = plan.args
        current = (_trusted_builtin(name) if origin == "builtin" else registered.get(name))
        if not current or current != expected:
            raise PCActionError("Application permission or location changed since preview")
        if origin == "registered":
            path = Path(current)
            if (not path.is_file() or path.is_symlink() or path.suffix.lower() != ".exe"):
                raise PCActionError("The approved executable was moved or is no longer safe")
        app_launcher(current)
        return "Launched "+name+"."
    if kind == "open_folder":
        folder = _folder(plan.args[0], roots=roots)
        folder_launcher(str(folder))
        return "Opened "+plan.args[0].title()+" in Windows File Explorer."
    if kind == "open_url":
        url = _safe_url(plan.args[0])
        if not browser_opener(url):
            raise PCActionError("The default browser did not confirm it opened the link")
        return "Opened the approved HTTPS website in your browser."
    if kind == "folder_summary":
        folder = _folder(plan.args[0], roots=roots)
        size, count, samples, types = 0, 0, [], Counter()
        for item in folder.iterdir():
            if count >= 5000:
                raise PCActionError("Folder has too many entries; select a smaller folder")
            if item.is_symlink() or not item.is_file():
                continue
            info = item.stat()
            count += 1
            size += info.st_size
            types[item.suffix.lower() or "[none]"] += 1
            if len(samples) < 25:
                samples.append(item.name[:120])
        by_type = ", ".join(k+" "+str(v) for k, v in types.most_common(10))
        return (plan.args[0].title()+": "+str(count)+" regular files, "+
                str(round(size / 1048576, 2))+" MB combined.\nTypes: "+
                (by_type or "none")+"\nFirst "+str(len(samples))+
                " filenames:\n"+"\n".join(samples))
    if kind == "list_windows":
        matches = windows()
        return ("Visible desktop windows ("+str(len(matches))+"):\n"+
                "\n".join(title[:160] for _hwnd, title in matches[:40]))
    if kind in ("focus_window", "type_text"):
        title = plan.args[0]
        hwnd = _unique_window(title, enumerator=windows)
        focus_window(hwnd)
        if kind == "focus_window":
            return "Switched to the matching desktop window."
        value = plan.args[1]
        if not 0 < len(value) <= 500 or any(ord(c) < 32 for c in value):
            raise PCActionError("Typing request changed or contains control characters")
        if windows is visible_windows:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetForegroundWindow.restype = wintypes.HWND
            if user32.GetForegroundWindow() != hwnd:
                raise PCActionError("Target lost focus, so no text was typed")
        type_writer(value)
        return "Typed "+str(len(value))+" approved characters into the selected window."
    if kind == "move_assets":
        if plan.prepared is None or not isinstance(plan.prepared, MovePlan):
            raise PCActionError("File move has not been fully previewed")
        src, dst, needle = plan.args
        if (plan.prepared.literal_match != needle or
                plan.prepared.source_root != _folder(src, roots=roots) or
                plan.prepared.destination_root != _folder(dst, roots=roots)):
            raise PCActionError("The approved move source or destination changed")
        count = execute_file_moves(plan.prepared, approved=True)
        return "Moved "+str(count)+" fully previewed project asset(s) without overwrite."
    if kind == "rename_file":
        folder_name, old, new = plan.args
        src_root = _folder(folder_name, roots=roots)
        src = _regular_file(src_root, old)
        dest_name = _basename(new)
        if (Path(old).suffix.casefold() != Path(dest_name).suffix.casefold()
                or old.casefold() == dest_name.casefold()):
            raise PCActionError("Renames must preserve file extension and change the name")
        dest = src_root / dest_name
        if dest.exists() or dest.is_symlink():
            raise PCActionError("The destination already exists; nothing was overwritten")
        # Link-then-unlink fails rather than clobbering an existing target,
        # including on Unix during CI. Only same-directory regular files.
        try:
            os.link(src, dest, follow_symlinks=False)
        except FileExistsError as exc:
            raise PCActionError("The destination appeared; no files were overwritten") from exc
        try:
            src.unlink()
        except OSError as exc:
            dest.unlink(missing_ok=True)
            raise PCActionError("Source could not be removed; original remains") from exc
        return "Renamed "+old+" to "+dest_name+" in "+folder_name.title()+"."
    if kind == "create_note":
        folder_name, name, content = plan.args
        folder = _folder(folder_name, roots=roots)
        name = _basename(name)
        if Path(name).suffix.lower() not in (".txt", ".md") or len(content) > 500:
            raise PCActionError("Only bounded .txt and .md notes can be created")
        target = folder / name
        if target.is_symlink() or target.exists():
            raise PCActionError("That filename is already taken; no file was overwritten")
        with target.open("x", encoding="utf-8") as stream:
            stream.write(content+"\n")
        return "Created "+name+" in "+folder_name.title()+" (without overwriting)."
    raise PCActionError("Operation is not supported")
