"""OriginIndex — derived index mapping KICE Origin Key to Master question_id.

Index layout:
    library/kice/indexes/origin_index.json

Format:
    {
      "schema_version": "1.0",
      "entries": {
        "kice:2026:09:math:prob_stat::29": "q-uuid-1",
        "kice:2026:09:math:calculus::29": "q-uuid-2"
      }
    }
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord
from _scripts_2.apps.exam.exam_core.storage.kice_master_repository import KiceMasterRepository


class OriginCollisionError(ValueError):
    """Raised when two different records share the exact same canonical Origin Key."""
    pass


def _atomic_json_save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".pending-idx-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class OriginIndex:
    """In-memory and persistent derived index for Origin Key -> question_id."""

    def __init__(self, index_path: Optional[Path] = None) -> None:
        self._index_path = index_path
        self._key_to_id: Dict[str, str] = {}
        self._id_to_key: Dict[str, str] = {}
        if index_path and index_path.is_file():
            self.load(index_path)

    @property
    def index_path(self) -> Optional[Path]:
        return self._index_path

    def __len__(self) -> int:
        return len(self._key_to_id)

    def __contains__(self, origin_key: str) -> bool:
        return origin_key in self._key_to_id

    def clear(self) -> None:
        self._key_to_id.clear()
        self._id_to_key.clear()

    def add(self, origin_key: str, question_id: str) -> None:
        """Add an origin key mapping. Raises OriginCollisionError if key conflicts."""
        if not origin_key:
            raise ValueError("origin_key cannot be empty")
        if not question_id:
            raise ValueError("question_id cannot be empty")

        existing_id = self._key_to_id.get(origin_key)
        if existing_id is not None and existing_id != question_id:
            raise OriginCollisionError(
                f"Origin key collision for '{origin_key}': "
                f"existing question '{existing_id}' conflicts with new question '{question_id}'"
            )

        existing_key = self._id_to_key.get(question_id)
        if existing_key is not None and existing_key != origin_key:
            raise OriginCollisionError(
                f"Question ID collision for '{question_id}': "
                f"existing origin key '{existing_key}' conflicts with new origin key '{origin_key}'"
            )

        self._key_to_id[origin_key] = question_id
        self._id_to_key[question_id] = origin_key

    def remove(self, question_id: str) -> Optional[str]:
        """Remove question from index. Returns removed origin key if found."""
        origin_key = self._id_to_key.pop(question_id, None)
        if origin_key:
            self._key_to_id.pop(origin_key, None)
        return origin_key

    def get(self, origin_key: str) -> Optional[str]:
        """Look up question_id by exact origin key."""
        return self._key_to_id.get(origin_key)

    def get_key(self, question_id: str) -> Optional[str]:
        """Look up origin key by question_id."""
        return self._id_to_key.get(question_id)

    def find(
        self,
        academic_year: str,
        session: str,
        subject: str,
        question_number: str,
        track: str = "",
        exam_form: str = "",
        authority: str = "kice",
    ) -> Optional[str]:
        """Convenience lookup by exact component tuple."""
        auth = authority.strip().lower()
        year = str(academic_year).strip()
        sess = str(session).strip()
        if sess.isdigit() and len(sess) == 1:
            sess = f"0{sess}"
        subj = subject.strip().lower()
        trk = track.strip().lower()
        form = exam_form.strip().lower()
        q_num = str(question_number).strip()

        key = f"{auth}:{year}:{sess}:{subj}:{trk}:{form}:{q_num}"
        return self.get(key)

    def find_candidates(
        self,
        *,
        authority: Optional[str] = "kice",
        academic_year: Optional[str] = None,
        session: Optional[str] = None,
        subject: Optional[str] = None,
        question_number: Optional[str] = None,
        track: Optional[str] = None,
        exam_form: Optional[str] = None,
    ) -> List[str]:
        """Find matching question IDs with support for wildcard filters.

        Filtering semantics:
            None: Wildcard (no constraint on this field)
            "": Exact match for blank/empty field
            str value: Exact match for normalized string value
        """
        results = []
        for origin_key, qid in self._key_to_id.items():
            parts = origin_key.split(":")
            if len(parts) != 7:
                continue
            k_auth, k_year, k_sess, k_subj, k_trk, k_form, k_qnum = parts

            if authority is not None:
                if k_auth != authority.strip().lower():
                    continue
            if academic_year is not None:
                if k_year != str(academic_year).strip():
                    continue
            if session is not None:
                sess_norm = str(session).strip()
                if sess_norm.isdigit() and len(sess_norm) == 1:
                    sess_norm = f"0{sess_norm}"
                if k_sess != sess_norm:
                    continue
            if subject is not None:
                if k_subj != subject.strip().lower():
                    continue
            if track is not None:
                if k_trk != track.strip().lower():
                    continue
            if exam_form is not None:
                if k_form != exam_form.strip().lower():
                    continue
            if question_number is not None:
                if k_qnum != str(question_number).strip():
                    continue

            results.append(qid)
        return sorted(results)

    def build_from_records(self, records: Iterable[CanonicalQuestionRecord]) -> None:
        """Rebuild index from a collection of records."""
        self.clear()
        for rec in records:
            key = rec.canonical_origin_key()
            if key:
                self.add(key, rec.question.question_id)

    def build_from_repository(self, repo: KiceMasterRepository) -> None:
        """Rebuild index by loading all records from repository."""
        records = repo.list_records()
        self.build_from_records(records)

    def save(self, path: Optional[Path] = None) -> None:
        """Atomically persist index to JSON."""
        target = path or self._index_path
        if not target:
            raise ValueError("No index file path specified")
        data = {
            "schema_version": "1.0",
            "entries": dict(self._key_to_id),
        }
        _atomic_json_save(target, data)

    def load(self, path: Optional[Path] = None) -> None:
        """Load index from JSON file."""
        target = path or self._index_path
        if not target or not target.is_file():
            return
        data = json.loads(target.read_text(encoding="utf-8"))
        entries = data.get("entries", {})
        self.clear()
        for key, qid in entries.items():
            self.add(key, qid)

    def keys(self) -> List[str]:
        return sorted(self._key_to_id.keys())

    def items(self) -> List[tuple[str, str]]:
        return [(k, self._key_to_id[k]) for k in sorted(self._key_to_id.keys())]
