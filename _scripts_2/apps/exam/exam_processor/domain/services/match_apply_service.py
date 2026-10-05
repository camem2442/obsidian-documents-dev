"""MatchApplyService — applies verified candidate matches to Global Knowledge Relations.

Follows strict apply-time safety invariants:
1. Artifact Content Freshness: Verifies artifact content_sha256 has not changed since report generation.
2. Index Re-evaluation: Re-runs EvidenceMatcher at apply-time to confirm candidate eligibility against current library state.
3. Master Verification: Confirms target question exists in KiceMasterRepository.
4. Deterministic Link IDs: Uses uuid5(NAMESPACE, f"{question_id}:{artifact_id}") for idempotency.
5. Evidence & Provenance Preservation: Embeds normalized evidence, matcher version, and report timestamp in ArtifactLinkRef.
6. Optimistic Concurrency: Honors expected_revision on QuestionLinks mutations.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from ..match_models import ArtifactMatchReport
from _scripts_2.apps.exam.exam_core.domain.relation_models import ArtifactLinkRef, QuestionLinks
from .evidence_matcher import EvidenceMatcher
from _scripts_2.apps.exam.exam_core.domain.services.relation_service import RelationService
from _scripts_2.apps.exam.exam_core.storage.artifact_repository import ArtifactRepository
from _scripts_2.apps.exam.exam_core.storage.kice_master_repository import KiceMasterRepository

ARTIFACT_LINK_NAMESPACE = uuid.UUID("e3f89021-99c5-4a24-9b2f-37604b361a91")


class MatchApplyService:
    """Service to safely apply approved match report candidates to Knowledge Relations."""

    def __init__(
        self,
        artifact_repo: ArtifactRepository,
        relation_service: RelationService,
        matcher: EvidenceMatcher,
        master_repo: KiceMasterRepository,
    ) -> None:
        self._artifact_repo = artifact_repo
        self._relation_service = relation_service
        self._matcher = matcher
        self._master_repo = master_repo

    def apply_candidate(
        self,
        report: ArtifactMatchReport,
        candidate_question_id: str,
        relation_type: str,
        expected_relation_revision: Optional[str] = None,
    ) -> QuestionLinks:
        """Apply an approved match candidate by linking the artifact to the master question."""
        # 1. Validate that the candidate was part of the original report
        matching_cand = next((c for c in report.candidates if c.question_id == candidate_question_id), None)
        if matching_cand is None:
            raise ValueError(
                f"Candidate question '{candidate_question_id}' was not in match report for artifact '{report.artifact_id}'"
            )

        # 2. Check artifact presence and content SHA-256 staleness
        artifact = self._artifact_repo.get_by_id(report.artifact_id)
        if artifact is None or not artifact.present:
            raise ValueError(f"Artifact '{report.artifact_id}' is no longer present in repository")

        if artifact.content_sha256 != report.artifact_content_sha256:
            raise ValueError(
                f"Artifact '{report.artifact_id}' content changed since report generation: "
                f"current={artifact.content_sha256} vs report={report.artifact_content_sha256}"
            )

        # 3. Re-run matcher to ensure candidate eligibility against current library index
        fresh_report = self._matcher.match_artifact(artifact)
        fresh_cand = next((c for c in fresh_report.candidates if c.question_id == candidate_question_id), None)
        if fresh_cand is None:
            raise ValueError(
                f"Stale match: Candidate '{candidate_question_id}' is no longer eligible under current index state"
            )

        # 4. Master existence validation
        if not self._master_repo.exists(candidate_question_id):
            raise ValueError(f"Master record '{candidate_question_id}' does not exist in library")

        # 5. Deterministic link ID and metadata preservation
        link_id = uuid.uuid5(ARTIFACT_LINK_NAMESPACE, f"{candidate_question_id}:{artifact.artifact_id}").hex
        evidence_strings = [e.normalized_string() for e in fresh_cand.evidences]
        now_iso = datetime.now(timezone.utc).isoformat()

        link_ref = ArtifactLinkRef(
            link_id=link_id,
            artifact_id=artifact.artifact_id,
            source_path=artifact.source_path,
            relation_type=relation_type,
            verification_status="candidate",
            evidence=evidence_strings,
            matcher_version=report.matcher_version,
            report_generated_at=report.generated_at,
            applied_at=now_iso,
        )

        # 6. Apply relation link through RelationService (idempotent)
        return self._relation_service.link_artifact(
            question_id=candidate_question_id,
            artifact=link_ref,
            expected_revision=expected_relation_revision,
        )
