"""RecordRepository for CanonicalQuestionRecord persistence."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord, validate_canonical_dict


def _atomic_json_save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".pending-rec-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class RecordRepository:
    def records_dir(self, job_dir: Path) -> Path:
        p = job_dir / "records"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def get(self, job_dir: Path, record_id: str) -> CanonicalQuestionRecord:
        path = job_dir / "records" / f"{record_id}.json"
        if not path.is_file():
            raise ValueError(f"Record '{record_id}' not found in {job_dir}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return CanonicalQuestionRecord.from_dict(data)

    def save(self, job_dir: Path, record: CanonicalQuestionRecord) -> None:
        record_id = record.question.question_id
        if not record_id:
            raise ValueError("Cannot save CanonicalQuestionRecord without question_id")
        data = record.to_dict()
        errors = validate_canonical_dict(data)
        if errors:
            raise ValueError(f"Invalid CanonicalQuestionRecord '{record_id}': " + "; ".join(errors))
        path = self.records_dir(job_dir) / f"{record_id}.json"
        _atomic_json_save(path, data)

    def get_many(self, job_dir: Path, record_ids: List[str]) -> Dict[str, CanonicalQuestionRecord]:
        results = {}
        for rid in record_ids:
            results[rid] = self.get(job_dir, rid)
        return results

    def save_many(self, job_dir: Path, records: List[CanonicalQuestionRecord]) -> None:
        for rec in records:
            self.save(job_dir, rec)
