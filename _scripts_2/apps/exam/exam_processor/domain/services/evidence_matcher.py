"""EvidenceMatcher — deterministic, dry-run candidate matcher for KS artifacts.

Invariants:
1. Pure Dry-Run: NEVER mutates master records, relations, or artifacts.
2. Canonical Origin Keys & Index Integrity: Verifies master.canonical_origin_key() == origin_index.get_key(qid). Fails closed on missing master or stale index.
3. Strict Exact vs Single Semantics: MATCH_EXACT_ORIGIN requires is_fully_qualified (authority, year, session, subject, question_number, and track/exam_form if required). If wildcards were used, emits MATCH_SINGLE_CANDIDATE (CandidateStatus.MANUAL_REVIEW).
4. Ambiguity Preservation: If multiple candidates match (e.g. elective without track), emits MATCH_AMBIGUOUS.
5. Deterministic Fingerprinting: Embeds SHA-256 fingerprint in report independent of generation timestamp.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from _scripts_2.apps.exam.exam_core.domain.artifact_models import ArtifactRecord
from ..match_models import (
    ArtifactMatchReport,
    CandidateStatus,
    MatchCandidate,
    MatchDecision,
)
from .origin_evidence_parser import OriginEvidenceParser
from _scripts_2.apps.exam.exam_core.indexing.origin_index import OriginIndex
from _scripts_2.apps.exam.exam_core.storage.kice_master_repository import KiceMasterRepository


class OriginIndexIntegrityError(RuntimeError):
    """Raised when OriginIndex references a non-existent Master or contains stale origin keys."""
    pass


class EvidenceMatcher:
    """Matches legacy KS note artifacts to KICE Master questions based on parsed origin evidence."""

    def __init__(
        self,
        master_repo: KiceMasterRepository,
        origin_index: OriginIndex,
        parser: Optional[OriginEvidenceParser] = None,
    ) -> None:
        self._master_repo = master_repo
        self._origin_index = origin_index
        self._parser = parser or OriginEvidenceParser()

    def match_artifact(self, artifact: ArtifactRecord) -> ArtifactMatchReport:
        """Evaluate an artifact against KICE master records and return a deterministic report.
        
        Zero disk mutation invariant: this method does not write any files.
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        if not artifact.present:
            return ArtifactMatchReport(
                artifact_id=artifact.artifact_id,
                artifact_source_path=artifact.source_path,
                artifact_content_sha256=artifact.content_sha256,
                generated_at=now_iso,
                decision=MatchDecision.MATCH_SKIPPED.value,
                notes=["Artifact is marked absent/deleted in vault."],
            )

        parsed = self._parser.parse_path(artifact.source_path)

        if not parsed.is_kice:
            return ArtifactMatchReport(
                artifact_id=artifact.artifact_id,
                artifact_source_path=artifact.source_path,
                artifact_content_sha256=artifact.content_sha256,
                generated_at=now_iso,
                decision=MatchDecision.MATCH_SKIPPED.value,
                notes=[parsed.rejection_reason or "Artifact rejected as non-KICE."],
            )

        # Query candidates from OriginIndex
        cand_qids = self._origin_index.find_candidates(
            authority=parsed.authority,
            academic_year=parsed.academic_year,
            session=parsed.session,
            subject=parsed.subject,
            question_number=parsed.question_number,
            track=parsed.track,
            exam_form=parsed.exam_form if parsed.exam_form else None,
        )

        candidates: list[MatchCandidate] = []
        for qid in cand_qids:
            # 1. Missing Master validation (Fail-Closed)
            if not self._master_repo.exists(qid):
                raise OriginIndexIntegrityError(
                    f"OriginIndex references missing master question '{qid}'"
                )
            master = self._master_repo.get(qid)

            # 2. Stale Origin Key validation (Fail-Closed)
            indexed_key = self._origin_index.get_key(qid)
            master_key = master.canonical_origin_key()
            if indexed_key != master_key:
                raise OriginIndexIntegrityError(
                    f"OriginIndex stale for question '{qid}': "
                    f"index maps to '{indexed_key}', but master canonical key is '{master_key}'"
                )

            candidate = MatchCandidate(
                question_id=qid,
                origin_key=master_key,
                evidences=list(parsed.evidences),
                status=CandidateStatus.AMBIGUOUS.value,
                relation_type_hint=parsed.relation_type_hint,
            )
            candidates.append(candidate)

        # Determine overall match decision
        if len(candidates) == 1:
            if parsed.is_fully_qualified:
                candidates[0].status = CandidateStatus.EXACT.value
                decision = MatchDecision.MATCH_EXACT_ORIGIN.value
                notes = [f"Exactly 1 fully-qualified master question matched: {candidates[0].origin_key}"]
            else:
                candidates[0].status = CandidateStatus.MANUAL_REVIEW.value
                decision = MatchDecision.MATCH_SINGLE_CANDIDATE.value
                notes = [
                    f"Single candidate found in current library ({candidates[0].origin_key}), "
                    "but origin was not fully qualified (wildcards used or unverified historical scheme). "
                    "Manual review required."
                ]
        elif len(candidates) > 1:
            for c in candidates:
                c.status = CandidateStatus.AMBIGUOUS.value
            decision = MatchDecision.MATCH_AMBIGUOUS.value
            notes = [
                f"Multiple ({len(candidates)}) master questions matched for elective or overlapping origin. "
                "Explicit track/form qualification required."
            ]
        else:
            decision = MatchDecision.MATCH_NONE.value
            notes = [
                f"No master question found in library matching origin: "
                f"year={parsed.academic_year}, session={parsed.session}, q={parsed.question_number}, subj={parsed.subject}"
            ]

        return ArtifactMatchReport(
            artifact_id=artifact.artifact_id,
            artifact_source_path=artifact.source_path,
            artifact_content_sha256=artifact.content_sha256,
            generated_at=now_iso,
            decision=decision,
            candidates=candidates,
            notes=notes,
        )
