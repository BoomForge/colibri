"""First-party, hash-verified application and module updates from GitHub Releases.

Milo never downloads executable updates from arbitrary user-configured URLs.
An owner-visible confirmation is required before installation; this module only
downloads and verifies bytes. Publishing a release is separate from building CI.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import os
import shutil
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CURRENT_VERSION = "0.9.5"
REPOSITORY = "BoomForge/colibri"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=20"
MAX_INSTALLER = 500 * 1024 * 1024
MAX_MODULE = 70 * 1024 * 1024
UA = "Milo-Personal-Assistant-Updater/0.9"
_SEMVER = re.compile(r"^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$")
_SAFE_ASSET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,120}$")


class UpdateError(RuntimeError):
    pass


def version_parts(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not _SEMVER.fullmatch(value):
        raise UpdateError("invalid update version")
    return tuple(int(x) for x in value.split("."))


def newer(candidate: str, installed: str = CURRENT_VERSION) -> bool:
    return version_parts(candidate) > version_parts(installed)


def _github_error(exc: Exception) -> str:
    """Actionable but privacy-safe failures; never log URLs or credentials."""
    if isinstance(exc, HTTPError):
        code = exc.code
        if code in (403, 429):
            remaining = exc.headers.get("X-RateLimit-Remaining", "")
            if remaining == "0":
                return ("GitHub API rate limit reached (HTTP " + str(code) +
                        "). Try again later or open the releases webpage.")
            return ("GitHub refused the updater request (HTTP " + str(code) +
                    "). Check firewall/proxy filtering of api.github.com.")
        if code == 404:
            return ("GitHub returned HTTP 404 for the pinned Milo repository. "
                    "Check that github.com and api.github.com are accessible.")
        if code >= 500:
            return ("GitHub's release API is temporarily unavailable (HTTP " +
                    str(code) + "). Retry later.")
        return "GitHub release API returned HTTP " + str(code)
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, ssl.SSLError):
        return ("TLS certificate verification failed. Check your Windows clock, "
                "security software and trusted certificates; do not disable TLS checks.")
    if isinstance(reason, socket.gaierror):
        return ("DNS could not resolve api.github.com. Check your internet "
                "connection and DNS settings.")
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return ("Connection to api.github.com timed out. Check your connection, "
                "firewall or VPN, or open releases in a browser.")
    if isinstance(reason, ConnectionRefusedError):
        return ("A network/proxy connection refused the GitHub request. "
                "Check VPN, proxy and firewall settings.")
    return ("The updater could not connect to api.github.com securely. "
            "Try the releases webpage in your browser; no update was installed.")


def _is_temporary(exc: Exception) -> bool:
    if isinstance(exc, HTTPError):
        return exc.code in (429, 500, 502, 503, 504)
    return isinstance(exc, (URLError, TimeoutError, OSError))


def _json_https(url: str, *, max_bytes: int = 1024 * 1024):
    # Match a fixed repo path, not an arbitrary prefix that could include
    # another host or unexpected endpoint.
    if url != RELEASES_API:
        raise UpdateError("untrusted release API address")
    request = Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    for attempt in range(2):
        try:
            with urlopen(request, timeout=15) as response:
                content = response.read(max_bytes + 1)
            break
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            if attempt == 0 and _is_temporary(exc):
                time.sleep(0.5)
                continue
            raise UpdateError(_github_error(exc)) from exc
    if len(content) > max_bytes:
        raise UpdateError("GitHub response exceeded size limit")
    try:
        return json.loads(content)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise UpdateError("GitHub Releases returned invalid JSON") from exc


def latest_milo_release() -> dict | None:
    items = _json_https(RELEASES_API)
    if not isinstance(items, list):
        raise UpdateError("unexpected GitHub Releases response")
    options = []
    for item in items:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        tag = item.get("tag_name", "")
        if isinstance(tag, str) and tag.startswith("milo-v"):
            try:
                ver = tag[6:]
                version_parts(ver)
            except UpdateError:
                continue
            options.append((version_parts(ver), item))
    return max(options, default=((), None), key=lambda pair: pair[0])[1]


def release_version(release: dict) -> str:
    tag = release.get("tag_name", "")
    if not isinstance(tag, str) or not tag.startswith("milo-v"):
        raise UpdateError("unexpected release tag")
    version = tag[6:]
    version_parts(version)
    return version


def _expected_release_path(release: dict, name: str) -> str:
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not re.fullmatch(r"milo-v\d+\.\d+\.\d+", tag):
        raise UpdateError("unrecognised release tag")
    return f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    size: int
    sha256: str
    url: str


def get_release_asset(release: dict, name: str, *, max_bytes: int) -> ReleaseAsset:
    if not isinstance(name, str) or not _SAFE_ASSET.fullmatch(name):
        raise UpdateError("invalid release asset name")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("release has no downloadable assets")
    matching = [x for x in assets if isinstance(x, dict) and x.get("name") == name]
    if len(matching) != 1:
        raise UpdateError(f"release asset unavailable: {name}")
    item = matching[0]
    digest = item.get("digest", "")
    size = item.get("size")
    url = item.get("browser_download_url")
    if (not isinstance(digest, str) or not digest.startswith("sha256:")
            or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)
            or not isinstance(size, int) or not 0 < size <= max_bytes
            or not isinstance(url, str) or url != _expected_release_path(release, name)):
        raise UpdateError("download metadata failed integrity/security checks")
    return ReleaseAsset(name=name, size=size, sha256=digest[7:].lower(), url=url)


def installer_asset(release: dict) -> ReleaseAsset:
    version = release_version(release)
    return get_release_asset(release, f"Milo-Setup-{version}.exe", max_bytes=MAX_INSTALLER)


def download_verified(asset: ReleaseAsset, destination: Path, *,
                      progress=None) -> Path:
    """Atomic download, mandatory exact SHA-256, no execution side effects."""
    directory = destination.parent
    directory.mkdir(parents=True, exist_ok=True)
    if destination.name != asset.name:
        raise UpdateError("destination filename does not match release asset")
    pending = destination.with_suffix(destination.suffix + ".download")
    if pending.exists():
        pending.unlink()
    request = Request(asset.url, headers={"User-Agent": UA})
    digest = hashlib.sha256()
    received = 0
    try:
        with urlopen(request, timeout=45) as response, pending.open("xb") as output:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > asset.size:
                    raise UpdateError("download size exceeds GitHub release metadata")
                digest.update(chunk)
                output.write(chunk)
                if progress:
                    progress(received, asset.size)
            output.flush()
            os.fsync(output.fileno())
        if received != asset.size or digest.hexdigest() != asset.sha256:
            raise UpdateError("download SHA-256 or length mismatch")
        pending.replace(destination)
        return destination
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise UpdateError("update download interrupted") from exc
    finally:
        pending.unlink(missing_ok=True)


def inspect_module_catalog(release: dict) -> list[dict]:
    asset = get_release_asset(release, "milo-modules.json", max_bytes=256 * 1024)
    request = Request(asset.url, headers={"User-Agent": UA})
    try:
        with urlopen(request, timeout=15) as response:
            data = response.read(asset.size + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise UpdateError("module catalog unavailable") from exc
    if len(data) != asset.size or hashlib.sha256(data).hexdigest() != asset.sha256:
        raise UpdateError("module catalog checksum mismatch")
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise UpdateError("invalid module catalog") from exc
    if not isinstance(obj, dict) or obj.get("schema") != 1 or not isinstance(obj.get("modules"), list):
        raise UpdateError("unsupported module catalog schema")
    if len(obj["modules"]) > 80:
        raise UpdateError("too many published modules")
    results = []
    for module in obj["modules"]:
        if not isinstance(module, dict):
            raise UpdateError("invalid module catalog entry")
        module_id, version, filename = (module.get(k) for k in ("id", "version", "asset"))
        if (not isinstance(module_id, str)
                or not re.fullmatch(r"[a-z][a-z0-9_-]{2,48}", module_id)
                or not isinstance(filename, str)
                or not _SAFE_ASSET.fullmatch(filename)):
            raise UpdateError("invalid module identifier/asset")
        version_parts(version)
        if not isinstance(module.get("name"), str) or len(module["name"]) > 70:
            raise UpdateError("invalid module display name")
        results.append(module)
    return results
