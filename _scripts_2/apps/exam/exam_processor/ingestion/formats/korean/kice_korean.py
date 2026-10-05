"""KICE two-column Korean set boundaries with ordered continuation regions."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber
import pypdfium2 as pdfium

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord
from _scripts_2.apps.exam.exam_processor.domain.canonical_factory import create_canonical_record
from ....domain.models import identity, new_item
from ....domain.review_state import ReviewState
from ....domain.text_layout import normalize_markdown
from ...pdf_styles import detect_source_styles
from ...region_render import render_region
from ..base import BaseFormatModule, IngestBatch


START = re.compile(r"^(?:\[(\d+)\s*[~～\-]\s*(\d+)\]|(\d{1,3})\.\s)")


def page_range(value, total):
    selected = set()
    for part in value.split(",") if value else [f"1-{total}"]:
        if not re.fullmatch(r"\d+(?:-\d+)?", part.strip()):
            raise ValueError("페이지는 1-3,5 형식으로 입력하세요.")
        ends = [int(n) for n in part.strip().split("-")]
        start, end = ends[0], ends[-1]
        if not 1 <= start <= end <= total:
            raise ValueError("페이지 범위가 PDF 범위를 벗어났습니다.")
        selected.update(range(start, end + 1))
    return sorted(selected)


def line_groups(column):
    words = sorted(column.extract_words(x_tolerance=2, y_tolerance=3), key=lambda w: (round(w["top"] / 3), w["x0"]))
    groups = []
    for word in words:
        if not groups or abs(word["top"] - groups[-1][0]["top"]) > 4:
            groups.append([word])
        else:
            groups[-1].append(word)
    return groups


def lines(column):
    return [(min(w["top"] for w in group), " ".join(w["text"] for w in sorted(group, key=lambda w: w["x0"]))) for group in line_groups(column)]


def passage_breaks(column):
    groups = line_groups(column)
    if not groups:
        return set()
    lefts = [min(w["x0"] for w in group) for group in groups]
    baseline = Counter(round(x * 2) / 2 for x in lefts).most_common(1)[0][0]
    # KICE prose paragraphs use a one-character first-line indent.
    starts = set()
    for index, (group, left) in enumerate(zip(groups, lefts)):
        previous = " ".join(w["text"] for w in groups[index - 1]) if index else ""
        if 6 <= left - baseline <= 18 and (not index or re.search(r"[.!?。][’'\"）)]?$", previous)):
            starts.add(min(w["top"] for w in group))
    return starts


def reflow_saved_draft(job, item, directory):
    """Recover paragraph indents only when the source still exactly matches the draft."""
    raw_blocks, prose_blocks = [], []
    for region in item["regions"]:
        with pdfplumber.open(Path(directory) / job["documents"][region["document"]]) as pdf:
            column = pdf.pages[region["page"] - 1].crop(tuple(region["bbox"]))
            row_data = lines(column)
            starts = passage_breaks(column) if item["kind"] == "passage" else set()
            raw_blocks.append("\n".join(text for _, text in row_data))
            prose_blocks.append("\n".join(("\n" if y in starts else "") + text for y, text in row_data))
    original = item.get("reference_text", "")
    if re.sub(r"\s", "", "\n".join(raw_blocks)) != re.sub(r"\s", "", original):
        return normalize_markdown(item["body"])
    return normalize_markdown("\n".join(prose_blocks))


def parse_pdf(path, source_id, track, pages, workdir, document_id="problem"):
    """Legacy item-returning parser preserved for compatibility."""
    items, warnings = [], []
    current = None
    context = "unassigned"
    shared = None
    expected = set()
    path, workdir = Path(path), Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    renderer = pdfium.PdfDocument(str(path))
    try:
        with pdfplumber.open(path) as pdf:
            selected = page_range(pages, len(pdf.pages))
            if any(right != left + 1 for left, right in zip(selected, selected[1:])):
                raise ValueError("지문 연결을 보존하려면 연속 페이지 범위를 선택하세요. 떨어진 범위는 별도 작업으로 넣으세요.")
            for page_no in selected:
                page = pdf.pages[page_no - 1]
                # KICE's full-width header rule and central divider delimit the print body.
                rules = [l for l in page.lines if abs(l["y0"] - l["y1"]) < 1 and l["width"] > page.width * .7 and l["top"] < page.height * .3]
                if not rules:
                    raise ValueError(f"{page_no}쪽의 평가원 본문 경계선을 찾지 못했습니다. 미지원 배치입니다.")
                rule = max(rules, key=lambda l: l["top"])
                top = rule["top"] + 3
                dividers = [l for l in page.lines if abs(l["x0"] - page.width / 2) < 5 and l["height"] > page.height * .4]
                bottom = max((l["bottom"] for l in dividers), default=page.height - 120)
                for left, right in [(rule["x0"] - 2, page.width / 2 - 5), (page.width / 2 + 5, rule["x1"] + 2)]:
                    column = page.crop((left, top, right, bottom))
                    row_data = lines(column)
                    prose_starts = passage_breaks(column)
                    anchors = [(y, match) for y, text in row_data if (match := START.match(text))]
                    boundaries = [(top, None)] + anchors + [(bottom, None)]
                    for index in range(len(boundaries) - 1):
                        y0, marker = boundaries[index]
                        y1 = boundaries[index + 1][0]
                        if marker:
                            if marker[1]:
                                context = f"{marker[1]}-{marker[2]}"
                                expected.update((context, str(n)) for n in range(int(marker[1]), int(marker[2]) + 1))
                                shared = new_item(source_id, context, context, "", kind="passage")
                                shared["question_set_id"] = context
                                current = shared
                            else:
                                number = marker[3]
                                current = new_item(source_id, context, number, "")
                                current["question_set_id"] = context
                                current["passage_id"] = shared["id"] if shared else ""
                            current["unit"] = track
                            items.append(current)
                        block = "\n".join(text for y, text in row_data if y0 <= y < y1)
                        if not block.strip() or y1 - y0 < 3:
                            continue
                        if current is None:
                            warnings.append(f"{page_no}쪽 시작 문맥이 없습니다. 앞 페이지를 포함하세요.")
                            continue
                        region = {"id": identity(document_id, page_no, left, y0), "document": document_id,
                                  "page": page_no, "bbox": [left, max(top, y0 - 2), right, y1 - 1],
                                  "width": page.width, "height": page.height}
                        filename = f"{region['id']}.png"
                        render_region(renderer, region, workdir / filename)
                        region["image"] = filename
                        current["regions"].append(region)
                        current["source_styles"].extend(detect_source_styles(page, page_no, region["bbox"]))
                        current["reference_text"] = current.get("reference_text", "") + ("\n\n" if current.get("reference_text") else "") + block
                        selected_rows = [(y, text) for y, text in row_data if y0 <= y < y1]
                        if current["kind"] == "passage":
                            block = "\n".join(("\n" if y in prose_starts else "") + text for y, text in selected_rows)
                        current["body"] += ("\n" if current["body"] else "") + block
            if not items:
                raise ValueError("문항 경계를 찾지 못했습니다. 스캔본 또는 미지원 형식입니다.")
    finally:
        renderer.close()
    seen = set()
    for item in items:
        if item["id"] in seen:
            warnings.append(f"중복 문항 경계: {item['number']}")
        seen.add(item["id"])
        item["body"] = normalize_markdown(item["body"])
        item["warnings"] = ["PDF 텍스트 초안입니다. 원본 이미지와 비교하고 AI 변환을 실행하세요."]
        points = re.search(r"\[(\d+)점\]", item["body"])
        item["points"] = int(points[1]) if points else None
    actual = {(x["context"], x["number"]) for x in items if x["kind"] == "question"}
    missing = expected - actual
    if missing:
        warnings.append("누락 문항: " + ", ".join(f"{c}/{n}" for c, n in sorted(missing)))
    return items, warnings


class KiceKoreanFormat(BaseFormatModule):
    format_id = "kice_korean"
    label = "평가원 국어 2단 PDF"
    extensions = frozenset({".pdf"})
    parser_version = "0.1.2"
    supported_tracks = frozenset({"매체", "화법과 작문"})

    def validate(self, request):
        super().validate(request)
        if request.solution_path and request.solution_path.suffix.lower() not in (".md", ".pdf"):
            raise ValueError("평가원 PDF에는 PDF 또는 Markdown 해설을 연결하세요.")

    def parse(self, request):
        self.validate(request)
        items, warnings = parse_pdf(
            request.problem_path,
            request.source_id,
            request.track,
            request.pages,
            request.workdir,
        )

        candidates = []
        solution_metadata = {}
        if request.solution_path:
            if request.solution_path.suffix.lower() == ".md":
                from .korean_notes import split_questions

                _, pairs = split_questions(request.solution_path.read_text(encoding="utf-8"))
                candidates = [{"number": number, "body": body, "regions": []} for number, body in pairs]
                solution_metadata = {"solution_format": "korean_notes"}
            else:
                from .. import resolve_solution_format
                from ..base import SolutionFormatRequest
                from .ebsi_korean_explanation import pdf_academic_year

                expected_year = pdf_academic_year(request.problem_path)
                solution_request = SolutionFormatRequest(
                    path=request.solution_path,
                    source_id=request.source_id,
                    track=request.track,
                    workdir=request.workdir,
                    expected_academic_year=expected_year,
                )
                solution_format = resolve_solution_format(solution_request)
                if solution_format:
                    solution_result = solution_format.parse(solution_request)
                    candidates = solution_result.items
                    warnings.extend("해설: " + warning for warning in solution_result.warnings)
                    solution_metadata = solution_result.metadata
                else:
                    solution_id = identity(request.source_id, "solutions")
                    candidate_items, extra = parse_pdf(
                        request.solution_path,
                        solution_id,
                        request.track,
                        "",
                        request.workdir,
                        "solution",
                    )
                    warnings.extend("해설: " + warning for warning in extra)
                    candidates = [item for item in candidate_items if item["kind"] == "question"]
                    solution_metadata = {"solution_format": "kice_korean"}

            for candidate in candidates:
                candidate["id"] = candidate.get(
                    "id", identity(request.source_id, "solution", candidate["number"])
                )

        records: List[CanonicalQuestionRecord] = []
        review_states: List[ReviewState] = []
        unmatched = []

        for item in items:
            kind = item["kind"]
            number = item["number"]
            context = item["context"]
            body = item["body"]
            points = item.get("points")
            passage_id = item.get("passage_id")
            source_styles = item.get("source_styles", [])
            reference_text = item.get("reference_text", "")
            item_warnings = list(item.get("warnings", []))

            matches = []
            solution_match = None
            if kind == "question" and request.solution_path:
                matches = [c for c in candidates if c["number"] == number]
                if len(matches) == 1:
                    solution_match = {
                        "status": "matched",
                        "basis": matches[0].get("match_basis", ["question_number"]),
                    }
                    item_warnings.append("해설 후보를 확인해 연결하세요.")
                elif len(matches) > 1:
                    solution_match = {
                        "status": "ambiguous",
                        "basis": ["question_number"],
                    }
                    item_warnings.append("같은 번호의 해설 후보가 여러 개입니다.")
                else:
                    solution_match = {
                        "status": "unmatched",
                        "basis": [],
                    }
                    item_warnings.append("연결할 해설 후보가 없습니다.")
                    unmatched.append(number)

            rec, rev = create_canonical_record(
                record_id=item["id"],
                subject="국어",
                source_id=request.source_id,
                source_type="exam",
                source_title=request.source,
                number=number,
                content_kind=kind,
                body=body,
                points=points,
                format_id=self.format_id,
                question_set_id=context,
                unit_name=request.track,
                unit_path=[request.track] if request.track else [],
                passage_ids=[passage_id] if passage_id else [],
                source_regions=item.get("regions", []),
                review_warnings=item_warnings,
                reference_text=reference_text,
                source_styles=source_styles,
                solution_candidates=matches if matches else None,
                solution_match=solution_match,
            )
            records.append(rec)
            review_states.append(rev)

        if unmatched:
            warnings.append("해설 미매칭: " + ", ".join(unmatched))

        return IngestBatch(
            records=records,
            review_states=review_states,
            warnings=warnings,
            metadata=solution_metadata,
        )

