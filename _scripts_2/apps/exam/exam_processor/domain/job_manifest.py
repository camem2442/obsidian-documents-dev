"""JobManifest dataclass for v2 job metadata and ordering."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


RUNTIME_KEYS = {
    "task",
    "queue",
    "queue_history",
    "ai_log",
    "ai_progress",
    "sample_review",
    "bulk_approval",
}


@dataclass
class JobManifest:
    id: str
    source_id: str
    source: str
    track: str
    format: str
    record_ids: List[str]
    documents: Dict[str, str]
    storage_version: int = 2
    warnings: List[str] = field(default_factory=list)
    subject: str = ""
    source_type: str = ""
    solution_format: str = ""
    last_export: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # Pull format-specific metadata to top level if needed for legacy compatibility
        meta = data.pop("metadata", {})
        if isinstance(meta, dict):
            for k, v in meta.items():
                if k not in data and k not in RUNTIME_KEYS:
                    data[k] = v
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> JobManifest:
        data_copy = dict(data)
        standard_fields = {
            "id", "source_id", "source", "track", "format", "record_ids",
            "documents", "storage_version", "warnings", "subject",
            "source_type", "solution_format", "last_export",
        }
        metadata = {
            k: v for k, v in data_copy.items()
            if k not in standard_fields and k not in RUNTIME_KEYS and k != "items"
        }
        return cls(
            id=data_copy.get("id", ""),
            source_id=data_copy.get("source_id", ""),
            source=data_copy.get("source", ""),
            track=data_copy.get("track", ""),
            format=data_copy.get("format", ""),
            record_ids=list(data_copy.get("record_ids", [])),
            documents=dict(data_copy.get("documents", {})),
            storage_version=int(data_copy.get("storage_version", 2)),
            warnings=list(data_copy.get("warnings", [])),
            subject=data_copy.get("subject", ""),
            source_type=data_copy.get("source_type", ""),
            solution_format=data_copy.get("solution_format", ""),
            last_export=data_copy.get("last_export", ""),
            metadata=metadata,
        )

