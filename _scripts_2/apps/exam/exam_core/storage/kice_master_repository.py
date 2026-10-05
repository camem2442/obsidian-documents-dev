"""KiceMasterRepository for persistent official KICE Master Question records.

Storage layout:
    library/kice/records/{question_id}.json

Invariants:
    1. Independent from job-scoped RecordRepository.
    2. Master Record Purity: Master records contain pure official question content.
       solution must be "" and relations.solution_ids must be [].
       Attempting to save a record with non-empty solution or solution_ids raises
       ValueError unless strip_knowledge=True is explicitly passed.
    3. Atomic JSON persistence via temporary file replacement.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord, validate_canonical_dict


def _atomic_json_save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".pending-master-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class KiceMasterRepository:
    """Repository for official KICE Master Question records in global library."""

    def __init__(self, library_root: Path) -> None:
        self._library_root = Path(library_root)

    @property
    def library_root(self) -> Path:
        return self._library_root

    @property
    def records_dir(self) -> Path:
        return self._library_root / "records"

    def get(self, question_id: str) -> CanonicalQuestionRecord:
        path = self.records_dir / f"{question_id}.json"
        if not path.is_file():
            raise ValueError(f"KICE Master Record '{question_id}' not found in {self.records_dir}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return CanonicalQuestionRecord.from_dict(data)

    def exists(self, question_id: str) -> bool:
        return (self.records_dir / f"{question_id}.json").is_file()

    def save(self, record: CanonicalQuestionRecord, strip_knowledge: bool = False) -> None:
        record_id = record.question.question_id
        if not record_id:
            raise ValueError("Cannot save CanonicalQuestionRecord without question_id")

        if strip_knowledge:
            record.solution = ""
            record.relations.solution_ids = []
        else:
            if record.solution:
                raise ValueError(
                    f"Master record purity violation: question '{record_id}' has non-empty solution "
                    f"({record.solution[:30]!r}...). Master records must not store solutions."
                )
            if record.relations.solution_ids:
                raise ValueError(
                    f"Master record purity violation: question '{record_id}' has solution_ids "
                    f"({record.relations.solution_ids}). Master records must not store solution_ids."
                )

        data = record.to_dict()
        errors = validate_canonical_dict(data)
        if errors:
            raise ValueError(f"Invalid CanonicalQuestionRecord '{record_id}': " + "; ".join(errors))

        path = self.records_dir / f"{record_id}.json"
        _atomic_json_save(path, data)

    def save_many(self, records: Iterable[CanonicalQuestionRecord], strip_knowledge: bool = False) -> None:
        for rec in records:
            self.save(rec, strip_knowledge=strip_knowledge)

    def get_many(self, question_ids: List[str]) -> Dict[str, CanonicalQuestionRecord]:
        results: Dict[str, CanonicalQuestionRecord] = {}
        for qid in question_ids:
            results[qid] = self.get(qid)
        return results

    def list_ids(self) -> List[str]:
        if not self.records_dir.exists():
            return []
        ids = []
        for p in self.records_dir.glob("*.json"):
            if not p.name.startswith("."):
                ids.append(p.stem)
        return sorted(ids)

    def list_records(self) -> List[CanonicalQuestionRecord]:
        return [self.get(qid) for qid in self.list_ids()]

    def delete(self, question_id: str) -> bool:
        path = self.records_dir / f"{question_id}.json"
        if path.is_file():
            path.unlink()
            return True
        return False
