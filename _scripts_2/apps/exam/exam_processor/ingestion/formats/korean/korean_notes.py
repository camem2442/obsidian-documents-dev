"""Read the supplied HWAJAK headings and MEDIA bold-number set notes."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord
from _scripts_2.apps.exam.exam_processor.domain.canonical_factory import create_canonical_record
from ....domain.models import identity, new_item
from ....domain.review_state import ReviewState
from ..base import BaseFormatModule, IngestBatch


QUESTION = re.compile(r"^(?:#{2,4}\s+)?(?:\*\*)?(\d+(?:-\d+)?)\.\s+", re.M)
SET = re.compile(r"\[(\d+)\s*[~～\-]\s*(\d+)(?:번)?\]")


def split_questions(text):
    matches = list(QUESTION.finditer(text))
    group = SET.search(text)
    if group:
        expected = {str(n) for n in range(int(group[1]), int(group[2]) + 1)}
        matches = [m for m in matches if m[1] in expected]
    if not matches:
        raise ValueError("지원하는 문항 제목을 찾지 못했습니다.")
    pieces = []
    for index, match in enumerate(matches):
        block = text[match.start():matches[index + 1].start() if index + 1 < len(matches) else len(text)].strip()
        # Set-level commentary is not part of the final individual solution.
        block = re.split(r"^###\s+\*?\*?\[세트 종합 분석\]", block, flags=re.M)[0].rstrip("\n- ")
        pieces.append((match.group(1), block))
    return text[:matches[0].start()].strip(), pieces


def parse_notes_canonical(
    text: str,
    source_id: str,
    track: str,
    solution_text: str = "",
    source_title: str = "",
) -> Tuple[List[CanonicalQuestionRecord], List[ReviewState], List[str]]:
    passage, questions = split_questions(text)
    passage = re.sub(r"\n## 문제\s*$", "", passage).rstrip("\n- ")
    group = SET.search(text)
    context = f"{group[1]}-{group[2]}" if group else "set"

    shared_id = identity(source_id, context, context)
    shared_rec, shared_rev = create_canonical_record(
        record_id=shared_id,
        subject="국어",
        source_id=source_id,
        source_type="exam" if re.search(r"(\d{4})학년도\s*(\d{1,2})월", source_title) else "other",
        source_title=source_title,
        number=context,
        content_kind="passage",
        body=passage,
        format_id="korean_notes",
        question_set_id=context,
        unit_name=track,
        unit_path=[track] if track else [],
        reference_text=passage,
        review_warnings=["기존 마크다운 참조본입니다. PDF 원본 대조 전입니다."],
    )

    records = [shared_rec]
    review_states = [shared_rev]

    solutions = {}
    if solution_text:
        _, pairs = split_questions(solution_text)
        for number, body in pairs:
            if number in solutions:
                raise ValueError("같은 세트의 해설 번호가 중복됩니다.")
            solutions[number] = body

    seen = set()
    warnings = []
    for number, body in questions:
        if number in seen:
            raise ValueError("같은 세트의 문항 번호가 중복됩니다.")
        seen.add(number)

        points_match = re.search(r"\[(\d+(?:\.\d+)?)점\]", body)
        points = float(points_match[1]) if points_match else None
        solution = solutions.get(number, "")
        answers = set(re.findall(r"정답\s*[:：]?\s*([①②③④⑤]|\d+)", solution))
        answer = None
        item_warnings = []
        if len(answers) == 1:
            answer = answers.pop()
        elif len(answers) > 1:
            item_warnings.append("정답 표기가 여러 개입니다.")

        item_id = identity(source_id, context, number)
        rec, rev = create_canonical_record(
            record_id=item_id,
            subject="국어",
            source_id=source_id,
            source_type="exam" if re.search(r"(\d{4})학년도\s*(\d{1,2})월", source_title) else "other",
            source_title=source_title,
            number=number,
            content_kind="question",
            body=body,
            solution=solution,
            answer=answer,
            points=points,
            format_id="korean_notes",
            question_set_id=context,
            unit_name=track,
            unit_path=[track] if track else [],
            passage_ids=[shared_id],
            reference_text=body,
            review_warnings=item_warnings,
        )
        records.append(rec)
        review_states.append(rev)

    if group:
        expected = {str(x) for x in range(int(group[1]), int(group[2]) + 1)}
        if seen != expected:
            warnings.append(f"세트 번호 불일치: 기대 {sorted(expected)}, 추출 {sorted(seen)}")

    return records, review_states, warnings


def parse_notes(text, source_id, track, solution_text=""):
    """Legacy helper maintained for test compatibility."""
    passage, questions = split_questions(text)
    passage = re.sub(r"\n## 문제\s*$", "", passage).rstrip("\n- ")
    group = SET.search(text)
    context = f"{group[1]}-{group[2]}" if group else "set"
    shared = new_item(source_id, context, context, passage, kind="passage")
    shared["question_set_id"] = context
    shared["unit"] = track
    shared["reference_text"] = passage
    shared["warnings"] = ["기존 마크다운 참조본입니다. PDF 원본 대조 전입니다."]
    items = [shared]
    solutions = {}
    if solution_text:
        _, pairs = split_questions(solution_text)
        for number, body in pairs:
            if number in solutions:
                raise ValueError("같은 세트의 해설 번호가 중복됩니다.")
            solutions[number] = body
    seen = set()
    for number, body in questions:
        if number in seen:
            raise ValueError("같은 세트의 문항 번호가 중복됩니다.")
        seen.add(number)
        item = new_item(source_id, context, number, body)
        item["question_set_id"] = context
        item.update(unit=track, passage_id=shared["id"], reference_text=body)
        points = re.search(r"\[(\d+(?:\.\d+)?)점\]", body)
        item["points"] = float(points[1]) if points else None
        item["solution"] = solutions.get(number, "")
        item["solution_reference"] = item["solution"]
        answers = set(re.findall(r"정답\s*[:：]?\s*([①②③④⑤]|\d+)", item["solution"]))
        if len(answers) == 1:
            item["answer"] = answers.pop()
        elif len(answers) > 1:
            item["warnings"].append("정답 표기가 여러 개입니다.")
        items.append(item)
    warnings = []
    if group:
        expected = {str(x) for x in range(int(group[1]), int(group[2]) + 1)}
        if seen != expected:
            warnings.append(f"세트 번호 불일치: 기대 {sorted(expected)}, 추출 {sorted(seen)}")
    return items, warnings


class KoreanNotesFormat(BaseFormatModule):
    format_id = "korean_notes"
    label = "기존 국어 Markdown"
    extensions = frozenset({".md"})
    parser_version = "0.1.1"
    supported_tracks = frozenset({"매체", "화법과 작문"})

    def validate(self, request):
        super().validate(request)
        if request.solution_path and Path(request.solution_path).suffix.lower() != ".md":
            raise ValueError("Markdown 참조본에는 Markdown 해설을 연결하세요. PDF 해설은 PDF 작업으로 넣으세요.")

    def parse(self, request):
        self.validate(request)
        solution_text = request.solution_path.read_text(encoding="utf-8") if request.solution_path else ""
        records, review_states, warnings = parse_notes_canonical(
            request.problem_path.read_text(encoding="utf-8"),
            request.source_id,
            request.track,
            solution_text,
            source_title=request.source,
        )
        return IngestBatch(
            records=records,
            review_states=review_states,
            warnings=warnings,
            metadata={"solution_format": "korean_notes" if solution_text else ""},
        )

