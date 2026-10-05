"""JobRepository for pure JobManifest persistence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from ..domain.job_manifest import JobManifest, RUNTIME_KEYS
from .json_io import atomic_json_save


class JobRepository:
    def load_manifest(self, job_dir: Path) -> JobManifest:
        path = job_dir / "job.json"
        if not path.is_file():
            raise ValueError(f"Job manifest not found in {job_dir}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return JobManifest.from_dict(data)

    def save_manifest(self, job_dir: Path, manifest: JobManifest | Dict[str, Any]) -> None:
        if isinstance(manifest, JobManifest):
            data = manifest.to_dict()
        else:
            data = dict(manifest)
        data["storage_version"] = 2
        data.pop("items", None)
        for key in RUNTIME_KEYS:
            data.pop(key, None)
        path = job_dir / "job.json"
        atomic_json_save(path, data)

