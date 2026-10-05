"""Ephemeral review fields and legacy view projections.

Kept in a dedicated module so uvicorn --reload does not leave Store with
half-updated static methods after multi-file saves.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from ..domain.workbook_content import workbook_presentation
from ..domain.review_state import ReviewState
from ..domain.services.review_service import approval_errors

if TYPE_CHECKING:
    from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord
    from ..domain.job_manifest import JobManifest
    from ..domain.review_state import ReviewState


from ..domain.review_decision import (
    CLASSIFIER_VERSION,
    ReviewDecision,
    ReviewDecisionContext,
    classify_review_decision,
)


def resolve_dependency_status(
    ids: List[str], records: Dict[str, CanonicalQuestionRecord]
) -> Optional[str]:
    """Resolve aggregated human_approval status for dependency relations.

    Returns:
      - None if ids is empty
      - None if any dependency record is missing from records (fail-closed -> NOT_READY)
      - 'approved' if all dependency records have human_approval == 'approved'
      - 'pending' otherwise
    """
    if not ids:
        return None
    deps = [records.get(i) for i in ids]
    if any(dep is None for dep in deps):
        return None
    if all(getattr(dep.review, "human_approval", "") == "approved" for dep in deps if dep is not None):
        return "approved"
    return "pending"


def build_review_decision_context(
    record: CanonicalQuestionRecord,
    records: Dict[str, CanonicalQuestionRecord],
    job_manifest: JobManifest | Dict[str, Any],
) -> ReviewDecisionContext:
    """Build ReviewDecisionContext with job warnings and resolved dependency statuses."""
    manifest_dict = job_manifest.to_dict() if hasattr(job_manifest, "to_dict") else dict(job_manifest)
    job_warnings = tuple(manifest_dict.get("warnings", []))
    passage_status = resolve_dependency_status(record.relations.passage_ids, records)
    script_status = resolve_dependency_status(record.relations.listening_script_ids, records)
    return ReviewDecisionContext(
        job_warnings=job_warnings,
        passage_approval_status=passage_status,
        script_approval_status=script_status,
    )


def build_legacy_item_view(
    record: CanonicalQuestionRecord,
    review_state: ReviewState,
    job_manifest: JobManifest | Dict[str, Any],
    review_decision: Optional[ReviewDecision | Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Project a CanonicalQuestionRecord and ReviewState into legacy item dict format."""
    manifest_dict = job_manifest.to_dict() if hasattr(job_manifest, "to_dict") else dict(job_manifest)
    item_id = record.question.question_id
    rev_id = record.provenance.revision_id or review_state.applies_to_revision_id

    regions: List[Dict[str, Any]] = []
    solution_regions: List[Dict[str, Any]] = []

    for r in record.provenance.source_regions:
        role = r.get("content_role")
        r_copy = {k: v for k, v in r.items() if k != "content_role"}
        if role == "solution":
            solution_regions.append(r_copy)
        else:
            regions.append(r_copy)

    passage_id = record.relations.passage_ids[0] if record.relations.passage_ids else ""
    listening_script_id = (
        record.relations.listening_script_ids[0] if record.relations.listening_script_ids else ""
    )
    selected_solution_id = (
        review_state.selected_solution_id
        or (record.relations.solution_ids[0] if record.relations.solution_ids else "")
    )

    review_assets_by_id = {}
    for a in review_state.assets:
        if isinstance(a, dict):
            aid = a.get("id") or a.get("asset_id")
            if aid:
                review_assets_by_id[aid] = a
                if ":" in aid:
                    review_assets_by_id[aid.split(":", 1)[1]] = a

    assets = []
    for a in record.assets:
        d = asdict(a)
        orig_id = a.asset_id
        if ":" in orig_id:
            orig_id = orig_id.split(":", 1)[1]
        d["id"] = orig_id
        if a.asset_id in review_assets_by_id:
            rev_a = review_assets_by_id[a.asset_id]
            for extra in ("crop", "structured"):
                if extra in rev_a:
                    d[extra] = rev_a[extra]
        elif orig_id in review_assets_by_id:
            rev_a = review_assets_by_id[orig_id]
            for extra in ("crop", "structured"):
                if extra in rev_a:
                    d[extra] = rev_a[extra]
        assets.append(d)

    content_kind = record.question.content_kind
    legacy_kind = "question" if content_kind == "example" else content_kind
    subtype = "example" if content_kind == "example" else ""

    item: Dict[str, Any] = {
        "id": item_id,
        "revision": rev_id,
        "kind": legacy_kind,
        "subtype": subtype,
        "context": record.question.question_set_id or (
            record.source.source_set_id or record.source.section_code or ""
        ),
        "number": record.question.question_number,
        "section_code": record.source.section_code or "",
        "section_title": record.source.section_title or "",
        "item_code": record.source.item_code or "",
        "body": record.body,
        "solution": record.solution,
        "answer": record.question.answer or "",
        "points": record.question.points,
        "correct_rate": record.question.correct_rate,
        "unit": record.unit.display_name or "",
        "unit_code": record.unit.code,
        "unit_path": list(record.unit.path),
        "curriculum_id": record.unit.taxonomy_id or "",
        "q_type": record.question.question_type or "",
        "passage_id": passage_id,
        "listening_script_id": listening_script_id,
        "selected_solution_id": selected_solution_id,
        "regions": regions,
        "solution_regions": solution_regions,
        "classification_status": record.classification.status,
        "classification_method": record.classification.method or "unclassified",
        "classification_candidates": list(record.classification.candidates),
        "classification_evidence": list(record.classification.evidence),
        "curriculum_revision": record.classification.curriculum_revision or "",
        "assets": assets,
        "source_styles": list(review_state.source_styles),
        "style_mapping_errors": list(review_state.style_mapping_errors),
        "group_mapping_errors": list(review_state.group_mapping_errors),
        "box_checks": list(review_state.box_checks),
        "solution_candidates": list(review_state.solution_candidates),
        "solution_match": review_state.solution_match,
        "solution_link_backup": review_state.solution_link_backup,
        "ai_runs": list(review_state.ai_runs),
        "review": record.review.human_approval,
        "published_revision": review_state.published_revision_id,
        "audit": review_state.audit,
        "audit_waiver": review_state.audit_waiver,
        "note": review_state.note or "",
        "warnings": list(review_state.warnings),
        "quality_checks": list(review_state.quality_checks),
        "history": list(review_state.history),
        "extracted_revision": review_state.extracted_revision,
        "reference_text": review_state.reference_text,
        "transcription_pending": list(review_state.transcription_pending),
        "modality": record.question.modality or "",
        "question_set_id": record.question.question_set_id or "",
        "strategy_tags": list(record.question.strategy_tags),
        "content_type": record.question.content_type or "",
        "material_validation_errors": list(review_state.material_validation_errors),
    }

    if record.origin:
        item["origin"] = asdict(record.origin)

    if review_decision is not None:
        item["review_decision"] = (
            review_decision.to_dict() if hasattr(review_decision, "to_dict") else dict(review_decision)
        )

    return item


def build_legacy_job_view(
    manifest: JobManifest | Dict[str, Any],
    records: Dict[str, CanonicalQuestionRecord],
    review_states: Dict[str, ReviewState],
    runtime_state: Optional[Any] = None,
) -> Dict[str, Any]:
    """Combine job manifest, record repositories, and runtime state into a legacy job dict."""
    manifest_dict = manifest.to_dict() if hasattr(manifest, "to_dict") else copy.deepcopy(manifest)
    record_ids = manifest_dict.get("record_ids", [])

    if runtime_state is not None:
        r_dict = runtime_state.to_dict() if hasattr(runtime_state, "to_dict") else dict(runtime_state)
        for k in ("task", "queue", "queue_history", "ai_log", "ai_progress"):
            if k in r_dict:
                manifest_dict[k] = r_dict[k]

    pending_tiers = {"NOT_READY": 0, "RED": 0, "YELLOW": 0, "GREEN": 0}
    approved_tiers = {"NOT_READY": 0, "RED": 0, "YELLOW": 0, "GREEN": 0}
    held_tiers = {"NOT_READY": 0, "RED": 0, "YELLOW": 0, "GREEN": 0}
    approved_total = 0
    held_total = 0

    items: List[Dict[str, Any]] = []
    for rid in record_ids:
        rec = records.get(rid)
        if not rec:
            raise ValueError(f"Storage corruption: record '{rid}' missing from records directory")
        rev_state = review_states.get(rid) or ReviewState(record_id=rid, applies_to_revision_id="")
        context = build_review_decision_context(rec, records, manifest_dict)
        decision = classify_review_decision(rec, rev_state, context=context)

        approval = getattr(rec.review, "human_approval", "")
        if approval not in {"pending", "approved", "held"}:
            raise ValueError(f"Invalid human_approval status '{approval}' on record '{rid}'")

        tier = decision.tier.value if hasattr(decision.tier, "value") else str(decision.tier)
        if tier not in {"NOT_READY", "RED", "YELLOW", "GREEN"}:
            raise ValueError(f"Invalid ReviewDecision tier '{tier}' on record '{rid}'")

        if approval == "pending":
            pending_tiers[tier] += 1
        elif approval == "approved":
            approved_total += 1
            approved_tiers[tier] += 1
        elif approval == "held":
            held_total += 1
            held_tiers[tier] += 1

        item_view = build_legacy_item_view(rec, rev_state, manifest_dict, review_decision=decision)
        # Store.review currently passes the first linked dependency to approve().
        def first_status(ids):
            dependency = records.get(ids[0]) if ids else None
            return dependency.review.human_approval if dependency else None
        errors = approval_errors(
            rec, rev_state, rev_state.note,
            passage_approval_status=first_status(rec.relations.passage_ids),
            script_approval_status=first_status(rec.relations.listening_script_ids),
            job_warnings=manifest_dict.get("warnings", []),
        )
        item_view["approval_gate"] = {"available": True, "eligible": not errors,
                                      "blockers": errors, "basis_revision": rec.provenance.revision_id}
        items.append(item_view)

    total_items = len(record_ids)
    if sum(pending_tiers.values()) + approved_total + held_total != total_items:
        raise ValueError("Review queue summary invariant failed: sum of items does not match total")

    manifest_dict["review_queue_summary"] = {
        "status": "computed",
        "pending": pending_tiers,
        "approved": {
            "total": approved_total,
            "by_tier": approved_tiers,
        },
        "held": {
            "total": held_total,
            "by_tier": held_tiers,
        },
        "total_items": total_items,
        "classifier_version": CLASSIFIER_VERSION,
    }

    manifest_dict["items"] = items
    attach_workbook_views(manifest_dict)
    attach_display_titles(manifest_dict)
    return manifest_dict


def attach_workbook_views(job) -> None:
    if job.get("format") == "hanwangi_2026_probability" and job.get("workbook"):
        for item in job.get("items", []):
            item["workbook_view"] = workbook_presentation(item)


def attach_display_titles(job) -> None:
    from ..exporters.profiles.markdown import format_display_title, preview_sections
    from _scripts_2.apps.exam.exam_processor.domain.canonical_mapping import item_to_canonical

    for item in job.get("items", []):
        if item.get("kind") != "question":
            item.pop("display_title", None)
            continue
        try:
            record = item_to_canonical(job, item)
            item["display_title"] = format_display_title(record)
            options = {}
            if job.get("format") == "kice_english":
                from ..exporters.english_export import output_stem
                by_id = {entry["id"]: entry for entry in job.get("items", [])}
                def dependency_path(rid, kind):
                    dependency = by_id.get(rid)
                    if not dependency or dependency.get("kind") != kind:
                        raise ValueError("연결된 지문/대본을 찾을 수 없습니다.")
                    return f"../{kind}s/{output_stem(dependency['number'])}.md"
                passages = record.relations.passage_ids
                options["passage_file"] = dependency_path(passages[0], "passage") if passages else ""
                scripts = record.relations.listening_script_ids
                options["script_files"] = [dependency_path(rid, "script") for rid in scripts]
                options["script_bodies"] = {dependency_path(rid, "script"): by_id[rid].get("body", "") for rid in scripts}
            item["standard_preview"] = preview_sections(record, **options)
        except (ValueError, TypeError, KeyError, AttributeError):
            # Queue unavailability must remain visible even with malformed metadata.
            item.pop("display_title", None)
            item["standard_preview"] = {"status": "unavailable", "warnings": ["표준 표시를 계산할 수 없습니다."]}


def verify_store_views_contract() -> None:
    """Raise when the running process cannot attach ephemeral views."""
    attach_workbook_views({"format": "hanwangi_2026_probability", "workbook": {}, "items": []})
    attach_display_titles({"items": []})


def attach_legacy_review_projection(job: Dict[str, Any]) -> None:
    """Adapt v1 evidence in memory; retain every original item field and history.

    The synthetic basis is a content fingerprint, never a persisted revision.
    Invalid legacy evidence yields an explicit unavailable queue, never GREEN.
    """
    from _scripts_2.apps.exam.exam_processor.domain.canonical_mapping import item_to_canonical

    for item in job.get("items", []):
        item.pop("review_decision", None)
        item["approval_gate"] = {
            "available": False, "eligible": False,
            "blockers": ["이전 형식 작업입니다. 승인하려면 저장 형식 전환의 별도 검증·승인이 필요합니다."],
            "basis_revision": item.get("revision", ""),
        }
    try:
        records, states, ids = {}, {}, []
        for item in job.get("items", []):
            rid = item.get("id")
            if not rid or rid in records or not item.get("revision"):
                raise ValueError("문항 ID 또는 revision이 없거나 중복됩니다.")
            if item.get("review") not in {"pending", "approved", "held"}:
                raise ValueError("검수 상태가 없거나 지원되지 않습니다.")
            records[rid] = item_to_canonical(job, item)
            evidence = {**item, "record_id": rid, "applies_to_revision_id": item["revision"]}
            evidence["state_revision_id"] = "legacy-sha256:" + hashlib.sha256(
                json.dumps(item, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            states[rid] = ReviewState.from_dict(evidence)
            ids.append(rid)
        projected = build_legacy_job_view({**job, "record_ids": ids}, records, states)
        for original, view in zip(job.get("items", []), projected["items"]):
            original["review_decision"] = view["review_decision"]
            original["approval_gate"]["review_blockers"] = view["approval_gate"]["blockers"]
            original["approval_gate"]["review_eligible"] = view["approval_gate"]["eligible"]
        job["review_queue_summary"] = projected["review_queue_summary"]
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        job["review_queue_summary"] = {
            "status": "unavailable", "reason": "legacy 검수 근거를 분류할 수 없습니다: " + str(exc),
            "total_items": len(job.get("items", [])), "classifier_version": CLASSIFIER_VERSION,
        }
