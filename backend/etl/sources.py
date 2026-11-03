"""Locate and open source files of a downloaded release without modifying them.

A release directory holds files exactly as published: plain CSV/TSV, gzip
files, or tar archives. Archive members are streamed, never extracted to disk.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import tarfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterator

from backend.config import MANIFEST_PATH, RAW_DIR

ARCHIVE_SUFFIXES = (".tgz", ".tar.gz", ".tar")


@dataclass(frozen=True)
class SourceRef:
    archive: Path          # file on disk
    member: str            # member path inside the archive, '' if not an archive

    @property
    def archive_rel(self) -> str:
        try:
            return self.archive.resolve().relative_to(RAW_DIR.resolve()).as_posix()
        except ValueError:
            return self.archive.as_posix()

    @property
    def display(self) -> str:
        return f"{self.archive_rel}!{self.member}" if self.member else self.archive_rel


def _is_archive(p: Path) -> bool:
    return p.name.endswith(ARCHIVE_SUFFIXES)


def find_source(release_dir: Path, basename: str) -> SourceRef | None:
    """Find `basename` as a plain file or as an archive member in release_dir."""
    direct = release_dir / basename
    if direct.is_file():
        return SourceRef(direct, "")
    for arch in sorted(p for p in release_dir.iterdir() if p.is_file() and _is_archive(p)):
        with tarfile.open(arch) as tf:
            for m in tf.getmembers():
                name = Path(m.name).name
                # skip macOS resource forks such as ._README
                if m.isfile() and name == basename and not name.startswith("._"):
                    return SourceRef(arch, m.name)
    return None


@contextmanager
def open_source(ref: SourceRef) -> Iterator[IO[bytes]]:
    if ref.member:
        with tarfile.open(ref.archive) as tf:
            fh = tf.extractfile(ref.member)
            if fh is None:
                raise FileNotFoundError(ref.display)
            yield fh
    elif ref.archive.name.endswith(".gz"):
        with gzip.open(ref.archive, "rb") as fh:
            yield fh
    else:
        with open(ref.archive, "rb") as fh:
            yield fh


def file_sha256(path: Path) -> str:
    """SHA-256 of the file on disk; reuses the download manifest when it matches."""
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["files"]
        rel = SourceRef(path, "").archive_rel
        entry = manifest.get(rel)
        if entry and entry["size_bytes"] == path.stat().st_size:
            return entry["sha256"]
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def manifest_url(path: Path) -> str | None:
    if not MANIFEST_PATH.exists():
        return None
    entry = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["files"].get(SourceRef(path, "").archive_rel)
    return entry["url"] if entry else None
