"""Obsidian Markdown Output Profile Adapter.

Projects a CanonicalQuestionRecord (or compatible item) into
Obsidian-compatible Markdown note with flat Frontmatter and SECTION markers.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord
from ...domain.models import format_output_markdown, yaml_scalar


def _number(value: str) -> str:
    value = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z]\d+[·.]\d+", value):
        value = re.sub(r"[·.]", "-", value)
    return value if value.endswith("번") else f"{value}번" if value else ""


def format_origin_title(origin, subject: str = "", exam_form: str = "") -> str:
    """Render only source facts present in the canonical origin record."""
    if not origin:
        return ""
    year = f"{origin.academic_year}학년도" if origin.academic_year else ""
    session = str(origin.session or "").strip()
    if session.isdigit() and int(session) == 11:
        event = "수능" if origin.authority == "KICE" else "11월"
    elif session.isdigit():
        event = f"{int(session)}월"
    else:
        event = session
    authority = {"KICE": "평가원", "EDU": "교육청", "POLICE": "사관/경찰"}.get(
        origin.authority or "", origin.authority or ""
    )
    if event == "수능":
        authority = ""
    variants = origin.variants or ([{"track": origin.track, "question_number": origin.question_number}]
                                   if origin.question_number else [])
    items = []
    for variant in variants:
        track = str(variant.get("track") or "").strip()
        if subject == "영어" and track in ("", "영어"):
            track = " ".join(part for part in ("영어", exam_form) if part)
        elif track in ("가", "나", "A", "B"):
            track += "형"
        elif track not in ("가형", "나형", "A형", "B형", "확률과 통계", "미적분", "기하"):
            track = ""  # a subject area such as 독서 is not an official exam track
        number = _number(variant.get("question_number", ""))
        items.append(" ".join(part for part in (track, number) if part))
    prefix = " ".join(part for part in (year, event, authority) if part)
    if prefix and items:
        return prefix + " " + " · ".join(items)
    return prefix or origin.raw_label or " · ".join(items)


def format_display_title(rec: CanonicalQuestionRecord) -> str:
    """Choose the learner-facing title without changing the stable question ID."""
    origin_title = format_origin_title(rec.origin, rec.question.subject, rec.source.exam_form)
    source_title = rec.source.title.strip()
    item_number = rec.source.item_code or rec.question.question_number or rec.question.display_code
    source_number = _number(item_number)
    source_label = " ".join(part for part in (source_title, source_number) if part)
    content_type = rec.question.content_type or ("past_exam" if rec.origin else "original")
    # Study-note ingestion does not establish authorship or original exam identity.
    if rec.source.format_id in {"numbered-qa-v1", "numbered-table-v1"}:
        content_type = rec.question.content_type
    if content_type == "variant" and origin_title:
        return f"{source_label} (변형: {origin_title})"
    if content_type == "past_exam" and origin_title:
        return origin_title
    return source_label or rec.question.display_code or rec.question.question_id


def project_to_markdown(
    record: CanonicalQuestionRecord | Dict[str, Any],
    passage_file: str = "",
    solution_files: Optional[list[str]] = None,
    script_files: Optional[list[str]] = None,
) -> str:
    """Project canonical question record into Obsidian Markdown note."""
    if isinstance(record, dict):
        rec = CanonicalQuestionRecord.from_dict(record)
    else:
        rec = record

    from ...domain.standard_content import standard_record
    rec, warnings = standard_record(rec)
    if warnings:
        raise ValueError("; ".join(warnings))

    # Concepts or standalone passages do not use question frontmatter
    if rec.question.content_kind in ("passage", "solution", "script", "concept"):
        return format_output_markdown(rec.body.strip(), rec.question.subject or "국어") + "\n"

    # Flat frontmatter for Obsidian properties
    unit_str = rec.unit.display_name or ("/".join(rec.unit.path) if rec.unit.path else "")
    source_title = rec.source.title or format_origin_title(rec.origin)
    origin_title = format_origin_title(rec.origin, rec.question.subject, rec.source.exam_form)
    content_type = rec.question.content_type or ("past_exam" if rec.origin else "original")
    # Study-note ingestion does not establish authorship or original exam identity.
    if rec.source.format_id in {"numbered-qa-v1", "numbered-table-v1"}:
        content_type = rec.question.content_type
    header_title = format_display_title(rec)

    metadata = {
        "subject": rec.question.subject or "국어",
        "spec_version": 1,
        "question_id": rec.question.question_id,
        "display_title": header_title,
        "content_type": content_type,
        "source": source_title,
        "source_type": rec.source.type,
    }
    if rec.source.type != "exam" or rec.source.item_code:
        metadata["book_code"] = rec.source.item_code or ""
    if rec.source.type != "exam" or rec.source.section_title:
        metadata["section"] = rec.source.section_title or ""
    metadata.update({
        "unit": unit_str,
        "q_type": rec.question.question_type or "",
        "q_number": str(rec.question.question_number),
        "points": rec.question.points,
        "answer": rec.question.answer or "",
        "status": rec.question.status or "unread",
        "passage_id": rec.relations.passage_ids[0] if rec.relations.passage_ids else "",
        "difficulty": rec.question.difficulty,
    })
    if rec.source.type == "workbook" and rec.solution:
        metadata["solution_source"] = rec.source.title
    if rec.origin:
        metadata.update({
            "origin": origin_title,
            "origin_year": rec.origin.academic_year or "",
            "origin_session": rec.origin.session or "",
            "origin_authority": rec.origin.authority or "",
            "origin_track": rec.origin.track or "",
            "origin_q_number": rec.origin.question_number or "",
        })
        if rec.origin.question_id:
            metadata["origin_question_id"] = rec.origin.question_id
    if rec.source.source_set_id:
        metadata["source_set_id"] = rec.source.source_set_id
    if rec.source.exam_code:
        metadata["exam_code"] = rec.source.exam_code
    if rec.source.exam_form:
        metadata["exam_form"] = rec.source.exam_form
    if rec.question.modality:
        metadata["modality"] = rec.question.modality
    if rec.question.question_set_id:
        metadata["question_set_id"] = rec.question.question_set_id
    if rec.question.strategy_tags:
        metadata["strategy_tags"] = rec.question.strategy_tags
    if rec.relations.listening_script_ids:
        metadata["listening_script_id"] = rec.relations.listening_script_ids[0]
    if rec.question.variant_of:
        metadata["variant_of"] = rec.question.variant_of
    if rec.question.correct_rate is not None:
        metadata["correct_rate"] = rec.question.correct_rate

    frontmatter = "---\n" + "\n".join(f"{k}: {yaml_scalar(v)}" for k, v in metadata.items()) + "\n---\n\n"
    passage_callout = f"> [!quote] 공통 지문\n> ![[{passage_file}]]\n\n" if passage_file else ""

    solution_body = (rec.solution or "").lstrip()
    answer_prefix = (
        f"**정답: {rec.question.answer}**\n\n"
        if rec.question.answer and not re.search(r"^\s*\*\*정답\s*:", rec.solution or "", re.MULTILINE)
        else ""
    )
    solution_links = "\n".join(f"![[{path}]]" for path in (solution_files or []))
    script_links = "\n".join(f"![[{path}]]" for path in (script_files or []))
    script_section = f"### 듣기 대본\n\n{script_links}" if script_links else ""
    solution = "\n\n".join(value for value in (answer_prefix + solution_body, solution_links, script_section) if value)

    note = (
        frontmatter
        + f"## {header_title}\n\n"
        + "<!-- SECTION:PROBLEM_START -->\n\n"
        + passage_callout
        + rec.body.strip()
        + "\n\n<!-- SECTION:PROBLEM_END -->\n\n---\n\n"
        + "<!-- SECTION:SOLUTION_START -->\n\n"
        + solution.strip()
        + "\n\n<!-- SECTION:SOLUTION_END -->\n"
    )

    return format_output_markdown(note, rec.question.subject or "국어")


def preview_sections(record, *, passage_file="", script_files=None, script_bodies=None):
    """Use the actual Markdown export body, excluding frontmatter/control markers."""
    from ...domain.standard_content import standard_record
    rec, warnings = standard_record(record)
    if warnings:
        return {"status": "unavailable", "warnings": warnings}
    markdown = project_to_markdown(record, passage_file=passage_file, script_files=script_files)
    if record.question.content_kind in ("passage", "solution", "script", "concept"):
        return {"status": "computed", "body": markdown, "solution": "", "all": markdown, "warnings": []}
    def section(name):
        return markdown.split(f"<!-- SECTION:{name}_START -->", 1)[1].split(f"<!-- SECTION:{name}_END -->", 1)[0].strip()
    body = f"## {format_display_title(rec)}\n\n" + section("PROBLEM")
    solution = section("SOLUTION")
    for path, linked_body in (script_bodies or {}).items():
        embed = f"![[{path}]]"
        solution = solution.replace(embed, embed + "\n\n" + linked_body.strip())
    return {"status": "computed", "body": body, "solution": solution,
            "all": body + ("\n\n---\n\n" + solution if solution else ""),
            "warnings": [], "source": rec.source.title, "markdown": markdown,
            "answer": rec.question.answer or "",
            "origin_status": "known" if rec.origin else "unknown",
            "solution_source": rec.source.title if rec.source.type == "workbook" and rec.solution else ""}
