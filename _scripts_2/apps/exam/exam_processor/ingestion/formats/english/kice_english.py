"""Conservative two-column KICE English draft ingestion.

The text layer is a review draft, never an approved transcription.  Source
regions remain attached so images, underlines, and reading order can be audited.
"""
from __future__ import annotations

import re
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium

from _scripts_2.apps.exam.exam_core.domain.canonical import AssetDomain, OriginDomain
from _scripts_2.apps.exam.exam_processor.domain.canonical_factory import create_canonical_record
from ....domain.models import identity, ingest_item_id
from ....domain.text_layout import normalize_markdown
from ...region_render import render_region
from ..base import BaseFormatModule, IngestBatch
from ..korean.kice_korean import lines, page_range


GROUP = re.compile(r"^\[(\d{1,2})\s*[～~\-]\s*(\d{1,2})\]")
QUESTION = re.compile(r"^(\d{1,2})\.(?:\s+|$)")
LISTENING_END_ANNOUNCEMENT = re.compile(
    r"(?s)\s*(?:이제\s*듣기[\s·]*말하기\s*문제가\s*끝났습니다.*|"
    r"이제\s*듣기\s*문제가\s*끝났습니다.*)$"
)
SHARED_PASSAGES = {(41, 42), (43, 45)}
VISUAL_QUESTIONS = {4, 10, 25}
VISUAL_ASSET_ID = "fig1"


def strip_listening_end(body):
    return LISTENING_END_ANNOUNCEMENT.sub("", body).rstrip()


def visual_material(number, asset_path):
    asset = AssetDomain(
        asset_id=VISUAL_ASSET_ID,
        section="body",
        source_path=asset_path,
        path=asset_path,
        media_type="image/png",
        description=f"{number}번 시각 자료",
    )
    markup = (f"\n\n<!-- MATERIAL:{VISUAL_ASSET_ID} START -->\n"
              f"![[{asset_path}]]\n<!-- MATERIAL:{VISUAL_ASSET_ID} END -->")
    return asset, markup


def _bounds(page):
    rules = [line for line in page.lines if abs(line["y0"] - line["y1"]) < 1
             and line["width"] > page.width * .7 and line["top"] < page.height * .3]
    if not rules:
        raise ValueError("평가원 영어 본문 경계선을 찾지 못했습니다.")
    rule = max(rules, key=lambda line: line["top"])
    dividers = [line for line in page.lines if abs(line["x0"] - page.width / 2) < 5
                and line["height"] > page.height * .4]
    top = rule["top"] + 3
    bottom = max((line["bottom"] for line in dividers), default=page.height - 115)
    return top, bottom, ((rule["x0"] - 2, page.width / 2 - 5),
                         (page.width / 2 + 5, rule["x1"] + 2))


def parse_problem_pdf(path, source_id, pages="", workdir=None):
    """Return ordered draft (records, review_states); fail closed on missing or duplicate numbers."""
    records, review_states = [], []
    warnings = []
    current_rec, current_rev = None, None
    group = None
    group_instruction = ""
    shared_rec = None
    selected = None
    total_pages = 0
    renderer = pdfium.PdfDocument(str(path)) if workdir else None
    try:
        with pdfplumber.open(path) as pdf:
            total_pages = len(pdf.pages)
            selected = page_range(pages, len(pdf.pages))
            if any(b != a + 1 for a, b in zip(selected, selected[1:])):
                raise ValueError("영어 지문 연결을 위해 연속 페이지를 선택하세요.")
            for page_no in selected:
                page = pdf.pages[page_no - 1]
                top, bottom, columns = _bounds(page)
                for left, right in columns:
                    row_data = lines(page.crop((left, top, right, bottom)))
                    anchors = [(y, "group", m) for y, text in row_data
                               if (m := GROUP.match(text))]
                    anchors += [(y, "question", m) for y, text in row_data
                                if (m := QUESTION.match(text)) and 1 <= int(m[1]) <= 45]
                    anchors.sort(key=lambda entry: entry[0])
                    boundaries = [(top, "continuation", None)] + anchors + [(bottom, "end", None)]
                    for index in range(len(boundaries) - 1):
                        y0, kind, marker = boundaries[index]
                        y1 = boundaries[index + 1][0]
                        if kind == "group":
                            group = (int(marker[1]), int(marker[2]))
                            if group in SHARED_PASSAGES:
                                p_label = f"{group[0]}-{group[1]}"
                                p_id = ingest_item_id(source_id, p_label, "passage", p_label)
                                shared_rec, shared_rev = create_canonical_record(
                                    record_id=p_id,
                                    subject="영어",
                                    source_id=source_id,
                                    source_type="exam",
                                    source_title="",
                                    number=p_label,
                                    content_kind="passage",
                                    question_set_id=f"set-{source_id}-{group[0]}-{group[1]}",
                                )
                                records.append(shared_rec)
                                review_states.append(shared_rev)
                                current_rec, current_rev = shared_rec, shared_rev
                            else:
                                shared_rec = None
                                current_rec, current_rev = None, None
                            group_instruction = ""
                        elif kind == "question":
                            number = int(marker[1])
                            if group and number > group[1]:
                                group, shared_rec, group_instruction = None, None, ""
                            context = f"{group[0]}-{group[1]}" if shared_rec else f"q{number:02d}"
                            q_id = ingest_item_id(source_id, context, "question", str(number))
                            unit_name = "듣기" if number <= 17 else "독해"
                            modality = "listening" if number <= 17 else "reading"
                            passage_ids = [shared_rec.question.question_id] if shared_rec else []
                            question_set_id = (
                                f"set-{source_id}-{group[0]}-{group[1]}" if shared_rec
                                else f"set-{source_id}-16-17" if group == (16, 17)
                                else ""
                            )
                            initial_body = (group_instruction + "\n") if (group_instruction and not shared_rec) else ""
                            current_rec, current_rev = create_canonical_record(
                                record_id=q_id,
                                subject="영어",
                                source_id=source_id,
                                source_type="exam",
                                source_title="",
                                number=str(number),
                                content_kind="question",
                                unit_name=unit_name,
                                modality=modality,
                                passage_ids=passage_ids,
                                question_set_id=question_set_id,
                                body=initial_body,
                            )
                            records.append(current_rec)
                            review_states.append(current_rev)
                        block = "\n".join(text for y, text in row_data if y0 <= y < y1).strip()
                        if not block or y1 - y0 < 3:
                            continue
                        raw_block = block
                        if current_rec is None:
                            if kind == "group":
                                group_instruction = block
                            continue
                        region = {"id": identity("problem", page_no, left, y0),
                                  "document": "problem", "page": page_no,
                                  "bbox": [left, max(top, y0 - 2), right, y1 - 1],
                                  "width": page.width, "height": page.height,
                                  "content_role": "body"}
                        if renderer and workdir:
                            workdir = Path(workdir)
                            workdir.mkdir(parents=True, exist_ok=True)
                            filename = f"{region['id']}.png"
                            render_region(renderer, region, workdir / filename)
                            region["image"] = filename
                            if kind == "question" and int(marker[1]) in VISUAL_QUESTIONS:
                                asset, markup = visual_material(number, f"regions/{filename}")
                                current_rec.assets.append(asset)
                                block += markup
                        current_rec.provenance.source_regions.append(region)
                        current_rev.reference_text = (current_rev.reference_text + "\n" + raw_block).strip()
                        current_rec.body = (current_rec.body + "\n" + block).strip()
            if not records:
                raise ValueError("영어 문항 경계를 찾지 못했습니다. 스캔본 또는 미지원 배치입니다.")
    finally:
        if renderer:
            renderer.close()
    questions = [rec for rec in records if rec.question.content_kind == "question"]
    counts = {n: sum(rec.question.question_number == str(n) for rec in questions) for n in range(1, 46)}
    if selected and selected[0] == 1 and selected[-1] == total_pages:
        wrong = [n for n, count in counts.items() if count != 1]
        if wrong:
            warnings.append("문항 번호 누락/중복: " + ", ".join(map(str, wrong)))
    for rec, rev in zip(records, review_states):
        body = normalize_markdown(rec.body)
        if rec.question.content_kind == "question" and rec.question.question_number == "17":
            body = strip_listening_end(body)
        rec.body = body
        rev.warnings = ["PDF 텍스트 초안입니다. 그림·표·밑줄·2단 순서를 원본과 대조하세요."]
        if rec.question.content_kind == "question":
            points_match = re.search(r"\[(\d+)점\]", rec.body)
            rec.question.points = int(points_match[1]) if points_match else None
    return records, review_states, warnings


def parse_script_pdf(path, source_id, workdir=None):
    """Keep one script note per broadcast; 16 and 17 share page 16's text."""
    records, review_states = [], []
    warnings = []
    renderer = pdfium.PdfDocument(str(path)) if workdir else None
    try:
        with pdfplumber.open(path) as pdf:
            for number in range(1, 17):
                if number > len(pdf.pages):
                    warnings.append(f"듣기 {number}번 대본 페이지가 없습니다.")
                    break
                text = pdf.pages[number - 1].extract_text() or ""
                match = re.search(r"(?m)^\s*(?:M|W)\s*:", text)
                if not match:
                    warnings.append(f"듣기 {number}번의 발화 시작을 확인하지 못했습니다.")
                    continue
                body = text[match.start():]
                body = re.split(r"(?m)^\s*(?:\d{1,2}\.\s|이 문제지에 관한 저작권)", body, maxsplit=1)[0].strip()
                label = "16-17" if number == 16 else str(number)
                s_id = ingest_item_id(source_id, f"script-{label}", "script", label)
                page = pdf.pages[number - 1]
                region = {"id": identity("script", number), "document": "script", "page": number,
                          "bbox": [0, 0, page.width, page.height],
                          "width": page.width, "height": page.height,
                          "content_role": "body"}
                if renderer and workdir:
                    workdir = Path(workdir)
                    workdir.mkdir(parents=True, exist_ok=True)
                    filename = f"{region['id']}.png"
                    render_region(renderer, region, workdir / filename)
                    region["image"] = filename
                rec, rev = create_canonical_record(
                    record_id=s_id,
                    subject="영어",
                    source_id=source_id,
                    source_type="exam",
                    source_title="",
                    number=label,
                    content_kind="script",
                    unit_name="듣기",
                    modality="listening",
                    body=f"## {label.replace('-', '~')}번\n\n{body}",
                    source_regions=[region],
                    review_warnings=["대본 PDF 텍스트 초안입니다. 발화자·문장·공유 범위를 원본과 대조하세요."],
                )
                records.append(rec)
                review_states.append(rev)
    finally:
        if renderer:
            renderer.close()
    return records, review_states, warnings


class KiceEnglishFormat(BaseFormatModule):
    format_id = "kice_english"
    label = "평가원 영어 2단 PDF"
    extensions = frozenset({".pdf"})
    parser_version = "0.2.0"
    supported_tracks = frozenset({"영어"})

    def parse(self, request):
        self.validate(request)
        match = re.search(r"(\d{4})학년도\s*(?:(\d{1,2})월|(?:대학수학능력시험|수능))", request.source)
        if not match:
            raise ValueError("평가원 영어 출처에는 '2027학년도 9월' 또는 '2026학년도 수능'을 적으세요.")
        year, session = match[1], f"{int(match[2]):02d}" if match[2] else "11"
        with pdfplumber.open(request.problem_path) as pdf:
            first_page = pdf.pages[0].extract_text() or ""
        header = first_page[:300]
        event_label = "대학수학능력시험" if session == "11" else f"{int(session)}월"
        if f"{year}학년도" not in header or event_label not in header:
            raise ValueError("입력한 출처 학년도·월이 문제지 첫 쪽 표제와 다릅니다.")
        form = re.search(r"(?:홀수형|짝수형)", first_page)
        form_suffix = "-odd" if form and form[0] == "홀수형" else "-even" if form else ""
        source_set_id = f"kice-{year}-{session}-eng{form_suffix}"
        records, review_states, warnings = parse_problem_pdf(
            request.problem_path, request.source_id, request.pages, request.workdir
        )
        for rec in records:
            rec.source.title = request.source
            rec.source.format_id = self.format_id
            rec.source.exam_code = f"{year[-2:]}{session}"
            rec.source.exam_form = form[0] if form else ""
            rec.source.source_set_id = source_set_id
            if rec.question.content_kind != "question":
                continue
            rec.question.content_type = "past_exam"
            rec.origin = OriginDomain(
                type="kice",
                academic_year=year,
                administration_year=str(int(year) - 1),
                session=session,
                authority="KICE",
                track="영어",
                question_number=rec.question.question_number,
                question_id=rec.question.question_id,
            )
        if request.script_path:
            scripts, s_reviews, extra = parse_script_pdf(request.script_path, request.source_id, request.workdir)
            warnings.extend(extra)
            records.extend(scripts)
            review_states.extend(s_reviews)
            by_number = {s.question.question_number: s.question.question_id for s in scripts}
            for rec in records:
                if rec.question.content_kind == "question" and rec.question.modality == "listening":
                    key = "16-17" if rec.question.question_number in ("16", "17") else rec.question.question_number
                    if key in by_number:
                        rec.relations.listening_script_ids = [by_number[key]]
                    else:
                        warnings.append(f"듣기 {rec.question.question_number}번 대본 연결이 없습니다.")
        elif any(rec.question.modality == "listening" for rec in records if rec.question.content_kind == "question"):
            warnings.append("평가원 영어 듣기 대본이 없어 듣기 문항을 완성할 수 없습니다.")
        return IngestBatch(records=records, review_states=review_states, warnings=warnings,
                           metadata={"subject": "영어", "source_type": "exam",
                                     "source_set_id": source_set_id,
                                     "exam_code": f"{year[-2:]}{session}",
                                     "exam_form": form[0] if form else ""})

