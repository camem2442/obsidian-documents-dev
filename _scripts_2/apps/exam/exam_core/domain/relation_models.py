"""Data models for Knowledge Relations in Exam Processor P2-B.

These models define the knowledge graph stored in:
    library/kice/links/{question_id}.json

Relations include:
    - solutions: External solutions (EBSi, Hanwangi, personal, AI)
    - occurrences: Placements in workbooks and mock exams
    - artifacts: KS learning artifacts (reasoning process, exam strategy, attitude, schema)
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


VALID_SOURCE_TYPES = {"official", "publisher", "personal", "ai"}
VALID_VERIFICATION_STATUSES = {"verified", "high_confidence_candidate", "candidate", "conflict"}
VALID_RELATION_TYPES = {
    "reasoning_process",
    "exam_strategy",
    "attitude_framework",
    "passage_schema",
    "choice_analysis",
    "translation",
}


@dataclass
class SolutionLink:
    solution_id: str
    source_type: str = "official"  # official, publisher, personal, ai
    provider: str = ""             # e.g., "EBSi", "Hanwangi", "user", "gemini"
    kind: str = "solution"
    perspective: str = ""          # e.g., "algebraic", "geometric", "fast_intuitive"
    approach_tags: List[str] = field(default_factory=list)
    created_at: str = ""


@dataclass
class OccurrenceLink:
    occurrence_id: str
    source_id: str
    title: str = ""
    edition: Optional[str] = None
    section_code: Optional[str] = None
    section_title: Optional[str] = None
    item_code: Optional[str] = None
    source_set_id: str = ""
    verified: bool = False


@dataclass
class ArtifactLinkRef:
    link_id: str
    artifact_id: str               # stable UUID
    source_path: str               # e.g., "KS/3 영어/.../260933 시험장.md"
    relation_type: str             # reasoning_process, exam_strategy, attitude_framework, passage_schema, choice_analysis, translation
    verification_status: str = "candidate"  # verified, candidate, conflict
    evidence: List[str] = field(default_factory=list)
    matcher_version: str = "1.0"
    report_generated_at: str = ""
    applied_at: str = ""


@dataclass
class QuestionLinks:
    question_id: str
    relation_revision: str = field(default_factory=lambda: uuid.uuid4().hex)
    primary_solution_id: Optional[str] = None
    solutions: List[SolutionLink] = field(default_factory=list)
    occurrences: List[OccurrenceLink] = field(default_factory=list)
    artifacts: List[ArtifactLinkRef] = field(default_factory=list)

    def validate(self) -> None:
        """Validate internal integrity of QuestionLinks."""
        if not self.question_id:
            raise ValueError("question_id cannot be empty")

        sol_ids = set()
        for s in self.solutions:
            if s.solution_id in sol_ids:
                raise ValueError(f"Duplicate solution_id '{s.solution_id}' in QuestionLinks")
            sol_ids.add(s.solution_id)
            if s.source_type not in VALID_SOURCE_TYPES:
                raise ValueError(f"Invalid source_type '{s.source_type}' for solution '{s.solution_id}'")

        if self.primary_solution_id is not None:
            if self.primary_solution_id not in sol_ids:
                raise ValueError(
                    f"primary_solution_id '{self.primary_solution_id}' is not in linked solutions"
                )

        occ_ids = set()
        for o in self.occurrences:
            if o.occurrence_id in occ_ids:
                raise ValueError(f"Duplicate occurrence_id '{o.occurrence_id}' in QuestionLinks")
            occ_ids.add(o.occurrence_id)

        link_ids = set()
        art_uuids = set()
        for a in self.artifacts:
            if a.link_id in link_ids:
                raise ValueError(f"Duplicate artifact link_id '{a.link_id}' in QuestionLinks")
            link_ids.add(a.link_id)

            if a.artifact_id in art_uuids:
                raise ValueError(f"Duplicate artifact_id '{a.artifact_id}' in QuestionLinks")
            art_uuids.add(a.artifact_id)

            if a.verification_status not in VALID_VERIFICATION_STATUSES:
                raise ValueError(
                    f"Invalid verification_status '{a.verification_status}' for artifact '{a.link_id}'"
                )
            if a.relation_type not in VALID_RELATION_TYPES:
                raise ValueError(
                    f"Invalid relation_type '{a.relation_type}' for artifact '{a.link_id}'"
                )

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> QuestionLinks:
        q_id = data.get("question_id", "")
        rel_rev = data.get("relation_revision") or uuid.uuid4().hex
        primary_sol = data.get("primary_solution_id")

        solutions = [
            SolutionLink(**s) if isinstance(s, dict) else s
            for s in data.get("solutions", [])
        ]
        occurrences = [
            OccurrenceLink(**o) if isinstance(o, dict) else o
            for o in data.get("occurrences", [])
        ]
        artifacts = [
            ArtifactLinkRef(**a) if isinstance(a, dict) else a
            for a in data.get("artifacts", [])
        ]

        obj = cls(
            question_id=q_id,
            relation_revision=rel_rev,
            primary_solution_id=primary_sol,
            solutions=solutions,
            occurrences=occurrences,
            artifacts=artifacts,
        )
        obj.validate()
        return obj
