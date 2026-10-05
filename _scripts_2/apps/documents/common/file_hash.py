#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mtime-aware SHA-256 cache (no job/baseline imports)."""

from __future__ import annotations

import hashlib
from pathlib import Path

_SHA_CACHE: dict[str, tuple[int, int, str]] = {}


def get_file_sha256(path: Path) -> str:
    try:
        st = path.expanduser().resolve().stat()
    except OSError:
        return ""

    key = str(path.expanduser().resolve())
    cached = _SHA_CACHE.get(key)
    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2]

    digest = hashlib.sha256()
    with open(key, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    sha = digest.hexdigest()
    _SHA_CACHE[key] = (st.st_mtime_ns, st.st_size, sha)
    return sha


def clear_sha_cache() -> None:
    _SHA_CACHE.clear()
