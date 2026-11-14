"""Opt-in, provenance-checked download of alternative local GGUF weights.

Abliterated weights are community derivatives, not a toggle and not bundled.
Requires a deliberate model-manager action, sufficient free disk space and
the Hugging Face published LFS SHA-256 metadata; no silent downloads.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

MODEL_REPO = "IamLucif3r/Qwen3-4B-Instruct-2507-Abliterated"
MODEL_FILENAME = "Qwen3-4B-Instruct-2507-Abliterated-Q4_K_M.gguf"
MODEL_PAGE = "https://huggingface.co/" + MODEL_REPO
API_TREE = "https://huggingface.co/api/models/" + MODEL_REPO + "/tree/main"
BASE_URL = MODEL_PAGE + "/resolve/main/"
MIN_SIZE = 1_000_000_000
MAX_SIZE = 6_000_000_000
HEADERS = {"User-Agent": "Milo-Local-Model-Manager/0.4"}
_SHA = re.compile(r"^[0-9a-fA-F]{64}$")


class ModelDownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelAsset:
    name: str
    size: int
    sha256: str
    url: str
    source: str = MODEL_PAGE


def model_asset_from_metadata(entries) -> ModelAsset:
    if not isinstance(entries, list):
        raise ModelDownloadError("unexpected model repository metadata")
    matching = [row for row in entries if
                isinstance(row, dict) and row.get("path") == MODEL_FILENAME]
    if len(matching) != 1:
        raise ModelDownloadError("matching abliterated model not published")
    obj = matching[0]
    lfs = obj.get("lfs", {})
    size = obj.get("size")
    digest = lfs.get("oid") if isinstance(lfs, dict) else None
    if digest is None and isinstance(lfs, dict):
        digest = lfs.get("sha256")
    if (not isinstance(size, int) or not MIN_SIZE <= size <= MAX_SIZE
            or not isinstance(digest, str) or not _SHA.fullmatch(digest)):
        raise ModelDownloadError(
            "Publisher did not provide verifiable model size and SHA-256; "
            "download refused")
    return ModelAsset(
        name=MODEL_FILENAME, size=size, sha256=digest.lower(),
        url=BASE_URL + quote(MODEL_FILENAME) + "?download=true")


def inspect_abliterated_release() -> ModelAsset:
    request = Request(API_TREE, headers=HEADERS)
    try:
        with urlopen(request, timeout=18) as response:
            data = response.read(1_000_001)
    except (OSError, HTTPError, URLError, TimeoutError) as exc:
        raise ModelDownloadError("could not query the model publisher") from exc
    if len(data) > 1_000_000:
        raise ModelDownloadError("publisher metadata too large")
    try:
        return model_asset_from_metadata(json.loads(data))
    except (ValueError, UnicodeError) as exc:
        raise ModelDownloadError("invalid publisher metadata") from exc


def download_abliterated(asset: ModelAsset, models_root: Path, *,
                        on_progress=None, cancel=None) -> Path:
    """Streaming and SHA-verified download, never overwrite existing models."""
    if asset.name != MODEL_FILENAME or not asset.url.startswith(BASE_URL):
        raise ModelDownloadError("unrecognised model download source")
    root = models_root.resolve(strict=True)
    folder = root / "Abliterated"
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / asset.name
    pending = folder / (asset.name + ".downloading")
    if destination.exists():
        raise ModelDownloadError(
            "model already installed; use Models to select/import it")
    if pending.exists():
        raise ModelDownloadError(
            "incomplete prior model download exists; inspect Models/Abliterated "
            "before retrying")
    if shutil.disk_usage(root).free < asset.size + 750 * 1024 * 1024:
        raise ModelDownloadError("not enough free space for the model and reserve")
    digest = hashlib.sha256()
    total = 0
    try:
        req = Request(asset.url, headers=HEADERS)
        with urlopen(req, timeout=60) as src, pending.open("xb") as out:
            while True:
                if cancel is not None and cancel.is_set():
                    raise ModelDownloadError("model download cancelled")
                chunk = src.read(512 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > asset.size:
                    raise ModelDownloadError("model download exceeds publisher file size")
                out.write(chunk)
                digest.update(chunk)
                if on_progress:
                    on_progress(total, asset.size)
            out.flush()
            os.fsync(out.fileno())
        if total != asset.size or digest.hexdigest() != asset.sha256:
            raise ModelDownloadError("model size or SHA-256 failed verification")
        pending.replace(destination)
        return destination
    except (OSError, HTTPError, URLError, TimeoutError) as exc:
        raise ModelDownloadError("model download failed; original model unchanged") from exc
    finally:
        pending.unlink(missing_ok=True)
