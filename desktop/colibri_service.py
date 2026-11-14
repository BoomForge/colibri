"""Entrypoint for the Colibri subprocess of Milo.exe (no visible console).

Imports Colibri's first-party gateway directly from the bundled Runtime
directory. Does not install Python, invoke a shell, contact the Internet, or
open Colibri's dashboard. Milo owns this worker and shuts it down on exit.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import threading


def monitor_owner(pid: int) -> None:
    """Exit if Milo UI crashes or is force-closed; don't leave resident GPU jobs."""
    if sys.platform != "win32" or pid <= 0:
        return
    SYNCHRONIZE, INFINITE = 0x00100000, 0xFFFFFFFF
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        raise RuntimeError("Milo parent process has already exited")
    def watch() -> None:
        try:
            kernel.WaitForSingleObject(handle, INFINITE)
        finally:
            kernel.CloseHandle(handle)
        # Closing the gateway process tears down the Windows job holding its
        # inference child. No remote service or other program is affected.
        os._exit(0)
    threading.Thread(target=watch, name="milo-parent-watchdog", daemon=True).start()


def run_service(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Milo's private Colibri worker")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--backend", choices=("cpu", "vulkan"), default="vulkan")
    parser.add_argument("--owner-pid", type=int, required=True)
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("currently supported only on Windows")
    runtime = args.runtime_dir.resolve(strict=True)
    model = args.model_dir.resolve(strict=True)
    if not 1 <= args.port <= 65535:
        parser.error("invalid local port")
    if not (model / "config.json").is_file() or not (model / "tokenizer.json").is_file():
        parser.error("model folder is not a complete Colibri-compatible checkpoint")
    if not (runtime / "openai_server.py").is_file():
        parser.error("bundled Colibri source runtime is missing")
    # First-party modules are copied from the checked-out BoomForge/colibri
    # commit during the same CI build, not downloaded dynamically at launch.
    sys.path.insert(0, str(runtime))
    from family_registry import default_model_id, resolve_model
    import openai_server
    info = resolve_model(str(model))
    family = info.descriptor
    engine = runtime / (family.engine_artifact + ".exe")
    if not engine.is_file():
        parser.error("the bundled " + family.id + " Colibri engine is unavailable")
    if not family.has_gateway_adapter:
        parser.error("this model has no supported chat gateway")
    monitor_owner(args.owner_pid)
    env = os.environ.copy()
    env["MILO_HIDE_ENGINE_CONSOLE"] = "1"
    # A VK build still runs on the CPU when Vulkan is disabled. Normal GPU
    # installation/drivers are owned by Windows, not silently modified.
    env["COLI_VULKAN"] = "1" if args.backend == "vulkan" else "0"
    env["COLI_CUDA"] = "0"
    env["COLI_VK_SHADERS"] = str(runtime / "shaders")
    openai_server.ARCH = family.id
    print("[milo] loading local Colibri family=" + family.id +
          " backend=" + args.backend, flush=True)
    openai_server.serve(
        str(model), host="127.0.0.1", port=args.port,
        model_id=default_model_id(info), api_key=None,
        cap=None, max_tokens=0, engine=str(engine), env=env,
        family=family,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run_service())
