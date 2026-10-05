"""KsVaultScanner — Read-Only inventory scanner for legacy KS vault notes.

Scans markdown files in KS/ vault and registers artifact records in ArtifactRepository.

Invariants:
    1. 100% Read-Only on source KS vault files (no edit, move, rename, or frontmatter mutation).
    2. Preflight Integrity Check: Verifies ArtifactRepository integrity before scanning.
    3. Consistent Exclusion Policy: 00 미분류 and 00 directories are strictly excluded from both discovery and reconciliation.
    4. Does NOT perform KICE matching or candidate scoring (deferred to P2-D).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional

from _scripts_2.apps.exam.exam_core.storage.artifact_repository import ArtifactRepository
from _scripts_2.apps.exam.exam_core.domain.artifact_models import ArtifactRecord


def _compute_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_excluded_path(path_str: str) -> bool:
    """Consistent exclusion filter for hidden files and unclassified folders."""
    p = Path(path_str)
    if p.name.startswith(".") or any(part.startswith(".") for part in p.parts):
        return True
    if any(part in ("00 미분류", "00") for part in p.parts):
        return True
    return False


class KsVaultScanner:
    """Scanner for indexing legacy KS vault notes into ArtifactRepository."""

    def __init__(
        self,
        artifact_repo: ArtifactRepository,
        repo_root: Optional[Path] = None,
    ) -> None:
        self._repo = artifact_repo
        self._repo_root = repo_root

    def scan_directory(self, ks_dir: Path) -> List[ArtifactRecord]:
        """Scan a directory containing KS markdown files (e.g., KS/ vault) and reconcile missing files."""
        # Preflight integrity validation to fail-closed on corrupted index or orphan records from crashes
        self._repo.validate_integrity()

        if not ks_dir.exists():
            return []

        seen_paths = set()
        records = []
        for p in ks_dir.rglob("*.md"):
            if _is_excluded_path(p.as_posix()):
                continue

            rel_path = (
                p.relative_to(self._repo_root).as_posix()
                if self._repo_root and p.is_relative_to(self._repo_root)
                else p.as_posix()
            )
            seen_paths.add(rel_path)

            content_sha = _compute_sha256(p)
            file_size = p.stat().st_size
            title = p.stem

            record = self._repo.register_or_update(
                source_path=rel_path,
                content_sha256=content_sha,
                file_size=file_size,
                title=title,
            )
            records.append(record)

        # Reconcile missing files within the scanned directory scope
        scope_prefix = (
            ks_dir.relative_to(self._repo_root).as_posix()
            if self._repo_root and ks_dir.is_relative_to(self._repo_root)
            else ks_dir.as_posix()
        )
        if not scope_prefix.endswith("/"):
            scope_prefix = f"{scope_prefix}/"

        for existing in self._repo.list_all(include_missing=False):
            if _is_excluded_path(existing.source_path):
                continue
            if (existing.source_path.startswith(scope_prefix) or existing.source_path == scope_prefix.rstrip("/")) and existing.source_path not in seen_paths:
                self._repo.mark_missing(existing.source_path)

        return sorted(records, key=lambda r: r.source_path)
