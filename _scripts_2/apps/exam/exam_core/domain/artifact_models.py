"""Data models for KS Learning Artifacts in Exam Processor P2-C.

These models define legacy KS vault note records and multi-file bundles stored in:
    library/artifacts/records/{artifact_id}.json
    library/artifacts/path-index.json

Invariants:
    1. 100% Read-Only on source KS vault files.
    2. Path index guarantees immutable artifact_id (UUID) across re-scans.
    3. Content modifications update content_sha256 while preserving artifact_id.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ArtifactRecord:
    artifact_id: str
    source_path: str               # relative path, e.g. "KS/3 영어/.../260933 시험장.md"
    content_sha256: str
    file_size: int
    title: str = ""
    present: bool = True
    created_at: str = ""
    updated_at: str = ""
    last_seen_at: str = ""
    missing_since: Optional[str] = None

    def validate(self) -> None:
        """Enforce strict field types and format invariants for ArtifactRecord."""
        if not isinstance(self.artifact_id, str) or not self.artifact_id.strip():
            raise ValueError("artifact_id must be a non-empty string")
        if not isinstance(self.source_path, str) or not self.source_path.strip():
            raise ValueError("source_path must be a non-empty string")
        if not isinstance(self.content_sha256, str) or not re.match(r"^[0-9a-f]{64}$", self.content_sha256):
            raise ValueError(f"content_sha256 must be a 64-char lowercase hex string, got {self.content_sha256!r}")
        if not isinstance(self.file_size, int) or self.file_size < 0:
            raise ValueError(f"file_size must be a non-negative int, got {self.file_size!r}")
        if not isinstance(self.present, bool):
            raise ValueError(f"present must be a bool, got {type(self.present).__name__}")
        if self.missing_since is not None and not isinstance(self.missing_since, str):
            raise ValueError("missing_since must be a string or None")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ArtifactRecord:
        if not isinstance(data, dict):
            raise ValueError("ArtifactRecord.from_dict requires a dict")

        present_val = data.get("present", True)
        if not isinstance(present_val, bool):
            raise ValueError(f"ArtifactRecord field 'present' must be bool, got {type(present_val).__name__}")

        file_size_val = data.get("file_size")
        if not isinstance(file_size_val, int) or file_size_val < 0:
            raise ValueError(f"ArtifactRecord field 'file_size' must be a non-negative int, got {file_size_val!r}")

        obj = cls(
            artifact_id=data.get("artifact_id", ""),
            source_path=data.get("source_path", ""),
            content_sha256=data.get("content_sha256", ""),
            file_size=file_size_val,
            title=data.get("title", ""),
            present=present_val,
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            last_seen_at=data.get("last_seen_at", ""),
            missing_since=data.get("missing_since"),
        )
        obj.validate()
        return obj


@dataclass
class ArtifactBundle:
    bundle_name: str
    anchor: ArtifactRecord
    siblings: List[ArtifactRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_name": self.bundle_name,
            "anchor": self.anchor.to_dict(),
            "siblings": [s.to_dict() for s in self.siblings],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ArtifactBundle:
        anchor_data = data.get("anchor") or {}
        anchor = ArtifactRecord.from_dict(anchor_data)
        siblings = [ArtifactRecord.from_dict(s) for s in data.get("siblings", [])]
        return cls(
            bundle_name=data.get("bundle_name", ""),
            anchor=anchor,
            siblings=siblings,
        )
