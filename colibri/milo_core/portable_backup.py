"""Encrypted, portable Milo backup archive (development foundation).

Only explicitly registered sources are exported. This does not modify a live
installation, migrate secrets out of Windows Credential Manager, or install
engines on another machine. A future desktop wizard must inventory all sources,
show coverage, arrange a destination, and rebind restored model paths.

Security: scrypt-derived AES-256-GCM; streaming encryption with ZIP64; no
unencrypted archive written during export; authenticated restore into a new
directory; source SHA-256 checks and symlink/directory escape rejection.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
from typing import BinaryIO, Mapping
import zipfile

MAGIC = b"MILOBAK1"
HEADER_BYTES = len(MAGIC) + 16 + 12
TAG_BYTES = 16
CHUNK = 1024 * 1024
SCHEMA_VERSION = 1
COMPONENTS = frozenset({
    "settings", "profiles", "avatars", "attachments", "models", "runtime",
    "plugins",
})
MAX_FILES = 100000
MAX_UNCOMPRESSED_BYTES = 1 << 43  # 8 TiB; includes very large local model files.


class BackupError(RuntimeError):
    """The bundle could not be safely created or restored."""


@dataclass(frozen=True)
class BundleSummary:
    components: tuple[str, ...]
    files: int
    bytes: int
    created_at: str


def _crypto():
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError as exc:
        raise BackupError("portable backup needs the optional 'cryptography' package") from exc
    return Cipher, algorithms, modes, Scrypt


def _key(passphrase: str, salt: bytes) -> bytes:
    if not isinstance(passphrase, str) or len(passphrase) < 12:
        raise ValueError("use a recovery passphrase of at least 12 characters")
    _, _, _, Scrypt = _crypto()
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def _normalise_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise BackupError("unsafe bundle member name")
    path = PurePosixPath(name)
    if (path.is_absolute() or any(p in {"..", ".", ""} for p in path.parts)
            or str(path) != name):
        raise BackupError("unsafe bundle member name")
    if len(path.parts) < 2 or path.parts[0] not in COMPONENTS | {"data"}:
        raise BackupError("unexpected bundle component")
    return name


def _file_iterator(root: Path, component: str):
    # Deterministic file order; never traverse symlinked directories.
    stack = [(root, Path())]
    while stack:
        directory, relative = stack.pop()
        children = sorted(directory.iterdir(), key=lambda p: p.name.casefold())
        for child in reversed(children):
            rel = relative / child.name
            if child.is_symlink():
                # Only linked model *files* can be copied by value. This catches
                # Colibri link-mode imports without reading arbitrary symlinked dirs.
                if component != "models" or not child.resolve(strict=True).is_file():
                    raise BackupError("unsupported symbolic link; export stopped")
                if child.suffix.lower() not in {".gguf", ".safetensors", ".bin"}:
                    raise BackupError("unapproved linked model file type")
                yield child, rel
            elif child.is_dir():
                stack.append((child, rel))
            elif child.is_file():
                yield child, rel
            else:
                raise BackupError("unsupported file type in backup source")


class _EncryptedWriter:
    def __init__(self, target: BinaryIO, encryptor):
        self.target = target
        self.encryptor = encryptor
        self.offset = 0

    def write(self, payload: bytes) -> int:
        self.target.write(self.encryptor.update(payload))
        self.offset += len(payload)
        return len(payload)

    def tell(self) -> int:
        return self.offset

    def flush(self) -> None:
        self.target.flush()


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    if not source.is_file() or source.is_symlink():
        raise BackupError("memory database is missing or unsafe")
    # SQLite backup API takes a transactionally consistent snapshot, including
    # committed writes from a live WAL database.
    try:
        uri = source.resolve(strict=True).as_uri() + "?mode=ro"
        # SQLite connection context managers commit, but do not CLOSE handles.
        # Explicit closing is essential before Windows can remove temp files.
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            with closing(sqlite3.connect(destination)) as snapshot:
                conn.backup(snapshot)
    except sqlite3.Error as exc:
        raise BackupError("could not snapshot SQLite memory") from exc


def export_bundle(
    destination: Path | str,
    passphrase: str,
    *,
    memory_db: Path | str,
    roots: Mapping[str, Path | str],
) -> BundleSummary:
    """Write a portable encrypted bundle without modifying registered sources.

    'roots' may include profiles/settings/avatars/attachments/models/runtime/plugins.
    Missing explicitly specified components cause a hard failure.
    """
    Cipher, algorithms, modes, _ = _crypto()
    target = Path(destination).expanduser()
    if target.exists() or target.is_symlink():
        raise BackupError("destination already exists; never overwrite a backup")
    source_db = Path(memory_db).expanduser()
    entries: list[tuple[Path, str]] = []
    for component, root_value in roots.items():
        if component not in COMPONENTS:
            raise ValueError("unknown backup component: " + str(component))
        root = Path(root_value).expanduser()
        if root.is_symlink() or not root.is_dir():
            raise BackupError("registered backup component unavailable: " + component)
        if target.resolve() == root.resolve() or root.resolve() in target.resolve().parents:
            raise BackupError("backup destination must be outside every backed-up folder")
        for path, relative in _file_iterator(root, component):
            entries.append((path, _normalise_name(
                component + "/" + relative.as_posix())))
            if len(entries) > MAX_FILES:
                raise BackupError("backup file-count limit exceeded")
    entries.sort(key=lambda item: item[1])
    if source_db.resolve() == target.resolve():
        raise BackupError("backup destination conflicts with memory database")
    target.parent.mkdir(parents=True, exist_ok=True)
    salt, nonce = os.urandom(16), os.urandom(12)
    header = MAGIC + salt + nonce
    key = _key(passphrase, salt)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    tmp_target = target.with_name(target.name + ".partial")
    if tmp_target.exists():
        raise BackupError("partial backup destination exists; remove it manually after inspection")
    file_rows = []
    total = 0
    created = datetime.now(timezone.utc).isoformat()
    try:
        with tempfile.TemporaryDirectory(prefix="milo-sqlite-snapshot-") as scratch:
            snapshot = Path(scratch) / "memory.sqlite3"
            _snapshot_sqlite(source_db, snapshot)
            entries.append((snapshot, "data/memory.sqlite3"))
            with open(tmp_target, "xb") as output:
                output.write(header)
                sink = _EncryptedWriter(output, encryptor)
                with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED,
                                     allowZip64=True) as archive:
                    for source, member in entries:
                        digest = sha256()
                        size = 0
                        with source.open("rb") as reader, archive.open(
                            member, "w", force_zip64=True
                        ) as writer:
                            while block := reader.read(CHUNK):
                                digest.update(block)
                                size += len(block)
                                total += len(block)
                                if total > MAX_UNCOMPRESSED_BYTES:
                                    raise BackupError("backup size limit exceeded")
                                writer.write(block)
                        file_rows.append({
                            "path": member, "size": size, "sha256": digest.hexdigest(),
                        })
                    manifest = {
                        "schema": SCHEMA_VERSION,
                        "created_at": created,
                        "components": sorted(set(roots) | {"data"}),
                        "files": file_rows,
                        "complete": False,  # No wizard inventory verified yet.
                        "note": "Restoration requires component path rebinding and engine checks.",
                    }
                    archive.writestr("manifest.json", json.dumps(
                        manifest, separators=(",", ":"), sort_keys=True,
                    ).encode("utf-8"))
                output.write(encryptor.finalize())
                output.write(encryptor.tag)
                output.flush()
                os.fsync(output.fileno())
        os.replace(tmp_target, target)
    except Exception:
        tmp_target.unlink(missing_ok=True)
        raise
    return BundleSummary(tuple(manifest["components"]), len(file_rows), total, created)


def _decrypt(archive_path: Path, passphrase: str, clear_zip: Path) -> None:
    Cipher, algorithms, modes, _ = _crypto()
    if not archive_path.is_file() or archive_path.is_symlink():
        raise BackupError("backup archive is not a regular file")
    try:
        with archive_path.open("rb") as source:
            header = source.read(HEADER_BYTES)
            if len(header) != HEADER_BYTES or not header.startswith(MAGIC):
                raise BackupError("unsupported or corrupt Milo backup header")
            key = _key(passphrase, header[len(MAGIC):len(MAGIC) + 16])
            size = archive_path.stat().st_size
            cipher_size = size - HEADER_BYTES - TAG_BYTES
            if cipher_size < 0:
                raise BackupError("truncated backup file")
            source.seek(size - TAG_BYTES)
            tag = source.read(TAG_BYTES)
            source.seek(HEADER_BYTES)
            decryptor = Cipher(algorithms.AES(key), modes.GCM(
                header[-12:], tag)).decryptor()
            decryptor.authenticate_additional_data(header)
            with clear_zip.open("xb") as output:
                remaining = cipher_size
                while remaining:
                    block = source.read(min(CHUNK, remaining))
                    if not block:
                        raise BackupError("truncated encrypted backup")
                    remaining -= len(block)
                    output.write(decryptor.update(block))
                output.write(decryptor.finalize())  # Verifies GCM authentication.
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError("backup authentication failed or file is damaged") from exc


def _validated_zip(clear_zip: Path):
    try:
        archive = zipfile.ZipFile(clear_zip, "r")
        names = archive.namelist()
        if len(names) > MAX_FILES + 1 or len(set(names)) != len(names):
            raise BackupError("duplicate or excessive backup entries")
        if names.count("manifest.json") != 1:
            raise BackupError("backup manifest missing")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema") != SCHEMA_VERSION or not isinstance(
            manifest.get("files"), list
        ):
            raise BackupError("unsupported backup schema")
        expected = {}
        size_total = 0
        for row in manifest["files"]:
            name = _normalise_name(row["path"])
            if name in expected or row["size"] < 0 or not isinstance(row["sha256"], str):
                raise BackupError("malformed backup manifest")
            size_total += row["size"]
            if size_total > MAX_UNCOMPRESSED_BYTES:
                raise BackupError("backup exceeds safe restore size")
            expected[name] = row
        if set(names) != set(expected) | {"manifest.json"}:
            raise BackupError("archive files differ from manifest")
        for info in archive.infolist():
            if info.is_dir() or info.flag_bits & 0x1:
                raise BackupError("unsupported compressed/encrypted archive entry")
            if info.filename in expected:
                if info.file_size != expected[info.filename]["size"]:
                    raise BackupError("archive file size differs from manifest")
        return archive, manifest, size_total
    except (KeyError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        raise BackupError("invalid or damaged backup manifest") from exc


def import_bundle(
    source: Path | str, passphrase: str, *, destination: Path | str,
) -> BundleSummary:
    """Authenticate and restore into a NEW directory; never overwrite current data.

    The caller must explicitly review and bind restored paths into a fresh
    installation before making it the active profile.
    """
    archive_path = Path(source).expanduser()
    target = Path(destination).expanduser()
    if target.exists() or target.is_symlink():
        raise BackupError("restore destination already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".milo-decrypt-", dir=target.parent) as tmp:
        clear_zip = Path(tmp) / "archive.zip"
        _decrypt(archive_path, passphrase, clear_zip)
        archive, manifest, total = _validated_zip(clear_zip)
        try:
            staging = Path(tmp) / "restored"
            staging.mkdir()
            for row in manifest["files"]:
                name = _normalise_name(row["path"])
                file_target = staging.joinpath(*PurePosixPath(name).parts)
                file_target.parent.mkdir(parents=True, exist_ok=True)
                digest = sha256()
                written = 0
                with archive.open(name) as reader, file_target.open("xb") as writer:
                    while block := reader.read(CHUNK):
                        digest.update(block)
                        written += len(block)
                        if written > row["size"]:
                            raise BackupError("restored file exceeds manifest size")
                        writer.write(block)
                if written != row["size"] or digest.hexdigest() != row["sha256"]:
                    raise BackupError("restored file hash does not match manifest")
            os.replace(staging, target)
        finally:
            archive.close()
    return BundleSummary(
        tuple(manifest["components"]), len(manifest["files"]),
        total, manifest["created_at"],
    )
