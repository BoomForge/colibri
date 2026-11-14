"""Milo-owned local Colibri engine lifecycle.

The engine is built from BoomForge/colibri and shipped alongside the Milo app.
No external Python, installer, browser, or shell window is needed at runtime.
Model weights are separate and never silently downloaded or executed from a
third-party command. Only the exact process Milo starts is terminated on exit.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen


class EngineStartupError(RuntimeError):
    pass


def install_root() -> Path:
    """Installer/portable root, distinct from mutable user memory."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def managed_models_root(root: Path | None = None) -> Path:
    """Prefer the portable Models directory; fall back only if unwritable."""
    base = root or install_root()
    portable = base / "Models"
    try:
        portable.mkdir(parents=True, exist_ok=True)
        test_file = portable / ".milo-write-probe"
        with test_file.open("xb"):
            pass
        test_file.unlink()
        return portable
    except (OSError, PermissionError):
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
        folder = local / "Milo" / "Models"
        folder.mkdir(parents=True, exist_ok=True)
        return folder


def model_valid(path: Path) -> bool:
    if not path.is_dir() or path.is_symlink():
        return False
    # Colibri's gateway needs generated config + tokenizer. A GGUF imported
    # with the first-party --mode link has gguf.json rather than local weights.
    if not (path / "config.json").is_file() or not (path / "tokenizer.json").is_file():
        return False
    weights = any(
        file.is_file() and not file.is_symlink() and
        file.suffix.lower() in {".gguf", ".safetensors", ".bin"}
        for file in path.iterdir()
    )
    if weights:
        return True
    pointer = path / "gguf.json"
    if pointer.is_symlink() or not pointer.is_file():
        return False
    try:
        import json
        obj = json.loads(pointer.read_text(encoding="utf-8"))
        source = obj.get("gguf")
        if not isinstance(source, str) or not source:
            return False
        file = Path(source)
        return file.is_file() and not file.is_symlink() and file.suffix.lower() == ".gguf"
    except (OSError, ValueError, TypeError):
        return False


def discover_loose_gguf(models_root: Path) -> list[Path]:
    """Find GGUF weights waiting for local in-place Colibri metadata import."""
    root = models_root.resolve(strict=True)
    result: list[Path] = []
    pending = [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        if depth > 2:
            continue
        for child in directory.iterdir():
            if child.is_symlink():
                continue
            if child.is_file() and child.suffix.lower() == ".gguf" and child.stat().st_size >= 24:
                result.append(child)
            elif child.is_dir() and depth < 2 and child.resolve().is_relative_to(root):
                pending.append((child, depth + 1))
    return sorted(result, key=lambda p: str(p).casefold())


def gguf_target(models_root: Path, weight: Path) -> Path:
    root = models_root.resolve(strict=True)
    file = weight.resolve(strict=True)
    if not file.is_relative_to(root) or file.suffix.lower() != ".gguf":
        raise ValueError("GGUF must first be stored under Milo Models")
    # The output must not be the same name as a user-owned directory.
    return file.parent / (file.stem + ".colibri")


def discover_models(models_root: Path) -> list[Path]:
    """Bounded search, no traversal of symlinked model directories."""
    root = models_root.resolve(strict=True)
    result: list[Path] = []
    pending = [(models_root, 0)]
    while pending:
        directory, depth = pending.pop()
        if directory.is_symlink() or not directory.is_dir():
            continue
        if model_valid(directory):
            result.append(directory)
            continue
        if depth >= 3:
            continue
        for child in sorted(directory.iterdir(), key=lambda p: p.name.casefold(), reverse=True):
            if child.is_dir() and not child.is_symlink() and child.resolve().is_relative_to(root):
                pending.append((child, depth + 1))
    return sorted(result, key=lambda p: str(p).casefold())


def chosen_model(models_root: Path, selected: str) -> Path | None:
    if not selected:
        return None
    root = models_root.resolve(strict=True)
    candidate = (root / selected).resolve()
    if not candidate.is_relative_to(root) or not model_valid(candidate):
        return None
    return candidate


def available_runtime_engines(runtime: Path) -> list[str]:
    return sorted(p.stem for p in runtime.glob("*.exe") if p.is_file())


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _online(base_url: str, *, timeout: float = 1.0) -> bool:
    try:
        with urlopen(base_url + "/models", timeout=timeout) as resp:
            return resp.status == 200
    except (URLError, OSError, TimeoutError, ValueError):
        return False


class LocalEngine:
    """Owns one hidden, per-launch service process and its associated log."""
    def __init__(self, root: Path, *, log_folder: Path):
        self.root = root
        self.runtime = root / "Runtime"
        self.log_folder = log_folder
        self.process: subprocess.Popen | None = None
        self.port: int | None = None
        self.endpoint: str | None = None
        self._lock = threading.RLock()
        self._log = None

    def start(self, model: Path, *, backend: str = "vulkan") -> str:
        if sys.platform != "win32":
            raise EngineStartupError("the bundled local engine preview currently targets Windows")
        if backend not in {"vulkan", "cpu"}:
            raise EngineStartupError("unsupported engine backend selection")
        model = model.resolve(strict=True)
        if not model_valid(model):
            raise EngineStartupError("model folder must include config.json, tokenizer.json and weights")
        with self._lock:
            if self.process is not None:
                if self.process.poll() is None and self.endpoint:
                    return self.endpoint
                self.stop()
            if not self.runtime.is_dir() or not (self.runtime / "openai_server.py").is_file():
                raise EngineStartupError("bundled Colibri runtime missing. Re-extract the full Milo ZIP.")
            engines = available_runtime_engines(self.runtime)
            if not engines:
                raise EngineStartupError("Colibri engine binaries missing from the application.")
            self.log_folder.mkdir(parents=True, exist_ok=True)
            self._log = (self.log_folder / "colibri-service.log").open("a", encoding="utf-8")
            self.port = _free_port()
            self.endpoint = "http://127.0.0.1:" + str(self.port) + "/v1"
            command = (
                [sys.executable] if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "desktop.milo_desktop"]
            ) + [
                "--colibri-service",
                "--model-dir", str(model),
                "--runtime-dir", str(self.runtime),
                "--port", str(self.port),
                "--backend", backend,
                "--owner-pid", str(os.getpid()),
            ]
            # No shell, no external interpreter, no console.
            kwargs = {
                "cwd": str(self.root),
                "stdin": subprocess.DEVNULL,
                "stdout": self._log,
                "stderr": subprocess.STDOUT,
                "close_fds": True,
                "creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                "env": {**os.environ, "MILO_HIDE_ENGINE_CONSOLE": "1"},
            }
            try:
                self.process = subprocess.Popen(command, **kwargs)
            except OSError as exc:
                self._log.close()
                self._log = None
                raise EngineStartupError("could not start bundled Colibri service") from exc
            return self.endpoint

    def await_ready(self, *, max_wait_seconds: float = 150.0) -> bool:
        deadline = time.monotonic() + max_wait_seconds
        while time.monotonic() < deadline:
            with self._lock:
                process = self.process
                endpoint = self.endpoint
            if process is None or process.poll() is not None:
                raise EngineStartupError(
                    "Colibri stopped during startup. See Milo/logs/colibri-service.log in local app data.")
            if endpoint and _online(endpoint, timeout=1.5):
                return True
            time.sleep(0.75)
        raise EngineStartupError("local model loading timed out; check the Colibri log")

    def stop(self) -> None:
        with self._lock:
            process = self.process
            self.process = None
            self.port = None
            self.endpoint = None
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=8)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        process.kill()
                    except OSError:
                        pass
            if self._log is not None:
                self._log.close()
                self._log = None
