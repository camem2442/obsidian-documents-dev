"""Match Models for Exam Processor P2-D.1: Evidence-based Matcher.

Data models for representing origin evidence, candidate matches, match decisions,
and artifact matching reports with deterministic fingerprinting.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MatchDecision(str, Enum):
    """Decision category for matching an artifact against master records."""
    MATCH_EXACT_ORIGIN = "MATCH_EXACT_ORIGIN"         # Deterministic 1:1 match with fully-qualified origin
    MATCH_SINGLE_CANDIDATE = "MATCH_SINGLE_CANDIDATE" # 1 candidate found via wildcard/partial match (Manual Review required)
    MATCH_AMBIGUOUS = "MATCH_AMBIGUOUS"               # Multiple candidates (e.g. elective track missing)
    MATCH_CONFLICT = "MATCH_CONFLICT"                 # Conflicting evidence
    MATCH_NONE = "MATCH_NONE"                         # No matching master question found in library
    MATCH_SKIPPED = "MATCH_SKIPPED"                   # Non-KICE or intentionally excluded artifact


class CandidateStatus(str, Enum):
    """Status of a candidate question match within the matcher domain.
    
    Distinguished from Relation verification_status ('candidate' vs 'verified').
    """
    EXACT = "EXACT"
    AMBIGUOUS = "AMBIGUOUS"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    REJECTED = "REJECTED"


@dataclass
class MatchEvidence:
    """An individual piece of evidence supporting or qualifying a match."""
    kind: str           # e.g., "stem_6digit", "path_subject", "path_track", "heading", "content"
    value: str          # e.g., "260929", "math", "prob_stat"
    strength: str       # "conclusive", "strong", "supporting", "weak"
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def normalized_string(self) -> str:
        return f"{self.kind}:{self.value}:{self.strength}"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MatchEvidence:
        return cls(
            kind=str(data.get("kind", "")),
            value=str(data.get("value", "")),
            strength=str(data.get("strength", "supporting")),
            description=str(data.get("description", "")),
        )


@dataclass
class MatchCandidate:
    """A master question candidate evaluated for an artifact."""
    question_id: str
    origin_key: str
    evidences: List[MatchEvidence] = field(default_factory=list)
    status: str = CandidateStatus.AMBIGUOUS.value
    relation_type_hint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question_id": self.question_id,
            "origin_key": self.origin_key,
            "evidences": [e.to_dict() for e in self.evidences],
            "status": self.status,
            "relation_type_hint": self.relation_type_hint,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MatchCandidate:
        evidences = [
            MatchEvidence.from_dict(e) if isinstance(e, dict) else e
            for e in data.get("evidences", [])
        ]
        return cls(
            question_id=str(data.get("question_id", "")),
            origin_key=str(data.get("origin_key", "")),
            evidences=evidences,
            status=str(data.get("status", CandidateStatus.AMBIGUOUS.value)),
            relation_type_hint=data.get("relation_type_hint"),
        )


def compute_match_fingerprint(
    matcher_version: str,
    artifact_id: str,
    artifact_content_sha256: str,
    decision: str,
    candidates: List[MatchCandidate],
) -> str:
    """Compute deterministic SHA-256 fingerprint for a match report independent of timestamp."""
    normalized_cands = []
    for c in sorted(candidates, key=lambda x: x.question_id):
        norm_evs = sorted(e.normalized_string() for e in c.evidences)
        normalized_cands.append({
            "qid": c.question_id,
            "origin_key": c.origin_key,
            "status": c.status,
            "relation_hint": c.relation_type_hint or "",
            "evidences": norm_evs,
        })
    payload = {
        "matcher_version": matcher_version,
        "artifact_id": artifact_id,
        "content_sha256": artifact_content_sha256,
        "decision": decision,
        "candidates": normalized_cands,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class ArtifactMatchReport:
    """Deterministic, read-only report produced by matching an artifact."""
    schema_version: str = "1.0"
    matcher_version: str = "1.0"
    artifact_id: str = ""
    artifact_source_path: str = ""
    artifact_content_sha256: str = ""
    generated_at: str = ""
    report_fingerprint: str = ""
    decision: str = MatchDecision.MATCH_NONE.value
    candidates: List[MatchCandidate] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.report_fingerprint and self.artifact_id:
            self.report_fingerprint = compute_match_fingerprint(
                self.matcher_version,
                self.artifact_id,
                self.artifact_content_sha256,
                self.decision,
                self.candidates,
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "matcher_version": self.matcher_version,
            "artifact_id": self.artifact_id,
            "artifact_source_path": self.artifact_source_path,
            "artifact_content_sha256": self.artifact_content_sha256,
            "generated_at": self.generated_at,
            "report_fingerprint": self.report_fingerprint,
            "decision": self.decision,
            "candidates": [c.to_dict() for c in self.candidates],
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ArtifactMatchReport:
        candidates = [
            MatchCandidate.from_dict(c) if isinstance(c, dict) else c
            for c in data.get("candidates", [])
        ]
        return cls(
            schema_version=str(data.get("schema_version", "1.0")),
            matcher_version=str(data.get("matcher_version", "1.0")),
            artifact_id=str(data.get("artifact_id", "")),
            artifact_source_path=str(data.get("artifact_source_path", "")),
            artifact_content_sha256=str(data.get("artifact_content_sha256", "")),
            generated_at=str(data.get("generated_at", "")),
            report_fingerprint=str(data.get("report_fingerprint", "")),
            decision=str(data.get("decision", MatchDecision.MATCH_NONE.value)),
            candidates=candidates,
            notes=list(data.get("notes", [])),
        )
