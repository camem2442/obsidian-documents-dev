"""Map runtime job items to canonical records."""
from __future__ import annotations

import json
import mimetypes
import re
import uuid
from typing import Any, Dict

from _scripts_2.apps.exam.exam_core.domain.canonical.schema import (
    SCHEMA_VERSION,
    SOURCE_AUDIT_RESULTS,
    AssetDomain,
    CanonicalQuestionRecord,
    ClassificationDomain,
    OriginDomain,
    ProvenanceDomain,
    QuestionDomain,
    RelationsDomain,
    ReviewDomain,
    SourceDomain,
    SourceLocator,
    UnitDomain,
)
from _scripts_2.apps.exam.exam_processor.domain.rich_content import image_description_from_markdown


def map_transcription(item: Dict[str, Any]) -> str:
    body = str(item.get("body", "")).strip()
    if item.get("transcription_pending") or not body or body == "[전사 대기]":
        return "pending"
    return "completed"


def map_source_audit(item: Dict[str, Any]) -> str:
    """Normalize runtime audit payload to review.source_audit."""
    from _scripts_2.apps.exam.exam_processor.domain.models import audit_waiver_current

    if audit_waiver_current(item):
        return "waived"
    audit = item.get("audit") or {}
    if not item.get("revision") or audit.get("revision") != item["revision"]:
        return "pending"
    result = audit.get("result")
    if result in SOURCE_AUDIT_RESULTS:
        return result
    status = audit.get("status")
    if status == "completed":
        return "no_difference"
    if status in ("failed", "error"):
        return "mismatch"
    return "pending"


def item_to_canonical(job: Dict[str, Any], item: Dict[str, Any], *, display_code=None) -> CanonicalQuestionRecord:
    """Convert an internal runtime item dictionary into a CanonicalQuestionRecord."""
    source_title = job.get("source", "")
    track = job.get("track", "")
    subject = item.get("subject") or job.get("subject") or (
        "영어" if track == "영어" or job.get("format") == "kice_english"
        else "수학" if track == "확률과 통계" or "hanwangi" in job.get("format", "")
        else "국어"
    )
    q_num = str(item.get("number", ""))

    source_type = job.get("source_type") or ("workbook" if job.get("workbook") else
                                           "exam" if job.get("format") == "kice_korean" else "other")
    exam = re.search(r"(\d{4})학년도\s*(\d{1,2})월", source_title)
    if exam and source_type == "other" and not job.get("source_type"):
        source_type = "exam"
    if display_code is None:
        display_code = item.get("display_code")
        if not display_code and source_type == "exam" and exam:
            display_code = f"{exam[1][-2:]}{int(exam[2]):02d}{q_num.zfill(2)}" if q_num else None
        display_code = display_code or item.get("item_code") or q_num or item.get("id", "")
    source_id = job.get("source_id") or uuid.uuid5(
        uuid.NAMESPACE_URL, json.dumps(["source", source_title, job.get("track", "")], ensure_ascii=False)
    ).hex
    regions = item.get("regions", [])
    document_id = job.get("document_id") or next((r.get("document", "") for r in regions), "")

    question = QuestionDomain(
        question_id=item.get("id", ""),
        display_code=display_code,
        subject=subject,
        content_kind="example" if item.get("subtype") == "example" else item.get("kind", "question"),
        question_number=q_num,
        points=item.get("points"),
        correct_rate=item.get("correct_rate"),
        answer=item.get("answer") or None,
        question_type=item.get("q_type") or None,
        difficulty=item.get("difficulty"),
        status=item.get("status", "unread"),
        content_type=item.get("content_type", ""),
        variant_of=item.get("variant_of") or [],
        modality=item.get("modality", ""),
        question_set_id=item.get("question_set_id") or (
            item.get("context", "") if source_type != "workbook" and item.get("context") != item.get("section_code") else ""
        ),
        strategy_tags=item.get("strategy_tags") or [],
    )

    source = SourceDomain(
        source_id=source_id,
        type=source_type,
        title=source_title,
        format_id=job.get("format", ""),
        edition=job.get("edition") if source_type == "workbook" else None,
        section_code=(
            item.get("section_code") or (item.get("context") if source_type == "workbook" else None)
        ) if source_type == "workbook" else None,
        section_title=item.get("section_title") if source_type == "workbook" else None,
        item_code=item.get("item_code") if source_type == "workbook" else None,
        source_set_id=item.get("source_set_id") or (
            item.get("context", "") if source_type == "workbook" else job.get("source_set_id", "")
        ),
        exam_code=job.get("exam_code", ""),
        exam_form=job.get("exam_form", ""),
        locator=SourceLocator(document_id=document_id,
                              pdf_pages=sorted({r["page"] for r in regions if r.get("document", "") == document_id and "page" in r})),
    )

    unit = UnitDomain(
        display_name=item.get("unit") or None,
        path=item.get("unit_path") or ([item["unit"]] if item.get("unit") else []),
        code=item.get("unit_code") or None,
        taxonomy_id=item.get("curriculum_id") or None,
    )

    relations = RelationsDomain(
        passage_ids=[item["passage_id"]] if item.get("passage_id") else [],
        solution_ids=[],
        listening_script_ids=[item["listening_script_id"]] if item.get("listening_script_id") else [],
    )

    provenance = ProvenanceDomain(
        revision_id=item.get("revision", ""),
        source_record_ids=[item["selected_solution_id"]] if item.get("selected_solution_id") else [],
        source_regions=(
            [dict(region, content_role="body") for region in item.get("regions", [])]
            + [dict(region, content_role="solution") for region in item.get("solution_regions", [])]
        ),
        source_hashes=job.get("source_hashes") or [],
    )

    seen_asset_ids = set()
    assets = []
    for asset in item.get("assets", []):
        sec = asset.get("section") or ("solution" if "solution" in str(asset.get("path", "")) else "body")
        base_id = str(asset.get("id") or asset.get("path", ""))
        a_id = base_id
        if a_id in seen_asset_ids:
            a_id = f"{sec}:{base_id}"
        if a_id in seen_asset_ids:
            a_id = str(asset.get("path", ""))
        seen_asset_ids.add(a_id)
        assets.append(AssetDomain(
            asset_id=a_id,
            section=sec,
            source_path=asset.get("path", ""),
            path=asset.get("path", ""),
            media_type=mimetypes.guess_type(asset.get("path", ""))[0] or "application/octet-stream",
            description=str(
                asset.get("description")
                or image_description_from_markdown(
                    item.get(sec, ""), asset.get("path", "")
                )
                or ""
            ),
            sha256=asset.get("sha256"),
        ))

    classification = ClassificationDomain(
        status=item.get("classification_status", "unclassified"),
        method={"unclassified": None, "workbook_toc_confirmed": "workbook_toc",
                "curriculum_ai_candidate": "curriculum_ai"}.get(item.get("classification_method"), item.get("classification_method") or None),
        candidates=item.get("classification_candidates", []),
        evidence=item.get("classification_evidence", []),
        curriculum_revision=item.get("curriculum_revision") or None,
    )

    review = ReviewDomain(
        transcription=map_transcription(item),
        source_audit=map_source_audit(item),
        classification={"unclassified": "not_started"}.get(item.get("classification_status"), item.get("classification_status", "not_started")),
        human_approval=item.get("review", "pending"),
    )

    record = CanonicalQuestionRecord(
        schema_version=SCHEMA_VERSION,
        question=question,
        source=source,
        origin=OriginDomain(**(item.get("origin") or job.get("origin")))
        if item.get("origin") or job.get("origin") else None,
        unit=unit,
        relations=relations,
        provenance=provenance,
        classification=classification,
        review=review,
        assets=assets,
        body=item.get("body", ""),
        solution=item.get("solution", ""),
    )

    from _scripts_2.apps.exam.exam_processor.domain.standard_content import populate_standard_metadata
    populate_standard_metadata(record)
    return record
