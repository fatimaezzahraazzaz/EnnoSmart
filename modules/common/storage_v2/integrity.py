from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO, Iterable


BLOCK_SIZE = 1024 * 1024


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(BLOCK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_stream(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    position = None
    try:
        position = handle.tell()
    except Exception:
        pass
    for block in iter(lambda: handle.read(BLOCK_SIZE), b""):
        digest.update(block)
    if position is not None:
        try:
            handle.seek(position)
        except Exception:
            pass
    return digest.hexdigest()


def sha256_chunks(chunks: Iterable[bytes]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk)
    return digest.hexdigest()


def validate_sha256(value: str) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("SHA-256 invalide.")
    return digest

