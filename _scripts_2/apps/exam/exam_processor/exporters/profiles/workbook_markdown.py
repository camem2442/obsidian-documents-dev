"""Workbook-specific preparation before the common Markdown projection."""
from __future__ import annotations

from typing import Any, Dict

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord, OriginDomain
from ...domain.workbook_content import workbook_presentation


def prepare_workbook_record(
    record: CanonicalQuestionRecord,
    job: Dict[str, Any] | Any,
    item: Dict[str, Any] | Any = None,
) -> CanonicalQuestionRecord:
    """Move recognized leading labels to metadata, preserving the raw job text."""
    job_dict = job.to_dict() if hasattr(job, "to_dict") else dict(job)
    raw_meta = getattr(job, "raw_metadata", {})
    workbook_spec = job_dict.get("workbook") or raw_meta.get("workbook")
    fmt = job_dict.get("format") or getattr(job, "format", "")

    if fmt != "hanwangi_2026_probability" or not workbook_spec:
        return record

    from ...domain.standard_content import populate_standard_metadata
    warnings = populate_standard_metadata(record)
    if warnings:
        raise ValueError("; ".join(warnings))

    nodes = workbook_spec.get("nodes", [])
    target_node_id = (
        (item.get("context") if isinstance(item, dict) else "")
        or record.source.source_set_id
        or record.source.section_code
    )
    node = next((n for n in nodes if n.get("id") == target_node_id), None)
    if node:
        record.source.section_code = node.get("section_code") or record.source.section_code
        record.unit.display_name = node.get("section_title") or record.unit.display_name
        if node.get("section_title") and not record.unit.path:
            record.unit.path = [node["section_title"]]
    return record
