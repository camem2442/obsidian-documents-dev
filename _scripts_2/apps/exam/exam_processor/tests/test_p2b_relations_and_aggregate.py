"""Tests for Exam Processor P2-B: Knowledge Relation Layer & Aggregate View.

Invariants verified:
1. Master Record Immutability: RelationService mutations DO NOT modify
   library/kice/records/{question_id}.json bytes, provenance.revision_id,
   human_approval, or source_audit.
2. Optimistic Concurrency: expected_relation_revision mismatch raises RelationConcurrencyError.
3. Multi-Solution Independence: Adding, removing, or re-electing solutions preserves
   sibling solutions, occurrences, and artifacts.
4. Primary Solution Election & Fallback: Removing primary re-elects another remaining solution.
5. Ephemeral On-the-fly Synthesis: QuestionAggregateView synthesizes master and relations
   in-memory without writing back to disk.
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from _scripts_2.apps.exam.exam_core.domain.canonical import (
    CanonicalQuestionRecord,
    OriginDomain,
    QuestionDomain,
    RelationsDomain,
    ReviewDomain,
    SourceDomain,
)
from _scripts_2.apps.exam.exam_core.domain.relation_models import (
    ArtifactLinkRef,
    OccurrenceLink,
    QuestionLinks,
    SolutionLink,
)
from _scripts_2.apps.exam.exam_core.domain.services.relation_service import RelationService
from _scripts_2.apps.exam.exam_core.storage.aggregate_views import build_question_aggregate
from _scripts_2.apps.exam.exam_core.storage.kice_master_repository import KiceMasterRepository
from _scripts_2.apps.exam.exam_core.storage.relation_repository import (
    RelationConcurrencyError,
    RelationRepository,
)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_sample_master(question_id: str) -> CanonicalQuestionRecord:
    return CanonicalQuestionRecord(
        question=QuestionDomain(
            question_id=question_id,
            display_code="Q29",
            subject="math",
            question_number="29",
        ),
        source=SourceDomain(
            source_id="src-kice-2026-09",
            type="exam",
            title="2026학년도 9월 모의평가",
        ),
        origin=OriginDomain(
            type="kice",
            authority="kice",
            academic_year="2026",
            session="09",
            track="prob_stat",
            question_number="29",
        ),
        relations=RelationsDomain(),
        review=ReviewDomain(
            human_approval="approved",
            source_audit="no_difference",
            transcription="completed",
        ),
        body="Official master question body.",
        solution="",
    )


def _make_sample_solution(solution_id: str, body: str) -> CanonicalQuestionRecord:
    return CanonicalQuestionRecord(
        question=QuestionDomain(
            question_id=solution_id,
            display_code=f"SOL-{solution_id}",
            subject="math",
            content_kind="solution",
        ),
        source=SourceDomain(
            source_id=f"src-{solution_id}",
            type="solution",
            title="Solution source",
        ),
        relations=RelationsDomain(),
        body=body,
        solution="",
    )


class TestP2BKnowledgeRelationsAndAggregateView(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._temp_dir.name)
        self.library_dir = self.base_dir / "library" / "kice"
        self.master_repo = KiceMasterRepository(library_root=self.library_dir)
        self.relation_repo = RelationRepository(library_root=self.library_dir)
        self.relation_service = RelationService(self.relation_repo, self.master_repo)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    # ----------------------------------------------------------------------
    # 1. Master Record Immutability under Relation Mutations
    # ----------------------------------------------------------------------
    def test_relation_change_does_not_modify_master_bytes(self) -> None:
        qid = "kice-2026-09-math-29"
        master = _make_sample_master(qid)
        self.master_repo.save(master)

        master_path = self.master_repo.records_dir / f"{qid}.json"
        initial_sha = _file_sha256(master_path)
        initial_rev = master.provenance.revision_id
        initial_approval = master.review.human_approval
        initial_audit = master.review.source_audit

        # Perform multiple knowledge mutations via RelationService
        self.relation_service.add_solution(
            question_id=qid,
            solution_id="sol-ebsi",
            is_primary=True,
            source_type="official",
            provider="EBSi",
        )
        self.relation_service.add_solution(
            question_id=qid,
            solution_id="sol-hanwangi",
            is_primary=False,
            source_type="publisher",
            provider="Hanwangi",
        )
        self.relation_service.link_occurrence(
            question_id=qid,
            occurrence=OccurrenceLink(
                occurrence_id="occ-1",
                source_id="src-hanwangi-a1",
                title="한완기 2026",
                item_code="Q041",
            ),
        )
        self.relation_service.link_artifact(
            question_id=qid,
            artifact=ArtifactLinkRef(
                link_id="link-art-1",
                artifact_id="art-uuid-1",
                source_path="KS/4 수학/26해4021/26해4021 태도 정리.md",
                relation_type="attitude_framework",
                verification_status="candidate",
            ),
        )
        self.relation_service.set_primary_solution(qid, "sol-hanwangi")

        # Invariant checks on Master Record
        self.assertEqual(_file_sha256(master_path), initial_sha)
        reloaded_master = self.master_repo.get(qid)
        self.assertEqual(reloaded_master.provenance.revision_id, initial_rev)
        self.assertEqual(reloaded_master.review.human_approval, initial_approval)
        self.assertEqual(reloaded_master.review.source_audit, initial_audit)
        self.assertEqual(reloaded_master.solution, "")
        self.assertEqual(reloaded_master.relations.solution_ids, [])

    # ----------------------------------------------------------------------
    # 2. Optimistic Concurrency Control & Referential Integrity Hardening
    # ----------------------------------------------------------------------
    def test_relation_revision_optimistic_lock(self) -> None:
        qid = "q-concurrent-test"
        self.master_repo.save(_make_sample_master(qid))
        links1 = self.relation_service.add_solution(qid, "sol-1")
        rev1 = links1.relation_revision

        # Mutate to advance revision to rev2
        links2 = self.relation_service.add_solution(qid, "sol-2")
        rev2 = links2.relation_revision
        self.assertNotEqual(rev1, rev2)

        # Attempting mutation with stale rev1 must fail
        with self.assertRaises(RelationConcurrencyError):
            self.relation_service.set_primary_solution(qid, "sol-1", expected_revision=rev1)

        # Mutation with current rev2 succeeds
        links3 = self.relation_service.set_primary_solution(qid, "sol-1", expected_revision=rev2)
        self.assertEqual(links3.primary_solution_id, "sol-1")

    def test_relation_rejects_missing_master(self) -> None:
        service_with_master = RelationService(self.relation_repo, self.master_repo)
        with self.assertRaises(ValueError) as cm:
            service_with_master.add_solution("non-existent-master-id", "sol-1")
        self.assertIn("does not exist", str(cm.exception))

    def test_links_reject_primary_not_in_solutions(self) -> None:
        invalid_links = QuestionLinks(
            question_id="q-invalid-primary",
            primary_solution_id="sol-X",
            solutions=[
                SolutionLink(solution_id="sol-A"),
                SolutionLink(solution_id="sol-B"),
            ],
        )
        with self.assertRaises(ValueError) as cm:
            self.relation_repo.save(invalid_links)
        self.assertIn("not in linked solutions", str(cm.exception))

    def test_relation_create_honors_expected_revision_contract(self) -> None:
        qid = "kice-2026-09-math-29"
        self.master_repo.save(_make_sample_master(qid))
        with self.assertRaises(RelationConcurrencyError):
            self.relation_service.add_solution(qid, "sol-1", expected_revision="stale-rev-123")

    # ----------------------------------------------------------------------
    # 3. Multi-Solution Operations
    # ----------------------------------------------------------------------
    def test_add_multiple_solutions_and_primary_selection(self) -> None:
        qid = "q-multi-sol"
        self.master_repo.save(_make_sample_master(qid))
        # First solution becomes primary by default
        links = self.relation_service.add_solution(qid, "sol-ebsi", provider="EBSi")
        self.assertEqual(links.primary_solution_id, "sol-ebsi")
        self.assertEqual(len(links.solutions), 1)

        # Second solution added without is_primary stays secondary
        links = self.relation_service.add_solution(qid, "sol-hanwangi", provider="Hanwangi")
        self.assertEqual(links.primary_solution_id, "sol-ebsi")
        self.assertEqual(len(links.solutions), 2)

        # Explicit primary switch
        links = self.relation_service.set_primary_solution(qid, "sol-hanwangi")
        self.assertEqual(links.primary_solution_id, "sol-hanwangi")

    def test_remove_one_solution_preserves_others(self) -> None:
        qid = "q-remove-sol"
        self.master_repo.save(_make_sample_master(qid))
        self.relation_service.add_solution(qid, "sol-1")
        self.relation_service.add_solution(qid, "sol-2")
        self.relation_service.add_solution(qid, "sol-3")

        links = self.relation_service.remove_solution(qid, "sol-2")
        sol_ids = [s.solution_id for s in links.solutions]
        self.assertEqual(sol_ids, ["sol-1", "sol-3"])
        self.assertEqual(links.primary_solution_id, "sol-1")

    def test_primary_solution_fallback(self) -> None:
        qid = "q-fallback"
        self.master_repo.save(_make_sample_master(qid))
        self.relation_service.add_solution(qid, "sol-1")
        self.relation_service.add_solution(qid, "sol-2")
        self.relation_service.set_primary_solution(qid, "sol-1")

        # Removing primary sol-1 should fall back to remaining sol-2
        links = self.relation_service.remove_solution(qid, "sol-1")
        self.assertEqual(links.primary_solution_id, "sol-2")

        # Removing the last solution leaves primary as None
        links = self.relation_service.remove_solution(qid, "sol-2")
        self.assertIsNone(links.primary_solution_id)
        self.assertEqual(len(links.solutions), 0)

    # ----------------------------------------------------------------------
    # 4. Occurrences and Artifacts Orthogonality
    # ----------------------------------------------------------------------
    def test_occurrence_and_artifact_survive_solution_changes(self) -> None:
        qid = "q-orthogonal"
        self.master_repo.save(_make_sample_master(qid))
        self.relation_service.add_solution(qid, "sol-1")
        self.relation_service.link_occurrence(
            qid,
            OccurrenceLink(occurrence_id="occ-1", source_id="src-1", title="한완기"),
        )
        self.relation_service.link_artifact(
            qid,
            ArtifactLinkRef(
                link_id="art-link-1",
                artifact_id="art-uuid-1",
                source_path="KS/3 영어/독해.md",
                relation_type="reasoning_process",
            ),
        )

        # Remove solution
        links = self.relation_service.remove_solution(qid, "sol-1")
        self.assertEqual(len(links.solutions), 0)
        # Occurrence and artifact are 100% intact
        self.assertEqual(len(links.occurrences), 1)
        self.assertEqual(links.occurrences[0].occurrence_id, "occ-1")
        self.assertEqual(len(links.artifacts), 1)
        self.assertEqual(links.artifacts[0].link_id, "art-link-1")

    # ----------------------------------------------------------------------
    # 5. QuestionAggregateView Composition
    # ----------------------------------------------------------------------
    def test_aggregate_view_composes_master_and_links(self) -> None:
        qid = "kice-2026-09-math-29"
        master = _make_sample_master(qid)
        self.master_repo.save(master)

        self.relation_service.add_solution(qid, "sol-ebsi", is_primary=False)
        self.relation_service.add_solution(qid, "sol-hanwangi", is_primary=True)
        self.relation_service.link_occurrence(
            qid,
            OccurrenceLink(occurrence_id="occ-1", source_id="src-1", title="한완기 2026"),
        )
        self.relation_service.link_artifact(
            qid,
            ArtifactLinkRef(
                link_id="link-1",
                artifact_id="art-1",
                source_path="KS/4 수학/26해4021 태도 정리.md",
                relation_type="attitude_framework",
            ),
        )

        sol_records = {
            "sol-ebsi": _make_sample_solution("sol-ebsi", "EBSi explanation text"),
            "sol-hanwangi": _make_sample_solution("sol-hanwangi", "Hanwangi deep explanation text"),
        }

        view = build_question_aggregate(
            question_id=qid,
            master_repo=self.master_repo,
            relation_repo=self.relation_repo,
            solution_records_by_id=sol_records,
        )

        # Check composite properties
        self.assertEqual(view.question_id, qid)
        self.assertEqual(view.master.body, "Official master question body.")
        self.assertEqual(view.links.primary_solution_id, "sol-hanwangi")
        self.assertIsNotNone(view.primary_solution)
        self.assertEqual(view.primary_solution_body, "Hanwangi deep explanation text")
        self.assertEqual(len(view.solutions), 2)
        self.assertEqual(len(view.occurrences), 1)
        self.assertEqual(len(view.artifacts), 1)
        self.assertEqual(view.artifacts[0].relation_type, "attitude_framework")


if __name__ == "__main__":
    unittest.main()

