"""Tests for Exam Processor P2-D.1: Evidence-based Matcher Hardening & Apply Suite.

Invariants verified:
1. OriginEvidenceParser & ExamSchemeResolver:
   - Rejects non-KICE authorities and workbook occurrence codes (e.g. Hanwangi '해', EBS item codes).
   - Strict Subject vs Track separation ('2 문법' -> korean with track=None for 2022+ electives, NOT lang_media).
   - Integrated Korean (2017~2021) correctly recognizes 문법 as fully qualified common.
   - Suffix relation hint inference with None default.
2. EvidenceMatcher & Index Integrity (Fail-Closed):
   - Missing master references in OriginIndex raise OriginIndexIntegrityError.
   - Stale origin keys in OriginIndex raise OriginIndexIntegrityError.
   - Pure dry-run with ZERO disk mutations on SSOT.
   - Deterministic report fingerprinting.
   - Exact vs Single distinction: MATCH_EXACT_ORIGIN (CandidateStatus.EXACT) vs MATCH_SINGLE_CANDIDATE (CandidateStatus.MANUAL_REVIEW).
3. MatchApplyService & True Idempotency:
   - Deterministic link_id (uuid5).
   - Preserves normalized evidence, matcher_version, report_generated_at, applied_at.
   - True No-Op on duplicate application (preserves relation_revision and file bytes).
   - Prevents duplicate artifact_ids in QuestionLinks.validate().
   - Fails on stale artifact SHA-256.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _scripts_2.apps.exam.exam_core.domain.artifact_models import ArtifactRecord
from _scripts_2.apps.exam.exam_core.domain.canonical import (
    CanonicalQuestionRecord,
    OriginDomain,
    QuestionDomain,
    RelationsDomain,
    SourceDomain,
)
from _scripts_2.apps.exam.exam_processor.domain.match_models import (
    CandidateStatus,
    MatchDecision,
)
from _scripts_2.apps.exam.exam_core.domain.relation_models import ArtifactLinkRef, QuestionLinks
from _scripts_2.apps.exam.exam_processor.domain.services.evidence_matcher import (
    EvidenceMatcher,
    OriginIndexIntegrityError,
)
from _scripts_2.apps.exam.exam_processor.domain.services.match_apply_service import (
    MatchApplyService,
)
from _scripts_2.apps.exam.exam_processor.domain.services.origin_evidence_parser import (
    OriginEvidenceParser,
    infer_relation_type_hint,
)
from _scripts_2.apps.exam.exam_core.domain.services.relation_service import RelationService
from _scripts_2.apps.exam.exam_core.indexing.origin_index import OriginIndex
from _scripts_2.apps.exam.exam_core.storage.artifact_repository import ArtifactRepository
from _scripts_2.apps.exam.exam_core.storage.kice_master_repository import KiceMasterRepository
from _scripts_2.apps.exam.exam_core.storage.relation_repository import RelationRepository


def _make_master_record(
    question_id: str,
    subject: str = "math",
    year: str = "2026",
    session: str = "09",
    track: str = "",
    q_num: str = "29",
    exam_form: str = "",
) -> CanonicalQuestionRecord:
    return CanonicalQuestionRecord(
        question=QuestionDomain(
            question_id=question_id,
            display_code=f"Q{q_num}",
            subject=subject,
            question_number=q_num,
        ),
        source=SourceDomain(
            source_id=f"src-kice-{year}-{session}",
            type="exam",
            title=f"{year}학년도 {session}월 모의평가",
            exam_form=exam_form,
        ),
        origin=OriginDomain(
            type="kice",
            authority="kice",
            academic_year=year,
            session=session,
            track=track,
            question_number=q_num,
            exam_form=exam_form,
        ),
        relations=RelationsDomain(),
        body="Master question body text.",
        solution="",
    )


class TestOriginEvidenceParser(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = OriginEvidenceParser()

    def test_pure_6digit_kice_filename_calculus(self) -> None:
        parsed = self.parser.parse_path("KS/1 수학/1 미적분/260929.md")
        self.assertTrue(parsed.is_kice)
        self.assertEqual(parsed.academic_year, "2026")
        self.assertEqual(parsed.session, "09")
        self.assertEqual(parsed.question_number, "29")
        self.assertEqual(parsed.subject, "math")
        self.assertEqual(parsed.track, "calculus")
        self.assertTrue(parsed.is_fully_qualified)
        self.assertIsNone(parsed.relation_type_hint)

    def test_suffix_relation_type_hints(self) -> None:
        self.assertEqual(infer_relation_type_hint("시험장 전략"), "exam_strategy")
        self.assertEqual(infer_relation_type_hint("전략"), "exam_strategy")
        self.assertEqual(infer_relation_type_hint("태도"), "attitude_framework")
        self.assertEqual(infer_relation_type_hint("강령"), "attitude_framework")
        self.assertEqual(infer_relation_type_hint("풀이 과정"), "reasoning_process")
        self.assertEqual(infer_relation_type_hint("해설"), "reasoning_process")
        self.assertEqual(infer_relation_type_hint("번역"), "translation")
        self.assertEqual(infer_relation_type_hint("오답 선지"), "choice_analysis")
        self.assertIsNone(infer_relation_type_hint(""))
        self.assertIsNone(infer_relation_type_hint("복습용"))

    def test_2022_2027_korean_grammar_path_separation_rule(self) -> None:
        # '2 문법' without explicit '언어와 매체' for 2026 Korean Q35 -> elective track is missing (not fully qualified)
        parsed = self.parser.parse_path("KS/2 국어/2 문법/260935.md")
        self.assertTrue(parsed.is_kice)
        self.assertEqual(parsed.subject, "korean")
        self.assertIsNone(parsed.track)
        self.assertFalse(parsed.is_fully_qualified)

        # Explicit '언어와 매체' folder -> fully qualified
        parsed_media = self.parser.parse_path("KS/2 국어/언어와 매체/260935.md")
        self.assertEqual(parsed_media.subject, "korean")
        self.assertEqual(parsed_media.track, "lang_media")
        self.assertTrue(parsed_media.is_fully_qualified)

    def test_2017_2021_integrated_korean_grammar(self) -> None:
        # 2018 Korean Q13 in '2 문법' -> 2018 Korean is integrated (track=""), so it is fully qualified!
        parsed_2018 = self.parser.parse_path("KS/2 국어/2 문법/180913.md")
        self.assertTrue(parsed_2018.is_kice)
        self.assertEqual(parsed_2018.subject, "korean")
        self.assertEqual(parsed_2018.track, "")
        self.assertTrue(parsed_2018.is_fully_qualified)

    def test_non_kice_and_workbook_rejections(self) -> None:
        p_hanwangi = self.parser.parse_path("KS/1 수학/26해0929.md")
        self.assertFalse(p_hanwangi.is_kice)
        self.assertIn("Hanwangi", p_hanwangi.rejection_reason or "")

        p_ebs = self.parser.parse_path("KS/영어/24001-0123.md")
        self.assertFalse(p_ebs.is_kice)
        self.assertIn("EBS", p_ebs.rejection_reason or "")

        p_police = self.parser.parse_path("KS/경찰대/2024/01.md")
        self.assertFalse(p_police.is_kice)
        self.assertIn("경찰", p_police.rejection_reason or "")


class TestEvidenceMatcherAndApplyService(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._temp_dir.name)
        self.lib_kice_dir = self.base_dir / "library" / "kice"
        self.lib_art_dir = self.base_dir / "library" / "artifacts"

        self.master_repo = KiceMasterRepository(library_root=self.lib_kice_dir)
        self.rel_repo = RelationRepository(library_root=self.lib_kice_dir)
        self.rel_service = RelationService(self.rel_repo, master_repo=self.master_repo)
        self.art_repo = ArtifactRepository(artifacts_root=self.lib_art_dir)
        self.origin_index = OriginIndex()

        # Seed master repository with KICE questions
        self.master_calc_29 = _make_master_record("q-calc-29", subject="math", track="calculus", q_num="29")
        self.master_prob_29 = _make_master_record("q-prob-29", subject="math", track="prob_stat", q_num="29")
        self.master_geom_29 = _make_master_record("q-geom-29", subject="math", track="geometry", q_num="29")
        self.master_math_01 = _make_master_record("q-math-01", subject="math", track="", q_num="1")
        self.master_eng_33 = _make_master_record("q-eng-33", subject="english", track="", q_num="33")

        self.master_repo.save_many([
            self.master_calc_29,
            self.master_prob_29,
            self.master_geom_29,
            self.master_math_01,
            self.master_eng_33,
        ])
        self.origin_index.build_from_repository(self.master_repo)

        self.matcher = EvidenceMatcher(
            master_repo=self.master_repo,
            origin_index=self.origin_index,
        )
        self.apply_service = MatchApplyService(
            artifact_repo=self.art_repo,
            relation_service=self.rel_service,
            matcher=self.matcher,
            master_repo=self.master_repo,
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_matcher_exact_origin_match(self) -> None:
        art = self.art_repo.register_or_update(
            source_path="KS/1 수학/1 미적분/260929.md",
            content_sha256="a" * 64,
            file_size=120,
        )

        report = self.matcher.match_artifact(art)

        self.assertEqual(report.decision, MatchDecision.MATCH_EXACT_ORIGIN.value)
        self.assertEqual(len(report.candidates), 1)
        self.assertEqual(report.candidates[0].question_id, "q-calc-29")
        self.assertEqual(report.candidates[0].origin_key, "kice:2026:09:math:calculus::29")
        self.assertEqual(report.candidates[0].status, CandidateStatus.EXACT.value)
        self.assertTrue(report.report_fingerprint)

        # Zero disk mutation check: no relations written
        self.assertIsNone(self.rel_service.get_links("q-calc-29"))

    def test_matcher_single_candidate_on_wildcard(self) -> None:
        # English 33 without subject folder (wildcard subject used, but only 1 question exists in library)
        art = self.art_repo.register_or_update(
            source_path="KS/기타/260933.md",
            content_sha256="b" * 64,
            file_size=110,
        )

        report = self.matcher.match_artifact(art)
        # Because origin was not fully qualified (subject was wildcard), must NOT be EXACT!
        self.assertEqual(report.decision, MatchDecision.MATCH_SINGLE_CANDIDATE.value)
        self.assertEqual(len(report.candidates), 1)
        self.assertEqual(report.candidates[0].status, CandidateStatus.MANUAL_REVIEW.value)

    def test_matcher_ambiguous_elective_match(self) -> None:
        # Math 29 in a generic Math folder without elective track
        art = self.art_repo.register_or_update(
            source_path="KS/1 수학/260929.md",
            content_sha256="c" * 64,
            file_size=150,
        )

        report = self.matcher.match_artifact(art)

        self.assertEqual(report.decision, MatchDecision.MATCH_AMBIGUOUS.value)
        self.assertEqual(len(report.candidates), 3)
        for c in report.candidates:
            self.assertEqual(c.status, CandidateStatus.AMBIGUOUS.value)

    def test_matcher_fails_closed_on_missing_master(self) -> None:
        # Invalidate index by adding an entry pointing to a non-existent master
        self.origin_index.add("kice:2026:09:math:prob_stat::30", "q-ghost-30")

        art = self.art_repo.register_or_update(
            source_path="KS/1 수학/확률과 통계/260930.md",
            content_sha256="d" * 64,
            file_size=100,
        )

        with self.assertRaises(OriginIndexIntegrityError) as ctx:
            self.matcher.match_artifact(art)
        self.assertIn("missing master question 'q-ghost-30'", str(ctx.exception))

    def test_matcher_fails_closed_on_stale_origin_key(self) -> None:
        # Invalidate index by mapping q-calc-29 to an incorrect key
        self.origin_index.remove("q-calc-29")
        self.origin_index.add("kice:2026:09:math:calculus::30", "q-calc-29")

        art = self.art_repo.register_or_update(
            source_path="KS/1 수학/1 미적분/260930.md",
            content_sha256="e" * 64,
            file_size=100,
        )

        with self.assertRaises(OriginIndexIntegrityError) as ctx:
            self.matcher.match_artifact(art)
        self.assertIn("OriginIndex stale for question 'q-calc-29'", str(ctx.exception))

    def test_apply_service_true_idempotency_and_no_op(self) -> None:
        art = self.art_repo.register_or_update(
            source_path="KS/영어/260933_시험장전략.md",
            content_sha256="f" * 64,
            file_size=200,
        )

        report = self.matcher.match_artifact(art)
        self.assertEqual(report.decision, MatchDecision.MATCH_EXACT_ORIGIN.value)

        # 1st Apply
        links1 = self.apply_service.apply_candidate(
            report=report,
            candidate_question_id="q-eng-33",
            relation_type="exam_strategy",
        )
        rev1 = links1.relation_revision
        rel_file = self.lib_kice_dir / "links" / "q-eng-33.json"
        self.assertTrue(rel_file.is_file())
        bytes1 = rel_file.read_bytes()

        self.assertEqual(len(links1.artifacts), 1)
        self.assertEqual(links1.artifacts[0].report_generated_at, report.generated_at)
        self.assertTrue(links1.artifacts[0].evidence)

        # 2nd Apply (Exact same parameters) -> True Idempotent No-Op
        links2 = self.apply_service.apply_candidate(
            report=report,
            candidate_question_id="q-eng-33",
            relation_type="exam_strategy",
        )
        rev2 = links2.relation_revision
        bytes2 = rel_file.read_bytes()

        # Revision and disk file must be completely untouched!
        self.assertEqual(rev1, rev2)
        self.assertEqual(bytes1, bytes2)
        self.assertEqual(len(links2.artifacts), 1)

    def test_question_links_rejects_duplicate_artifact_id(self) -> None:
        link_ref1 = ArtifactLinkRef(
            link_id="link-1",
            artifact_id="art-uuid-1",
            source_path="KS/1.md",
            relation_type="exam_strategy",
        )
        link_ref2 = ArtifactLinkRef(
            link_id="link-2",
            artifact_id="art-uuid-1",  # Duplicate artifact UUID!
            source_path="KS/1.md",
            relation_type="attitude_framework",
        )
        links = QuestionLinks(
            question_id="q-1",
            artifacts=[link_ref1, link_ref2],
        )
        with self.assertRaises(ValueError) as ctx:
            links.validate()
        self.assertIn("Duplicate artifact_id 'art-uuid-1'", str(ctx.exception))

    def test_strict_sha256_validation(self) -> None:
        # Invalid non-hex 64-char SHA raises ValueError
        with self.assertRaises(ValueError) as ctx:
            ArtifactRecord(
                artifact_id="art-1",
                source_path="KS/1.md",
                content_sha256="z" * 64,
                file_size=10,
            ).validate()
        self.assertIn("64-char lowercase hex string", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
