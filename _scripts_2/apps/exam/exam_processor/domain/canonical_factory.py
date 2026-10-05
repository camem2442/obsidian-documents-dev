"""CanonicalQuestionRecord & ReviewState creation factories for parsers."""
from __future__ import annotations

import copy
import uuid
from typing import Any, Dict, List, Optional

from _scripts_2.apps.exam.exam_core.domain.canonical.schema import (
    AssetDomain,
    CanonicalQuestionRecord,
    ClassificationDomain,
    OriginDomain,
    ProvenanceDomain,
    QuestionDomain,
    RelationsDomain,
    ReviewDomain,
    SCHEMA_VERSION,
    SourceDomain,
    SourceLocator,
    UnitDomain,
)
from _scripts_2.apps.exam.exam_processor.domain.review_state import ReviewState


def create_canonical_record(
    *,
    record_id: str,
    subject: str,
    source_id: str,
    source_type: str,
    source_title: str,
    number: str = "",
    content_kind: str = "question",
    body: str = "",
    solution: str = "",
    answer: Optional[str] = None,
    points: Optional[float] = None,
    correct_rate: Optional[float] = None,
    question_type: Optional[str] = None,
    difficulty: Optional[int] = None,
    modality: str = "",
    content_type: str = "",
    question_set_id: str = "",
    strategy_tags: Optional[List[str]] = None,
    # Source metadata
    format_id: str = "",
    edition: Optional[str] = None,
    section_code: Optional[str] = None,
    section_title: Optional[str] = None,
    item_code: Optional[str] = None,
    source_set_id: str = "",
    exam_code: str = "",
    exam_form: str = "",
    document_id: str = "",
    pdf_pages: Optional[List[int]] = None,
    # Origin
    origin: Optional[OriginDomain] = None,
    # Unit
    unit_name: Optional[str] = None,
    unit_path: Optional[List[str]] = None,
    unit_code: Optional[str] = None,
    taxonomy_id: Optional[str] = None,
    # Relations
    passage_ids: Optional[List[str]] = None,
    solution_ids: Optional[List[str]] = None,
    listening_script_ids: Optional[List[str]] = None,
    # Provenance
    source_regions: Optional[List[Dict[str, Any]]] = None,
    source_hashes: Optional[List[str]] = None,
    # Assets
    assets: Optional[List[AssetDomain]] = None,
    # Classification
    classification_status: str = "unclassified",
    classification_method: Optional[str] = None,
    classification_candidates: Optional[List[Dict[str, Any]]] = None,
    classification_evidence: Optional[List[str]] = None,
    # Revision
    revision_id: Optional[str] = None,
    # ReviewState initial evidence
    transcription_pending: Optional[List[str]] = None,
    review_warnings: Optional[List[str]] = None,
    reference_text: str = "",
    source_styles: Optional[List[Dict[str, Any]]] = None,
    solution_candidates: Optional[List[Dict[str, Any]]] = None,
    solution_match: Optional[Dict[str, Any]] = None,
) -> tuple[CanonicalQuestionRecord, ReviewState]:
    """Create paired (CanonicalQuestionRecord, ReviewState) with strict domain boundaries."""
    rev_id = revision_id or uuid.uuid4().hex

    # Determine transcription pending list
    if transcription_pending is not None:
        pending = list(transcription_pending)
    else:
        pending = ["body"] if (not body.strip() or body == "[전사 대기]") else []

    question = QuestionDomain(
        question_id=record_id,
        display_code=str(number) if number else record_id,
        subject=subject,
        content_kind=content_kind,
        question_number=str(number),
        points=points,
        correct_rate=correct_rate,
        answer=answer,
        question_type=question_type,
        difficulty=difficulty,
        status="unread",
        content_type=content_type,
        modality=modality,
        question_set_id=question_set_id,
        strategy_tags=strategy_tags or [],
    )

    source = SourceDomain(
        source_id=source_id,
        type=source_type,
        title=source_title,
        format_id=format_id,
        edition=edition,
        section_code=section_code,
        section_title=section_title,
        item_code=item_code,
        source_set_id=source_set_id,
        exam_code=exam_code,
        exam_form=exam_form,
        locator=SourceLocator(
            document_id=document_id,
            pdf_pages=sorted(list(set(pdf_pages or []))),
        ),
    )

    unit = UnitDomain(
        display_name=unit_name,
        path=unit_path or ([unit_name] if unit_name else []),
        code=unit_code,
        taxonomy_id=taxonomy_id,
    )

    relations = RelationsDomain(
        passage_ids=list(passage_ids or []),
        solution_ids=list(solution_ids or []),
        listening_script_ids=list(listening_script_ids or []),
    )

    provenance = ProvenanceDomain(
        revision_id=rev_id,
        source_record_ids=[],
        source_regions=list(source_regions or []),
        source_hashes=list(source_hashes or []),
    )

    classification = ClassificationDomain(
        status=classification_status,
        method=classification_method,
        candidates=list(classification_candidates or []),
        evidence=list(classification_evidence or []),
    )

    review = ReviewDomain(
        transcription="pending" if pending else "completed",
        source_audit="pending",
        classification="confirmed" if classification_status == "confirmed" else "not_started",
        human_approval="pending",
    )

    record = CanonicalQuestionRecord(
        schema_version=SCHEMA_VERSION,
        question=question,
        source=source,
        origin=origin,
        unit=unit,
        relations=relations,
        provenance=provenance,
        classification=classification,
        review=review,
        assets=list(assets or []),
        body=body,
        solution=solution,
    )

    review_state = ReviewState(
        record_id=record_id,
        applies_to_revision_id=rev_id,
        published_revision_id="",
        transcription_pending=pending,
        warnings=list(review_warnings or []),
        reference_text=reference_text,
        source_styles=list(source_styles or []),
        solution_candidates=list(solution_candidates or []),
        solution_match=copy.deepcopy(solution_match) if solution_match else None,
    )

    return record, review_state
