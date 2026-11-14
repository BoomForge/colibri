"""Optional official-architecture Qwen3-4B Thinking GGUF for specialist Research.

Never bundled or downloaded silently. This is a community quantisation of an
official Qwen3 model family that Colibri's registered qwen36 engine supports in
principle. Actual performance/format must still be tested on owner's Windows.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import quote

REPO = "bartowski/Qwen_Qwen3-4B-Thinking-2507-GGUF"
FILENAME = "Qwen3-4B-Thinking-2507-Q4_K_M.gguf"
MODEL_PAGE = "https://huggingface.co/" + REPO
TREE = "https://huggingface.co/api/models/" + REPO + "/tree/main"
URL = MODEL_PAGE + "/resolve/main/" + quote(FILENAME)
SHA = re.compile(r"^[0-9a-f]{64}$")


class ResearchModelError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResearchAsset:
    size: int
    digest: str


def validated_asset_metadata(rows) -> ResearchAsset:
    candidates = [item for item in rows
                  if isinstance(item, dict) and item.get("path") == FILENAME]
    if len(candidates) != 1:
        raise ResearchModelError("Requested Qwen3 Thinking Q4_K_M not published")
    item = candidates[0]
    lfs = item.get("lfs")
    digest = lfs.get("oid") if isinstance(lfs, dict) else None
    size = item.get("size")
    if (not isinstance(size, int) or not 1_800_000_000 < size < 4_000_000_000 or
            not isinstance(digest, str) or not SHA.fullmatch(digest.lower())):
        raise ResearchModelError("Publisher omitted trusted size/SHA-256 metadata")
    return ResearchAsset(size, digest.lower())


def inspect_research_asset() -> ResearchAsset:
    request = Request(TREE, headers={"User-Agent": "Milo/0.8-model-manager"})
    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read(800_001)
    except (OSError, HTTPError, URLError) as exc:
        raise ResearchModelError("Cannot inspect the official model-family publisher") from exc
    if len(payload) > 800_000:
        raise ResearchModelError("Model metadata too large")
    try:
        return validated_asset_metadata(json.loads(payload))
    except (ValueError, TypeError) as exc:
        raise ResearchModelError("Malformed model metadata") from exc


def download_research_asset(root: Path, asset: ResearchAsset,
                            *, progress=None, cancel=None) -> Path:
    root = root.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ResearchModelError("Models folder unavailable")
    folder = root / "Research"
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink():
        raise ResearchModelError("Research models folder cannot be symlinked")
    target = folder / FILENAME
    staging = folder / (FILENAME + ".downloading")
    if target.exists() or staging.exists():
        raise ResearchModelError("Model already downloaded or has incomplete staging file")
    if shutil.disk_usage(root).free < asset.size + 750_000_000:
        raise ResearchModelError("Insufficient disk space for safe model download")
    sha = hashlib.sha256()
    count = 0
    try:
        req = Request(URL, headers={"User-Agent": "Milo/0.8-model-manager"})
        with urlopen(req, timeout=70) as src, staging.open("xb") as out:
            while True:
                if cancel and cancel.is_set():
                    raise ResearchModelError("Download cancelled")
                chunk = src.read(512 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > asset.size:
                    raise ResearchModelError("Publisher download size mismatch")
                sha.update(chunk)
                out.write(chunk)
                if progress:
                    progress(count, asset.size)
            out.flush()
            os.fsync(out.fileno())
        if count != asset.size or sha.hexdigest() != asset.digest:
            raise ResearchModelError("Downloaded model SHA-256 or size mismatch")
        staging.replace(target)
        return target
    except (OSError, HTTPError, URLError) as exc:
        raise ResearchModelError("Model download failed; existing models unchanged") from exc
    finally:
        staging.unlink(missing_ok=True)
