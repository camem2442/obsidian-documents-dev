"""ArtifactRepository for KS Learning Artifacts metadata persistence.

Storage layout:
    library/artifacts/records/{artifact_id}.json
    library/artifacts/path-index.json

Invariants:
    1. 100% Read-Only on source KS vault files.
    2. path-index.json maps source_path to stable UUID artifact_id.
    3. Re-scanning same source_path preserves artifact_id.
    4. Atomic JSON persistence via temporary file replacement.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from _scripts_2.apps.exam.exam_core.domain.artifact_models import ArtifactRecord


class ArtifactIndexCorruptionError(RuntimeError):
    """Raised when library/artifacts/path-index.json is corrupted."""
    pass


class ArtifactRecordCorruptionError(RuntimeError):
    """Raised when an artifact record is missing, malformed, or mismatches the path-index."""
    pass


def _atomic_json_save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".pending-art-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class ArtifactRepository:
    """Repository managing artifact records and path index under library/artifacts/."""

    def __init__(self, artifacts_root: Path) -> None:
        self._root = Path(artifacts_root)

    @property
    def root_dir(self) -> Path:
        return self._root

    @property
    def records_dir(self) -> Path:
        return self._root / "records"

    @property
    def path_index_file(self) -> Path:
        return self._root / "path-index.json"

    def _load_path_index(self) -> Dict[str, str]:
        if not self.path_index_file.is_file():
            return {}
        try:
            data = json.loads(self.path_index_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ArtifactIndexCorruptionError(
                    f"Corrupted path-index.json at {self.path_index_file}: expected JSON object, got {type(data).__name__}"
                )

            seen_ids = set()
            for k, v in data.items():
                if not isinstance(k, str) or not k.strip():
                    raise ArtifactIndexCorruptionError(f"path-index contains empty path key: {k!r}")
                if not isinstance(v, str) or not v.strip():
                    raise ArtifactIndexCorruptionError(f"path-index contains empty artifact_id for path: {k}")
                if v in seen_ids:
                    raise ArtifactIndexCorruptionError(
                        f"path-index contains duplicate artifact_id '{v}' assigned to multiple paths"
                    )
                seen_ids.add(v)

            return data
        except json.JSONDecodeError as exc:
            raise ArtifactIndexCorruptionError(
                f"Corrupted path-index.json at {self.path_index_file}: {exc}"
            ) from exc

    def _save_path_index(self, index: Dict[str, str]) -> None:
        _atomic_json_save(self.path_index_file, index)

    def get_by_id(self, artifact_id: str) -> Optional[ArtifactRecord]:
        path = self.records_dir / f"{artifact_id}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ArtifactRecordCorruptionError(
                    f"Corrupted artifact record at {path}: expected JSON object"
                )
            record = ArtifactRecord.from_dict(data)
            if record.artifact_id != artifact_id:
                raise ArtifactRecordCorruptionError(
                    f"Record ID mismatch: file '{path.name}' contains artifact_id '{record.artifact_id}'"
                )
            return record
        except (json.JSONDecodeError, ValueError) as exc:
            raise ArtifactRecordCorruptionError(
                f"Corrupted artifact record at {path}: {exc}"
            ) from exc

    def get_by_path(self, source_path: str) -> Optional[ArtifactRecord]:
        index = self._load_path_index()
        art_id = index.get(source_path)
        if not art_id:
            return None
        record = self.get_by_id(art_id)
        if record is None:
            raise ArtifactRecordCorruptionError(
                f"Path index references artifact_id '{art_id}' for '{source_path}', but record file is missing"
            )
        if record.source_path != source_path:
            raise ArtifactRecordCorruptionError(
                f"Bi-directional mismatch: index mapped '{source_path}' -> '{art_id}', but record has source_path '{record.source_path}'"
            )
        return record

    def register_or_update(
        self,
        source_path: str,
        content_sha256: str,
        file_size: int,
        title: str = "",
    ) -> ArtifactRecord:
        """Register a new artifact or update an existing one while guaranteeing bidirectional integrity."""
        if not source_path:
            raise ValueError("source_path cannot be empty")

        index = self._load_path_index()
        art_id = index.get(source_path)
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        if art_id:
            existing = self.get_by_id(art_id)
            if existing is None:
                raise ArtifactRecordCorruptionError(
                    f"Path index points to missing artifact record '{art_id}' for '{source_path}'. "
                    f"Refusing to generate a new UUID to prevent knowledge graph corruption."
                )
            if existing.source_path != source_path:
                raise ArtifactRecordCorruptionError(
                    f"Bi-directional path mismatch for '{art_id}': index has '{source_path}', record has '{existing.source_path}'"
                )

            if (
                existing.content_sha256 == content_sha256
                and existing.file_size == file_size
                and existing.title == (title or existing.title)
                and existing.present is True
            ):
                existing.last_seen_at = now_iso
                _atomic_json_save(self.records_dir / f"{art_id}.json", existing.to_dict())
                return existing

            # Content updated / restored: preserve artifact_id, update metadata
            updated = ArtifactRecord(
                artifact_id=art_id,
                source_path=source_path,
                content_sha256=content_sha256,
                file_size=file_size,
                title=title or existing.title,
                present=True,
                created_at=existing.created_at or now_iso,
                updated_at=now_iso,
                last_seen_at=now_iso,
                missing_since=None,
            )
            _atomic_json_save(self.records_dir / f"{art_id}.json", updated.to_dict())
            return updated

        # New artifact: Record-First, Index-Second write ordering
        new_id = f"art-{uuid.uuid4().hex}"
        record = ArtifactRecord(
            artifact_id=new_id,
            source_path=source_path,
            content_sha256=content_sha256,
            file_size=file_size,
            title=title or Path(source_path).stem,
            present=True,
            created_at=now_iso,
            updated_at=now_iso,
            last_seen_at=now_iso,
            missing_since=None,
        )
        # 1. Write record JSON first
        _atomic_json_save(self.records_dir / f"{new_id}.json", record.to_dict())
        # 2. Write path-index JSON second
        index[source_path] = new_id
        self._save_path_index(index)
        return record

    def mark_missing(self, source_path: str) -> Optional[ArtifactRecord]:
        """Mark an artifact as missing/deleted without removing its UUID from path-index."""
        index = self._load_path_index()
        art_id = index.get(source_path)
        if not art_id:
            return None

        existing = self.get_by_id(art_id)
        if existing is None:
            raise ArtifactRecordCorruptionError(
                f"Cannot mark missing: path index references missing record '{art_id}' for '{source_path}'"
            )
        if not existing.present:
            return existing

        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        updated = ArtifactRecord(
            artifact_id=art_id,
            source_path=source_path,
            content_sha256=existing.content_sha256,
            file_size=existing.file_size,
            title=existing.title,
            present=False,
            created_at=existing.created_at,
            updated_at=now_iso,
            last_seen_at=existing.last_seen_at,
            missing_since=now_iso,
        )
        _atomic_json_save(self.records_dir / f"{art_id}.json", updated.to_dict())
        return updated

    def list_all(self, include_missing: bool = False) -> List[ArtifactRecord]:
        index = self._load_path_index()
        records = []
        for path, art_id in index.items():
            r = self.get_by_id(art_id)
            if r is None:
                raise ArtifactRecordCorruptionError(
                    f"Path index references missing artifact record '{art_id}' for '{path}'"
                )
            if r.source_path != path:
                raise ArtifactRecordCorruptionError(
                    f"Bi-directional path mismatch: index mapped '{path}' -> '{art_id}', but record has '{r.source_path}'"
                )
            if include_missing or r.present:
                records.append(r)
        return sorted(records, key=lambda x: x.source_path)

    def validate_integrity(self) -> None:
        """Enforce strict bidirectional referential integrity between path-index and records."""
        index = self._load_path_index()
        seen_art_ids = set()

        # 1. Forward check: index -> records
        for path, art_id in index.items():
            rec = self.get_by_id(art_id)
            if rec is None:
                raise ArtifactRecordCorruptionError(
                    f"Integrity check failed: path-index references missing record '{art_id}' for '{path}'"
                )
            if rec.source_path != path:
                raise ArtifactRecordCorruptionError(
                    f"Integrity check failed: path mismatch for '{art_id}'. Index has '{path}', record has '{rec.source_path}'"
                )
            seen_art_ids.add(art_id)

        # 2. Reverse check: records -> index (Orphan check)
        if self.records_dir.exists():
            for rec_file in self.records_dir.glob("*.json"):
                if rec_file.name.startswith(".") or rec_file.name.startswith(".pending-"):
                    continue
                file_art_id = rec_file.stem
                if file_art_id not in seen_art_ids:
                    raise ArtifactRecordCorruptionError(
                        f"Integrity check failed: orphan record found at '{rec_file}' (not referenced in path-index.json)"
                    )


