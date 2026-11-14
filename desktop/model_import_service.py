"""One-time, private, zero-requantization GGUF metadata import.

The original GGUF stays in Models (no multi-gigabyte copy). Colibri's own
gguf_import.py creates the complete config/tokenizer/gguf.json sidecar folder.
The import runs as a hidden Milo.exe worker; it is not a separate Python install.
"""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import shutil
import sys
import uuid

from colibri.milo_core.local_engine import (
    gguf_target, install_root, managed_models_root, model_valid,
)


def import_gguf(source: str, *, models_root: Path | None = None, runtime: Path | None = None) -> Path:
    root = (models_root or managed_models_root()).resolve(strict=True)
    file = Path(source).resolve(strict=True)
    target = gguf_target(root, file)
    if target.exists():
        if model_valid(target):
            return target
        raise ValueError("an incomplete model folder already exists: " + str(target))
    toolroot = runtime or (install_root() / "Runtime")
    tool = toolroot / "tools" / "gguf_import.py"
    if not tool.is_file() or not (toolroot / "tools" / "gguf_reader.py").is_file():
        raise RuntimeError("the bundled Colibri GGUF import tools are missing")
    staging = target.parent / ("." + target.name + ".pending-" + uuid.uuid4().hex[:8])
    if staging.exists():
        raise FileExistsError("model import staging path already exists")
    previous = sys.argv
    try:
        sys.argv = [str(tool), str(file), str(staging), "--mode", "link"]
        # First-party tool reads GGUF metadata and creates complete Colibri
        # checkpoint sidecars. No weights are modified or duplicated.
        runpy.run_path(str(tool), run_name="__main__")
        if not model_valid(staging):
            raise RuntimeError("generated Colibri model folder was incomplete")
        staging.rename(target)
        return target
    finally:
        sys.argv = previous
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def worker_main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise ValueError("GGUF import requires exactly one model filename")
    output = import_gguf(argv[0])
    print("Model imported successfully:", output, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(worker_main(sys.argv[1:]))
