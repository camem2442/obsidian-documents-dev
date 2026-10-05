"""Versioned records shared by PDF, Markdown and the review UI."""
import copy
import hashlib
import json
import re
import uuid
from .rich_content import material_errors


def identity(*parts):
    return uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(parts, ensure_ascii=False)).hex


def ingest_item_id(source_id, context, kind, number):
    """Create a job-item ID once at ingest. Later number edits must not recompute this."""
    return identity(source_id, context, kind, str(number))


def relabel_number(item, number):
    """Change the printed number/display code without replacing the internal ID."""
    number = str(number or "").strip()
    if not number:
        raise ValueError("문항 번호는 비울 수 없습니다.")
    item["number"] = number
    return item


def revision():
    return uuid.uuid4().hex


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def new_item(source_id, context, number, body, *, kind="question", regions=None):
    return {
        "id": ingest_item_id(source_id, context, kind, number), "revision": revision(),
        "kind": kind, "subtype": "", "context": context, "question_set_id": "", "number": str(number), "body": body,
        "solution": "", "answer": "", "points": None, "correct_rate": None, "unit": "", "q_type": "",
        "passage_id": "", "regions": regions or [], "solution_regions": [],
        "classification_status": "unclassified", "classification_method": "unclassified",
        "curriculum_id": "", "classification_candidates": [], "classification_evidence": [],
        "assets": [], "source_styles": [], "style_mapping_errors": [], "group_mapping_errors": [], "box_checks": [], "ai_runs": [], "review": "pending", "published_revision": "", "audit": None,
        "note": "", "warnings": [], "quality_checks": [], "history": [],
    }


def invalidate(item):
    keys = ("revision", "body", "solution", "answer", "points", "correct_rate", "unit", "q_type", "regions",
            "solution_regions", "assets", "review", "audit", "audit_waiver", "selected_solution_id",
            "solution_link_backup", "transcription_pending", "modality", "question_set_id",
            "listening_script_id", "strategy_tags", "content_type")
    item["history"].append(copy.deepcopy({k: item[k] for k in keys if k in item}))
    item["revision"] = revision()
    item["review"] = "pending"
    # The previous audit remains visible, but never authorizes the new revision.


def link_solution(item, candidate):
    """Attach one reviewed candidate while preserving the pre-link content."""
    if item.get("selected_solution_id") == candidate["id"]:
        return False
    if not item.get("selected_solution_id"):
        item["solution_link_backup"] = {
            "solution": item.get("solution", ""),
            "solution_reference": item.get("solution_reference", ""),
            "solution_regions": copy.deepcopy(item.get("solution_regions", [])),
            "answer": item.get("answer", ""),
            "solution_transcription_pending": "solution" in item.get("transcription_pending", []),
            "assets": copy.deepcopy([
                asset for asset in item.get("assets", [])
                if asset.get("section") == "solution"
            ]),
        }
    invalidate(item)
    item["solution"] = candidate["body"]
    item["solution_reference"] = candidate["body"]
    item["solution_regions"] = copy.deepcopy(candidate.get("regions", []))
    item["selected_solution_id"] = candidate["id"]
    if candidate.get("answer"):
        item["answer"] = candidate["answer"]
    if candidate.get("transcription_required"):
        pending = item.setdefault("transcription_pending", [])
        if "solution" not in pending:
            pending.append("solution")
    elif "transcription_pending" in item:
        item["transcription_pending"] = [s for s in item["transcription_pending"] if s != "solution"]
    item.setdefault("solution_match", {"status": "matched", "basis": ["question_number"]})
    item["solution_match"]["confirmed_by_user"] = True
    item["warnings"] = [
        warning for warning in item["warnings"]
        if warning != "해설 후보를 확인해 연결하세요."
    ]
    item["assets"] = [
        asset for asset in item["assets"]
        if asset.get("section") != "solution"
    ]
    return True


def unlink_solution(item):
    """Detach a candidate and restore the content present before the first link."""
    if not item.get("selected_solution_id"):
        raise ValueError("연결된 해설이 없습니다.")
    backup = copy.deepcopy(item.get("solution_link_backup") or {})
    invalidate(item)
    item["solution"] = backup.get("solution", "")
    item["solution_reference"] = backup.get("solution_reference", "")
    item["solution_regions"] = backup.get("solution_regions", [])
    item["answer"] = backup.get("answer", "")
    if "transcription_pending" in item:
        item["transcription_pending"] = [s for s in item["transcription_pending"] if s != "solution"]
        if backup.get("solution_transcription_pending"):
            item["transcription_pending"].append("solution")
    item["assets"] = [
        asset for asset in item["assets"]
        if asset.get("section") != "solution"
    ] + backup.get("assets", [])
    item["selected_solution_id"] = ""
    item.pop("solution_link_backup", None)
    if item.get("solution_match"):
        item["solution_match"].pop("confirmed_by_user", None)
    if (item.get("solution_candidates")
            and "해설 후보를 확인해 연결하세요." not in item["warnings"]):
        item["warnings"].append("해설 후보를 확인해 연결하세요.")


DEFAULT_AUDIT_WAIVER_REASON = "원본 대조 생략"


def normalize_audit_waiver_reason(reason):
    text = (reason or "").strip()
    return text or DEFAULT_AUDIT_WAIVER_REASON


def audit_waiver_current(item):
    waiver = item.get("audit_waiver") or {}
    revision = item.get("revision")
    if not revision or waiver.get("revision") != revision or waiver.get("status") != "waived":
        return None
    return waiver


def audit_approval_gate(item, note=""):
    """Return an error message when audit gate blocks approval, else None."""
    note = (note or item.get("note") or "").strip()
    waiver = audit_waiver_current(item)
    if waiver:
        return None
    audit = item.get("audit") or {}
    revision = item.get("revision")
    if not audit or audit.get("revision") != revision or audit.get("status") != "completed":
        return "현재 버전의 AI 검수를 완료하세요."
    unresolved = [
        issue for issue in (audit.get("issues") or [])
        if not issue.get("applied") and not issue.get("skipped")
    ]
    if audit.get("result") != "no_difference" and unresolved and not note:
        return "AI 지적을 확인한 판단 근거를 남기세요."
    return None


def structural_errors(item):
    errors = []
    if item.get("transcription_pending"):
        errors.append("원본 영역의 전사가 완료되지 않았습니다: " + ", ".join(item["transcription_pending"]))
    if not item["body"].strip():
        errors.append("본문이 비어 있습니다.")
    for section in ("body", "solution"):
        errors.extend(material_errors(item[section], [a for a in item["assets"] if a["section"] == section]))
    if "{{diagram:" in item["body"] or "{{diagram:" in item["solution"]:
        errors.append("연결되지 않은 도표가 있습니다.")
    if item.get("solution_candidates") and not item.get("selected_solution_id"):
        errors.append("해설 후보의 연결을 확인하세요.")
    match_status = (item.get("solution_match") or {}).get("status")
    if match_status == "unmatched" or (
        match_status == "ambiguous" and not item.get("selected_solution_id")
    ):
        errors.append("해설 매칭 상태를 확인하세요.")
    references = re.findall(r"!\[\[([^\]]+)\]\]", item["body"] + "\n" + item["solution"])
    registered = {a["path"] for a in item["assets"]}
    if any(path not in registered for path in references):
        errors.append("등록되지 않은 에셋 링크가 있습니다.")
    for region in item["regions"] + item["solution_regions"]:
        box = region.get("bbox", [])
        if len(box) != 4 or not (0 <= box[0] < box[2] <= region.get("width", 0) and 0 <= box[1] < box[3] <= region.get("height", 0)):
            errors.append("원본 영역 좌표가 잘못되었습니다.")
    if item["points"] is not None and (isinstance(item["points"], bool) or not isinstance(item["points"], (int, float)) or item["points"] < 0):
        errors.append("배점은 0 이상의 숫자 또는 빈 값이어야 합니다.")
    if "<!-- SECTION:" in item["body"] or "<!-- SECTION:" in item["solution"]:
        errors.append("본문에 출력용 SECTION 마커가 중복되어 있습니다.")
    if item.get("style_mapping_errors"):
        errors.append("PDF 서식을 결과에 정확히 연결하지 못했습니다.")
    if item.get("group_mapping_errors"):
        errors.append("PDF 묶음 범위를 결과에 정확히 연결하지 못했습니다.")
    if any(check.get("status") == "missing" for check in item.get("box_checks", [])):
        errors.append("PDF 박스 내부 내용이 결과에 연결되지 않았습니다.")
    if item.get("material_validation_errors"):
        errors.append("자료 구조화 결과의 숫자·기호·단위 검사가 필요합니다.")
    return errors


def yaml_scalar(value):
    # JSON scalars are valid YAML scalars; keep identifiers and answers quoted.
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


STRUCTURAL_COMMENT = r"<!-- (?:SECTION|MATERIAL|GROUP):[^>\n]+ -->"
CHOICE_LINE = r"[①②③④⑤]"


def repair_pdf_spacing_artifacts(text):
    """Clean narrow PDF text-layer spacing artifacts at Markdown export time."""
    for before, after in {
        "수 월해진": "수월해진",
        "하 게": "하게",
        "기준으 로": "기준으로",
        "적절하 다": "적절하다",
        "반영되 었": "반영되었",
        "반영 되": "반영되",
        "반 영": "반영",
        "제시되 어": "제시되어",
        "없 다": "없다",
        "있 다": "있다",
        ") 의": ")의",
        "’ 에서": "’에서",
    }.items():
        text = text.replace(before, after)
    text = re.sub(r"([가-힣])으\s+로\b", r"\1으로", text)
    return text


def space_structural_comments(text):
    text = re.sub(r"[ \t]*(<!-- (?:SECTION|MATERIAL|GROUP):[^>\n]+ -->)[ \t]*", r"\n\1\n", text)
    rows = text.splitlines()
    output = []
    after_comment = False
    for row in rows:
        if re.fullmatch(STRUCTURAL_COMMENT, row.strip()):
            while output and not output[-1].strip():
                output.pop()
            if output:
                output.append("")
            output.append(row.strip())
            after_comment = True
            continue
        if after_comment:
            if not row.strip():
                continue
            output.append("")
            after_comment = False
        output.append(row)
    return "\n".join(output) + ("\n" if text.endswith("\n") else "")


def compact_choice_spacing(text):
    rows = text.splitlines()
    output = []
    for index, row in enumerate(rows):
        if not row.strip() and output and re.match(rf"^{CHOICE_LINE}\s*", output[-1].strip()):
            next_row = next((candidate for candidate in rows[index + 1:] if candidate.strip()), "")
            if re.match(rf"^{CHOICE_LINE}\s*", next_row.strip()):
                continue
        output.append(row)
    return "\n".join(output) + ("\n" if text.endswith("\n") else "")


def render_group_callouts(text):
    rows = text.splitlines()
    output = []
    index = 0
    while index < len(rows):
        start = re.fullmatch(r"<!-- GROUP:([A-Z]+) START -->", rows[index].strip())
        if not start:
            output.append(rows[index])
            index += 1
            continue
        label = start[1]
        end_index = None
        for cursor in range(index + 1, len(rows)):
            if re.fullmatch(rf"<!-- GROUP:{label} END -->", rows[cursor].strip()):
                end_index = cursor
                break
        if end_index is None:
            output.append(rows[index])
            index += 1
            continue
        body = rows[index + 1:end_index]
        first_content = next((row.strip() for row in body if row.strip()), "")
        output.append(rows[index].strip())
        if first_content.startswith(f"> [{label}]"):
            output.extend(body)
            output.append(rows[end_index].strip())
            index = end_index + 1
            continue
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()
        if body:
            output.append("")
            first = True
            for row in body:
                if not row.strip():
                    output.append(">")
                    continue
                if first:
                    output.append(f"> [{label}] {row.strip()}")
                    first = False
                else:
                    output.append(f"> {row.strip()}")
            output.append("")
        output.append(rows[end_index].strip())
        index = end_index + 1
    return "\n".join(output) + ("\n" if text.endswith("\n") else "")


def format_output_markdown(text, subject="국어"):
    if subject == "국어":
        text = repair_pdf_spacing_artifacts(text)
    elif subject == "수학":
        # Whitespace within a delimited TeX expression is presentation, not a
        # content revision. Preserve code and display math byte-for-byte.
        text = re.sub(
            r"```[\s\S]*?```|~~~[\s\S]*?~~~|`+[^`\n]*?`+|"
            r"\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|"
            r"(?<![\\$])\$(?!\$)(?:\\.|[^\\$])+\$|\\\([\s\S]+?\\\)",
            lambda m: re.sub(r"\s*\r?\n\s*", " ", m[0])
            if (m[0].startswith("$") and not m[0].startswith("$$")) or m[0].startswith("\\(")
            else m[0], text,
        )
        protected_pattern = re.compile(
            r"(```[\s\S]*?```|~~~[\s\S]*?~~~|`+[^`\n]*?`+|"
            r"\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|"
            r"\$(?!\$)(?:\\.|[^\\$\n])+\$|\\\([\s\S]+?\\\))"
        )
        circled = {chr(ord("A") + index): chr(0x24B6 + index) for index in range(26)}
        circled.update({str(index): "⓪①②③④⑤⑥⑦⑧⑨"[index] for index in range(10)})
        pieces = protected_pattern.split(text)
        text = "".join(
            piece if index % 2 else re.sub(
                r"\\textcircled\{([A-Z0-9])\}",
                lambda match: circled[match.group(1)],
                piece,
            )
            for index, piece in enumerate(pieces)
        )

        def split_condition_line(match):
            content = match.group(2)
            if not re.search(r"\(가\)", content):
                return match.group(0)
            parts = re.split(r"\s+(?=\((?:나|다|라|마)\))", content)
            return match.group(1) + ("\n> ".join(part.strip() for part in parts))

        text = re.sub(r"(?m)^(>\s+)([^\n]+)$", split_condition_line, text)
    text = compact_choice_spacing(text)
    text = space_structural_comments(text)
    return render_group_callouts(text)


def markdown_note(job, item, passage_file=""):
    """Render an item through the canonical Markdown output profile."""
    from _scripts_2.apps.exam.exam_processor.domain.canonical_mapping import item_to_canonical
    from ..exporters.profiles.markdown import project_to_markdown
    record = item_to_canonical(job, item)
    return project_to_markdown(record, passage_file=passage_file)
