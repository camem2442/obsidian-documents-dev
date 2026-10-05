"""RelationService — Knowledge Relation mutations for KICE Master records.

All knowledge relation mutations (solutions, occurrences, artifacts) MUST go
through this service.

Invariants:
    1. Knowledge relation mutations DO NOT bump Master content revision
       (CanonicalQuestionRecord.provenance.revision_id).
    2. Knowledge relation mutations DO NOT reset Master human_approval or source_audit.
    3. Knowledge relation mutations DO NOT modify library/kice/records/{question_id}.json.
    4. Each mutation updates relation_revision in library/kice/links/{question_id}.json.
    5. Optimistic concurrency control via expected_revision is supported.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, List, Optional

from _scripts_2.apps.exam.exam_core.storage.relation_repository import RelationRepository
from _scripts_2.apps.exam.exam_core.domain.relation_models import ArtifactLinkRef, OccurrenceLink, QuestionLinks, SolutionLink

if TYPE_CHECKING:
    from _scripts_2.apps.exam.exam_core.storage.kice_master_repository import KiceMasterRepository


class RelationService:
    """Service managing external knowledge relations for questions."""

    def __init__(
        self,
        relation_repo: RelationRepository,
        master_repo: KiceMasterRepository,
    ) -> None:
        if master_repo is None:
            raise ValueError("master_repo cannot be None")
        self._repo = relation_repo
        self._master_repo = master_repo

    def _ensure_master_exists(self, question_id: str) -> None:
        if not self._master_repo.exists(question_id):
            raise ValueError(f"Master record for question '{question_id}' does not exist")

    def get_links(self, question_id: str) -> Optional[QuestionLinks]:
        """Retrieve question links if they exist."""
        return self._repo.get(question_id)

    def add_solution(
        self,
        question_id: str,
        solution_id: str,
        is_primary: bool = False,
        source_type: str = "official",
        provider: str = "",
        perspective: str = "",
        approach_tags: Optional[List[str]] = None,
        created_at: str = "",
        expected_revision: Optional[str] = None,
    ) -> QuestionLinks:
        """Link a solution to a question without touching Master content revision."""
        if not question_id:
            raise ValueError("question_id cannot be empty")
        if not solution_id:
            raise ValueError("solution_id cannot be empty")

        self._ensure_master_exists(question_id)

        links = self._repo.get_or_create(question_id)
        current_rev = links.relation_revision

        # Check existing
        existing_idx = None
        for i, s in enumerate(links.solutions):
            if s.solution_id == solution_id:
                existing_idx = i
                break

        new_link = SolutionLink(
            solution_id=solution_id,
            source_type=source_type,
            provider=provider,
            perspective=perspective,
            approach_tags=list(approach_tags or []),
            created_at=created_at,
        )

        if existing_idx is not None:
            links.solutions[existing_idx] = new_link
        else:
            links.solutions.append(new_link)

        if is_primary or links.primary_solution_id is None:
            links.primary_solution_id = solution_id

        links.relation_revision = uuid.uuid4().hex
        eff_exp_rev = (
            expected_revision
            if expected_revision is not None
            else (current_rev if self._repo.exists(question_id) else None)
        )
        self._repo.save(links, expected_revision=eff_exp_rev)
        return links

    def set_primary_solution(
        self,
        question_id: str,
        solution_id: str,
        expected_revision: Optional[str] = None,
    ) -> QuestionLinks:
        """Designate which linked solution is the primary solution."""
        self._ensure_master_exists(question_id)
        links = self._repo.get(question_id)
        if links is None:
            raise ValueError(f"No relations found for question '{question_id}'")

        current_rev = links.relation_revision
        sol_ids = {s.solution_id for s in links.solutions}
        if solution_id not in sol_ids:
            raise ValueError(f"Solution '{solution_id}' is not linked to question '{question_id}'")

        links.primary_solution_id = solution_id
        links.relation_revision = uuid.uuid4().hex
        eff_exp_rev = expected_revision if expected_revision is not None else current_rev
        self._repo.save(links, expected_revision=eff_exp_rev)
        return links

    def remove_solution(
        self,
        question_id: str,
        solution_id: str,
        expected_revision: Optional[str] = None,
    ) -> QuestionLinks:
        """Remove a linked solution and re-elect primary if needed."""
        self._ensure_master_exists(question_id)
        links = self._repo.get(question_id)
        if links is None:
            raise ValueError(f"No relations found for question '{question_id}'")

        current_rev = links.relation_revision
        links.solutions = [s for s in links.solutions if s.solution_id != solution_id]

        if links.primary_solution_id == solution_id:
            if links.solutions:
                links.primary_solution_id = links.solutions[0].solution_id
            else:
                links.primary_solution_id = None

        links.relation_revision = uuid.uuid4().hex
        eff_exp_rev = expected_revision if expected_revision is not None else current_rev
        self._repo.save(links, expected_revision=eff_exp_rev)
        return links

    def link_occurrence(
        self,
        question_id: str,
        occurrence: OccurrenceLink,
        expected_revision: Optional[str] = None,
    ) -> QuestionLinks:
        """Link a workbook or exam placement occurrence."""
        self._ensure_master_exists(question_id)
        links = self._repo.get_or_create(question_id)
        current_rev = links.relation_revision

        existing_idx = None
        for i, o in enumerate(links.occurrences):
            if o.occurrence_id == occurrence.occurrence_id:
                existing_idx = i
                break

        if existing_idx is not None:
            links.occurrences[existing_idx] = occurrence
        else:
            links.occurrences.append(occurrence)

        links.relation_revision = uuid.uuid4().hex
        eff_exp_rev = (
            expected_revision
            if expected_revision is not None
            else (current_rev if self._repo.exists(question_id) else None)
        )
        self._repo.save(links, expected_revision=eff_exp_rev)
        return links

    def remove_occurrence(
        self,
        question_id: str,
        occurrence_id: str,
        expected_revision: Optional[str] = None,
    ) -> QuestionLinks:
        """Remove an occurrence link."""
        self._ensure_master_exists(question_id)
        links = self._repo.get(question_id)
        if links is None:
            raise ValueError(f"No relations found for question '{question_id}'")

        current_rev = links.relation_revision
        links.occurrences = [o for o in links.occurrences if o.occurrence_id != occurrence_id]
        links.relation_revision = uuid.uuid4().hex
        eff_exp_rev = expected_revision if expected_revision is not None else current_rev
        self._repo.save(links, expected_revision=eff_exp_rev)
        return links

    def link_artifact(
        self,
        question_id: str,
        artifact: ArtifactLinkRef,
        expected_revision: Optional[str] = None,
    ) -> QuestionLinks:
        """Link a KS learning artifact.
        
        True Idempotency: If an existing link with the same artifact_id and matching
        semantic payload already exists, returns current QuestionLinks as a no-op
        without bumping relation_revision or touching disk.
        """
        self._ensure_master_exists(question_id)
        links = self._repo.get_or_create(question_id)
        current_rev = links.relation_revision

        existing_idx = None
        for i, a in enumerate(links.artifacts):
            if a.link_id == artifact.link_id or a.artifact_id == artifact.artifact_id:
                existing_idx = i
                break

        if existing_idx is not None:
            existing = links.artifacts[existing_idx]
            # Semantic equivalence check (applied_at is excluded to preserve idempotency on retries)
            if (
                existing.artifact_id == artifact.artifact_id
                and existing.source_path == artifact.source_path
                and existing.relation_type == artifact.relation_type
                and existing.verification_status == artifact.verification_status
                and existing.evidence == artifact.evidence
                and existing.matcher_version == artifact.matcher_version
                and existing.report_generated_at == artifact.report_generated_at
            ):
                return links

            links.artifacts[existing_idx] = artifact
        else:
            links.artifacts.append(artifact)

        links.relation_revision = uuid.uuid4().hex
        eff_exp_rev = (
            expected_revision
            if expected_revision is not None
            else (current_rev if self._repo.exists(question_id) else None)
        )
        self._repo.save(links, expected_revision=eff_exp_rev)
        return links


    def remove_artifact(
        self,
        question_id: str,
        link_id: str,
        expected_revision: Optional[str] = None,
    ) -> QuestionLinks:
        """Remove a KS learning artifact link."""
        self._ensure_master_exists(question_id)
        links = self._repo.get(question_id)
        if links is None:
            raise ValueError(f"No relations found for question '{question_id}'")

        current_rev = links.relation_revision
        links.artifacts = [a for a in links.artifacts if a.link_id != link_id]
        links.relation_revision = uuid.uuid4().hex
        eff_exp_rev = expected_revision if expected_revision is not None else current_rev
        self._repo.save(links, expected_revision=eff_exp_rev)
        return links

