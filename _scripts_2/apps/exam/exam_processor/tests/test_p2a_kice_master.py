"""Tests for Exam Processor P2-A: Global KICE Library Foundation.

Invariants verified:
1. Complete isolation between job-scoped RecordRepository and global KiceMasterRepository.
2. Master Record Purity: Master records must not store solutions or solution_ids on disk.
3. 7-tuple KICE canonical origin key generation and collision freedom across elective tracks.
4. OriginIndex collision detection, persistence, and lookup fidelity.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _scripts_2.apps.exam.exam_core.domain.canonical import (
    CanonicalQuestionRecord,
    OriginDomain,
    QuestionDomain,
    RelationsDomain,
    SourceDomain,
)
from _scripts_2.apps.exam.exam_core.indexing.origin_index import OriginCollisionError, OriginIndex
from _scripts_2.apps.exam.exam_core.storage.kice_master_repository import KiceMasterRepository
from _scripts_2.apps.exam.exam_processor.storage.record_repository import (
    RecordRepository,
)


def _make_sample_record(
    question_id: str,
    subject: str = "math",
    year: str = "2026",
    session: str = "09",
    track: str = "prob_stat",
    q_num: str = "29",
    exam_form: str = "",
    authority: str = "kice",
    solution: str = "",
    solution_ids: list[str] | None = None,
) -> CanonicalQuestionRecord:
    return CanonicalQuestionRecord(
        question=QuestionDomain(
            question_id=question_id,
            display_code=f"Q{q_num}",
            subject=subject,
            question_number=q_num,
        ),
        source=SourceDomain(
            source_id="src-kice-2026-09",
            type="exam",
            title="2026학년도 9월 모의평가",
            exam_form=exam_form,
        ),
        origin=OriginDomain(
            type="kice",
            authority=authority,
            academic_year=year,
            session=session,
            track=track,
            question_number=q_num,
        ),
        relations=RelationsDomain(
            solution_ids=solution_ids or [],
        ),
        body="Sample question body text.",
        solution=solution,
    )


class TestP2AGlobalKiceLibrary(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._temp_dir.name)
        self.job_dir = self.base_dir / "jobs" / "job_test_01"
        self.library_dir = self.base_dir / "library" / "kice"

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    # ----------------------------------------------------------------------
    # 1. Storage Isolation
    # ----------------------------------------------------------------------
    def test_storage_isolation_between_job_and_library(self) -> None:
        job_repo = RecordRepository()
        lib_repo = KiceMasterRepository(library_root=self.library_dir)

        rec = _make_sample_record("q-kice-2026-09-29")

        # Save to global library
        lib_repo.save(rec)

        self.assertTrue(lib_repo.exists("q-kice-2026-09-29"))
        self.assertTrue((self.library_dir / "records" / "q-kice-2026-09-29.json").is_file())

        # Must not exist in job directory
        self.assertFalse((self.job_dir / "records" / "q-kice-2026-09-29.json").exists())
        with self.assertRaises(ValueError):
            job_repo.get(self.job_dir, "q-kice-2026-09-29")

    # ----------------------------------------------------------------------
    # 2. Master Record Purity
    # ----------------------------------------------------------------------
    def test_master_record_purity_rejects_non_empty_solution(self) -> None:
        lib_repo = KiceMasterRepository(library_root=self.library_dir)
        rec = _make_sample_record("q-dirty-sol", solution="EBSi explanation text")

        with self.assertRaises(ValueError) as ctx:
            lib_repo.save(rec)
        self.assertIn("Master record purity violation", str(ctx.exception))
        self.assertIn("has non-empty solution", str(ctx.exception))

    def test_master_record_purity_rejects_non_empty_solution_ids(self) -> None:
        lib_repo = KiceMasterRepository(library_root=self.library_dir)
        rec = _make_sample_record("q-dirty-ids", solution_ids=["sol-ebsi-01"])

        with self.assertRaises(ValueError) as ctx:
            lib_repo.save(rec)
        self.assertIn("Master record purity violation", str(ctx.exception))
        self.assertIn("has solution_ids", str(ctx.exception))

    def test_master_record_save_with_strip_knowledge(self) -> None:
        lib_repo = KiceMasterRepository(library_root=self.library_dir)
        rec = _make_sample_record(
            "q-strip",
            solution="temporary solution",
            solution_ids=["sol-01", "sol-02"],
        )

        lib_repo.save(rec, strip_knowledge=True)

        loaded = lib_repo.get("q-strip")
        self.assertEqual(loaded.solution, "")
        self.assertEqual(loaded.relations.solution_ids, [])

    # ----------------------------------------------------------------------
    # 3. KICE Origin Key
    # ----------------------------------------------------------------------
    def test_canonical_origin_key_generation(self) -> None:
        rec = _make_sample_record(
            "q-prob-stat-29",
            subject="math",
            year="2026",
            session="9",  # single digit should normalize to "09"
            track="prob_stat",
            q_num="29",
        )
        expected_key = "kice:2026:09:math:prob_stat::29"
        self.assertEqual(rec.canonical_origin_key(), expected_key)

    def test_canonical_origin_key_differentiates_elective_tracks(self) -> None:
        # Math 29: Calculus vs Probability & Statistics vs Geometry
        rec_calc = _make_sample_record("q-calc-29", subject="math", track="calculus", q_num="29")
        rec_prob = _make_sample_record("q-prob-29", subject="math", track="prob_stat", q_num="29")
        rec_geom = _make_sample_record("q-geom-29", subject="math", track="geometry", q_num="29")

        key_calc = rec_calc.canonical_origin_key()
        key_prob = rec_prob.canonical_origin_key()
        key_geom = rec_geom.canonical_origin_key()

        self.assertNotEqual(key_calc, key_prob)
        self.assertNotEqual(key_prob, key_geom)
        self.assertEqual(key_calc, "kice:2026:09:math:calculus::29")
        self.assertEqual(key_prob, "kice:2026:09:math:prob_stat::29")
        self.assertEqual(key_geom, "kice:2026:09:math:geometry::29")

    def test_canonical_origin_key_differentiates_korean_electives(self) -> None:
        # Korean 35: Speech & Writing vs Language & Media
        rec_speech = _make_sample_record("q-speech-35", subject="korean", track="speech_writing", q_num="35")
        rec_lang = _make_sample_record("q-lang-35", subject="korean", track="lang_media", q_num="35")

        self.assertEqual(rec_speech.canonical_origin_key(), "kice:2026:09:korean:speech_writing::35")
        self.assertEqual(rec_lang.canonical_origin_key(), "kice:2026:09:korean:lang_media::35")
        self.assertNotEqual(rec_speech.canonical_origin_key(), rec_lang.canonical_origin_key())

    # ----------------------------------------------------------------------
    # 4. OriginIndex Operations & Collision Detection
    # ----------------------------------------------------------------------
    def test_origin_index_collision_detection(self) -> None:
        index = OriginIndex()
        key = "kice:2026:09:math:prob_stat::29"

        index.add(key, "question-uuid-A")
        self.assertEqual(index.get(key), "question-uuid-A")

        # Idempotent re-add of the same question is allowed
        index.add(key, "question-uuid-A")

        # Different question with the same key raises OriginCollisionError
        with self.assertRaises(OriginCollisionError) as ctx:
            index.add(key, "question-uuid-B")
        self.assertIn("Origin key collision", str(ctx.exception))
        self.assertIn("question-uuid-A", str(ctx.exception))
        self.assertIn("question-uuid-B", str(ctx.exception))

    def test_origin_index_reverse_collision_detection(self) -> None:
        index = OriginIndex()
        key_a = "kice:2026:09:math:prob_stat::29"
        key_b = "kice:2026:09:math:calculus::29"

        index.add(key_a, "question-uuid-1")
        # Same question ID with a different origin key raises OriginCollisionError
        with self.assertRaises(OriginCollisionError) as ctx:
            index.add(key_b, "question-uuid-1")
        self.assertIn("Question ID collision", str(ctx.exception))
        self.assertIn("question-uuid-1", str(ctx.exception))
        self.assertIn(key_a, str(ctx.exception))
        self.assertIn(key_b, str(ctx.exception))

    def test_origin_index_build_from_repository_and_find(self) -> None:
        lib_repo = KiceMasterRepository(library_root=self.library_dir)
        records = [
            _make_sample_record("q-1", subject="math", track="calculus", q_num="29"),
            _make_sample_record("q-2", subject="math", track="prob_stat", q_num="29"),
            _make_sample_record("q-3", subject="korean", track="speech_writing", q_num="35"),
        ]
        lib_repo.save_many(records)

        index_file = self.library_dir / "indexes" / "origin_index.json"
        index = OriginIndex(index_path=index_file)
        index.build_from_repository(lib_repo)
        self.assertEqual(len(index), 3)

        # Query via find
        found_calc = index.find(
            academic_year="2026",
            session="09",
            subject="math",
            track="calculus",
            question_number="29",
        )
        self.assertEqual(found_calc, "q-1")

        found_prob = index.find(
            academic_year="2026",
            session="9",  # normalization check
            subject="math",
            track="prob_stat",
            question_number="29",
        )
        self.assertEqual(found_prob, "q-2")

        # Persistence check
        index.save()
        self.assertTrue(index_file.is_file())

        loaded_index = OriginIndex(index_path=index_file)
        self.assertEqual(len(loaded_index), 3)
        self.assertEqual(loaded_index.get("kice:2026:09:math:prob_stat::29"), "q-2")

    def test_origin_index_find_candidates(self) -> None:
        lib_repo = KiceMasterRepository(library_root=self.library_dir)
        records = [
            _make_sample_record("q-calc-29", subject="math", track="calculus", q_num="29"),
            _make_sample_record("q-prob-29", subject="math", track="prob_stat", q_num="29"),
            _make_sample_record("q-geom-29", subject="math", track="geometry", q_num="29"),
            _make_sample_record("q-math-01", subject="math", track="", q_num="1"),
            _make_sample_record("q-kor-35", subject="korean", track="speech_writing", q_num="35"),
        ]
        lib_repo.save_many(records)

        index = OriginIndex()
        index.build_from_repository(lib_repo)

        # 1. Exact match with track
        cands = index.find_candidates(
            academic_year="2026",
            session="09",
            subject="math",
            question_number="29",
            track="calculus",
        )
        self.assertEqual(cands, ["q-calc-29"])

        # 2. Wildcard track (track=None) -> returns all 3 elective candidates
        cands_wildcard = index.find_candidates(
            academic_year="2026",
            session="09",
            subject="math",
            question_number="29",
            track=None,
        )
        self.assertEqual(sorted(cands_wildcard), ["q-calc-29", "q-geom-29", "q-prob-29"])

        # 3. Explicit blank track (track="") -> matches non-elective question
        cands_common = index.find_candidates(
            academic_year="2026",
            session="09",
            subject="math",
            question_number="1",
            track="",
        )
        self.assertEqual(cands_common, ["q-math-01"])

        # 4. Filter by subject mismatch
        cands_none = index.find_candidates(
            academic_year="2026",
            session="09",
            subject="english",
            question_number="29",
        )
        self.assertEqual(cands_none, [])


if __name__ == "__main__":
    unittest.main()

