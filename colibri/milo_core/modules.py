"""Local, replaceable first-party feature modules for Milo desktop.

Modules are acquired only through hash-checked assets published by the pinned
BoomForge/colibri release channel. Installation requires the owner's approval.
Python feature modules are trusted code, not a sandbox; they must be reviewed
before publication. Installation has no postinstall hooks and never auto-runs a
new module. Activation requires an explicit restart.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
import zipfile


class ModuleError(RuntimeError):
    pass


_ID = re.compile(r"[a-z][a-z0-9_-]{2,48}")
_SEMVER = re.compile(r"\d+\.\d+\.\d+")
BUILTINS = {
    "models": ("Models", "Local model selection and GGUF import", True),
    "memory": ("Memory", "Approved memories and original personality recovery", True),
    "drive_memory": ("Google Drive memory",
                     "Owner-authorised read-only Google Drive reference snapshots", True),
    "files": ("File search", "Deterministic filename tools", True),
    "vision": ("Vision", "Optional local image understanding (owner approval)", True),
    "sol": ("SOL consultation", "Permission-gated manual second opinions", True),
    "backup": ("Backup & restore", "Encrypted portable backups", True),
}
MAX_ARCHIVE = 70 * 1024 * 1024
MAX_UNPACKED = 150 * 1024 * 1024
ALLOWED_SUFFIXES = {".py", ".json", ".txt", ".md", ".png", ".webp",
                    ".jpg", ".jpeg", ".ico", ".dll", ".pyd", ".onnx", ".bin"}


def storage_root(data_root: Path) -> Path:
    root = data_root / "modules"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _state_path(data_root: Path) -> Path:
    return storage_root(data_root) / "state.json"


def load_state(data_root: Path) -> dict:
    try:
        state = json.loads(_state_path(data_root).read_text(encoding="utf-8"))
        if isinstance(state, dict) and state.get("schema") == 1:
            enabled = state.get("enabled", {})
            installed = state.get("installed", {})
            if isinstance(enabled, dict) and isinstance(installed, dict):
                return state
    except (OSError, ValueError):
        pass
    return {"schema": 1, "enabled": {}, "installed": {}}


def save_state(data_root: Path, state: dict) -> None:
    path = _state_path(data_root)
    raw = json.dumps(state, sort_keys=True, ensure_ascii=False, indent=2)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())
    temp.replace(path)


def enabled(data_root: Path, module_id: str) -> bool:
    return load_state(data_root).get("enabled", {}).get(module_id, True) is not False


def set_enabled(data_root: Path, module_id: str, new_value: bool):
    state = load_state(data_root)
    if module_id not in BUILTINS and module_id not in state.get("installed", {}):
        raise ModuleError("unknown module")
    state.setdefault("enabled", {})[module_id] = bool(new_value)
    save_state(data_root, state)


def valid_manifest(blob: bytes, expected_id: str | None = None,
                   expected_version: str | None = None) -> dict:
    try:
        item = json.loads(blob.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ModuleError("invalid module.json") from exc
    if not isinstance(item, dict) or item.get("api") != 1:
        raise ModuleError("unsupported module API")
    module_id = item.get("id")
    version = item.get("version")
    name = item.get("name")
    entry = item.get("entrypoint")
    if (not isinstance(module_id, str) or not _ID.fullmatch(module_id)
            or module_id in BUILTINS or not isinstance(version, str)
            or not _SEMVER.fullmatch(version) or not isinstance(name, str)
            or not 1 <= len(name) <= 80):
        raise ModuleError("invalid module manifest fields")
    if expected_id and module_id != expected_id:
        raise ModuleError("module does not match published ID")
    if expected_version and version != expected_version:
        raise ModuleError("module version does not match release catalog")
    if entry is not None and (
        not isinstance(entry, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,50}\.py", entry)
    ):
        raise ModuleError("unsafe module entrypoint")
    permissions = item.get("permissions", [])
    if (not isinstance(permissions, list)
            or not all(p in {"ui"} for p in permissions)):
        raise ModuleError("unsupported module permissions")
    return item


def install_verified_module(data_root: Path, archive: Path, *,
                            module_id: str, version: str) -> dict:
    """Consume only bytes already verified against a GitHub Release digest."""
    if not _ID.fullmatch(module_id) or not _SEMVER.fullmatch(version):
        raise ModuleError("invalid module destination")
    if not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE:
        raise ModuleError("module archive is missing or oversized")
    modules = storage_root(data_root)
    target = modules / module_id / version
    if target.exists():
        existing = target / "module.json"
        if existing.is_file():
            return valid_manifest(existing.read_bytes(), module_id, version)
        raise ModuleError("incomplete module installation exists")
    staging_parent = modules / module_id
    staging_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="milo-module-", dir=staging_parent) as stage:
        temporary = Path(stage)
        try:
            with zipfile.ZipFile(archive) as bundle:
                entries = bundle.infolist()
                if not 1 <= len(entries) <= 500:
                    raise ModuleError("invalid module archive inventory")
                names = set()
                total = 0
                for file in entries:
                    raw_path = PurePosixPath(file.filename)
                    if (file.filename in names or file.filename.startswith("/")
                            or "\\" in file.filename
                            or not raw_path.parts
                            or any(x in {"..", ".", ""} for x in raw_path.parts)):
                        raise ModuleError("unsafe module archive path")
                    names.add(file.filename)
                    mode = (file.external_attr >> 16) & 0o170000
                    if mode == stat.S_IFLNK:
                        raise ModuleError("module symlinks are forbidden")
                    if file.is_dir():
                        continue
                    if raw_path.suffix.lower() not in ALLOWED_SUFFIXES:
                        raise ModuleError("unsupported module archive file extension")
                    total += file.file_size
                    if total > MAX_UNPACKED:
                        raise ModuleError("unpacked module exceeds size limit")
                if "module.json" not in names:
                    raise ModuleError("missing module.json")
                meta = valid_manifest(bundle.read("module.json"), module_id, version)
                for item in entries:
                    destination = temporary.joinpath(*PurePosixPath(item.filename).parts)
                    if item.is_dir():
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(item) as src, destination.open("xb") as dst:
                        shutil.copyfileobj(src, dst)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ModuleError("damaged module archive") from exc
        if meta.get("entrypoint") and not (temporary / meta["entrypoint"]).is_file():
            raise ModuleError("declared module entrypoint not found")
        temporary.rename(target)
    state = load_state(data_root)
    state.setdefault("installed", {})[module_id] = version
    # Newly installed modules are OFF until owner explicitly enables them.
    state.setdefault("enabled", {})[module_id] = False
    save_state(data_root, state)
    return meta


@dataclass(frozen=True)
class ModuleAction:
    module_id: str
    label: str
    open_panel: object


class PluginUI:
    """Tiny UI registration bridge. NOT a security sandbox."""
    def __init__(self, module_id: str):
        self.module_id = module_id
        self.actions: list[ModuleAction] = []

    def add_action(self, label: str, callback):
        if not isinstance(label, str) or not 1 <= len(label) <= 45 or not callable(callback):
            raise ModuleError("invalid plugin action")
        if len(self.actions) >= 6:
            raise ModuleError("plugin registered too many actions")
        self.actions.append(ModuleAction(self.module_id, label, callback))


def load_module_actions(data_root: Path) -> tuple[list[ModuleAction], list[str]]:
    """Only explicitly enabled, installed add-ons are loaded on process start."""
    state = load_state(data_root)
    actions: list[ModuleAction] = []
    errors: list[str] = []
    for module_id, version in sorted(state.get("installed", {}).items()):
        if not state.get("enabled", {}).get(module_id, False):
            continue
        if (not isinstance(module_id, str) or not _ID.fullmatch(module_id)
                or not isinstance(version, str) or not _SEMVER.fullmatch(version)):
            errors.append("invalid module state entry")
            continue
        root = storage_root(data_root) / module_id / version
        try:
            meta = valid_manifest((root / "module.json").read_bytes(), module_id, version)
            entrypoint = meta.get("entrypoint")
            if not entrypoint:
                continue
            target = root / entrypoint
            spec = importlib.util.spec_from_file_location(
                "milo_plugin_" + module_id.replace("-", "_"), target)
            if spec is None or spec.loader is None:
                raise ModuleError("module loader unavailable")
            plugin = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(plugin)
            if not hasattr(plugin, "register"):
                raise ModuleError("module lacks register(api) entrypoint")
            api = PluginUI(module_id)
            plugin.register(api)
            actions.extend(api.actions)
        except Exception:
            # Never expose private data or plugin exception details in UI.
            errors.append("Could not load module: " + module_id)
    return actions, errors
