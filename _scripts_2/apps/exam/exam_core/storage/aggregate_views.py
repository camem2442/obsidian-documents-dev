"""Ephemeral composite views dynamically synthesizing Master Question with Knowledge Relations.

Invariant:
    QuestionAggregateView is an ephemeral runtime projection. It is NEVER written
    back to disk as a combined document. Master records and Knowledge relations
    remain 100% physically isolated on disk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord
from _scripts_2.apps.exam.exam_core.domain.relation_models import ArtifactLinkRef, OccurrenceLink, QuestionLinks
from _scripts_2.apps.exam.exam_core.storage.kice_master_repository import KiceMasterRepository
from _scripts_2.apps.exam.exam_core.storage.relation_repository import RelationRepository


@dataclass
class QuestionAggregateView:
    """Projected composite view synthesized on-the-fly for UI and Exporters."""
    question_id: str
    master: CanonicalQuestionRecord
    links: QuestionLinks
    primary_solution: Optional[CanonicalQuestionRecord] = None
    solutions: Dict[str, CanonicalQuestionRecord] = field(default_factory=dict)
    occurrences: List[OccurrenceLink] = field(default_factory=list)
    artifacts: List[ArtifactLinkRef] = field(default_factory=list)

    @property
    def primary_solution_body(self) -> str:
        """Compatibility projection of primary solution body."""
        if self.primary_solution:
            return self.primary_solution.body
        return ""


def build_question_aggregate(
    question_id: str,
    master_repo: KiceMasterRepository,
    relation_repo: RelationRepository,
    solution_records_by_id: Optional[Dict[str, CanonicalQuestionRecord]] = None,
) -> QuestionAggregateView:
    """Dynamically synthesize QuestionAggregateView without touching disk."""
    master = master_repo.get(question_id)
    links = relation_repo.get(question_id)
    if links is None:
        links = QuestionLinks(question_id=question_id)

    solutions_map: Dict[str, CanonicalQuestionRecord] = {}
    if solution_records_by_id:
        for s in links.solutions:
            if s.solution_id in solution_records_by_id:
                solutions_map[s.solution_id] = solution_records_by_id[s.solution_id]

    primary_rec = None
    if links.primary_solution_id:
        primary_rec = solutions_map.get(links.primary_solution_id)

    return QuestionAggregateView(
        question_id=question_id,
        master=master,
        links=links,
        primary_solution=primary_rec,
        solutions=solutions_map,
        occurrences=list(links.occurrences),
        artifacts=list(links.artifacts),
    )
