"""EBSi Korean answer-and-explanation PDF parser."""
import re
from collections import defaultdict

import pdfplumber
import pypdfium2 as pdfium

from ....domain.models import identity
from ....domain.text_layout import join_prose
from ...region_render import render_region
from ..base import BaseSolutionFormatModule, FormatResult


QUESTION = re.compile(r"^(3[5-9]|4[0-5])\.\s+(.+)$")
ACADEMIC_YEAR = re.compile(r"(20\d{2})학년도")
SECTION = {
    "화법과 작문": "화법과 작문",
    "매체": "언어와 매체",
    "언어와 매체": "언어와 매체",
}


def pdf_academic_year(path):
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages[:2]:
            if match := ACADEMIC_YEAR.search(page.extract_text() or ""):
                return match[1]
    return ""


def page_lines(page):
    words = sorted(
        page.extract_words(x_tolerance=2, y_tolerance=3),
        key=lambda word: (round(word["top"] / 3), word["x0"]),
    )
    groups = []
    for word in words:
        if not groups or abs(word["top"] - groups[-1][0]["top"]) > 4:
            groups.append([word])
        else:
            groups[-1].append(word)
    return [
        {
            "top": min(word["top"] for word in group),
            "bottom": max(word["bottom"] for word in group),
            "text": " ".join(word["text"] for word in sorted(group, key=lambda word: word["x0"])),
        }
        for group in groups
    ]


def solution_markdown(number, title, rows):
    text = clean_solution_text(join_prose([row.strip() for row in rows if row.strip()]))
    answer_match = re.search(r"정답\s*([①②③④⑤]|\d+)", text)
    answer = answer_match[1] if answer_match else ""
    text = re.sub(r"\s*정답\s*([①②③④⑤]|\d+)\s*", " ", text).strip()
    correct, marker, wrong = text.partition("[오답피하기]")
    correct = re.sub(r"^정답해설\s*[:：]?\s*", "", correct).strip()
    parts = [f"### {number}. {title}"]
    if answer:
        parts.append(f"**정답: {answer}**")
    if correct:
        parts.extend(["### 정답 해설", correct])
    if marker and wrong.strip():
        parts.extend(["### 오답 피하기", wrong.strip()])
    return "\n\n".join(parts), answer


def clean_solution_text(text):
    """Repair common EBSi PDF text-layer artifacts without changing content."""
    text = text.replace("\uf149", "으")
    text = text.replace("사이 테스", "사이테스")
    text = re.sub(r"([가-힣])\s+(은|는|이|가|을|를|와|과|도|만|부터|까지|라고|이라고)\b", r"\1\2", text)
    text = re.sub(r"([’”\"\\)〉])\s+(은|는|이|가|을|를|와|과|도|만|부터|까지|라고|이라고)\b", r"\1\2", text)
    text = re.sub(r"하\s+는\b", "하는", text)
    text = re.sub(r"된정보", "된 정보", text)
    for before, after in {
        "또한이 자료": "또한 이 자료",
        "수 월해진": "수월해진",
        "하 여": "하여",
        "하 게": "하게",
        "포함하 여": "포함하여",
        "만으 로": "만으로",
        "기준으 로": "기준으로",
        "부속 서": "부속서",
        "적절하 지": "적절하지",
        "적절하 다": "적절하다",
        "반영되 었": "반영되었",
        "제시되 어": "제시되어",
        "없 다": "없다",
        "있 다": "있다",
    }.items():
        text = text.replace(before, after)
    text = re.sub(r"([가-힣])으\s+로\b", r"\1으로", text)
    text = re.sub(r"(\d)에(?=[가-힣])", r"\1에 ", text)
    return text


class EBSiKoreanExplanationFormat(BaseSolutionFormatModule):
    format_id = "ebsi_korean_explanation"
    label = "EBSi 국어 정답 및 해설"
    parser_version = "0.1.0"

    def supports(self, request):
        if request.path.suffix.lower() != ".pdf" or request.track not in SECTION:
            return False
        with pdfplumber.open(request.path) as pdf:
            first = "\n".join((page.extract_text() or "") for page in pdf.pages[:2])
        # Some Hancom PDFs expose the visual title as tripled glyphs in the text layer.
        return bool(ACADEMIC_YEAR.search(first) and "■ [공통: 독서·문학]" in first and "최근 수정일" in first)

    def parse(self, request):
        academic_year = pdf_academic_year(request.path)
        if not academic_year:
            raise ValueError("EBS 국어 해설지에서 학년도를 찾지 못했습니다.")
        if request.expected_academic_year and academic_year != request.expected_academic_year:
            raise ValueError(
                f"문제는 {request.expected_academic_year}학년도인데 해설은 {academic_year}학년도입니다."
            )

        section_name = SECTION[request.track]
        records = []
        active = False
        questions_started = False
        page_sizes = {}
        with pdfplumber.open(request.path) as pdf:
            for page_no, page in enumerate(pdf.pages, 1):
                page_sizes[page_no] = (page.width, page.height)
                rows = page_lines(page.crop((28, 35, page.width - 28, page.height - 35)))
                for row in rows:
                    text = row["text"].strip()
                    if text.startswith("■ [선택:"):
                        active = section_name in text
                        questions_started = False
                        continue
                    if not active:
                        continue
                    if re.fullmatch(r"\d+", text) or text.startswith("EBS"):
                        continue
                    if re.match(r"^\[\d+\s*[~～-]\s*\d+\]", text):
                        questions_started = True
                        continue
                    if not questions_started:
                        continue
                    # The answer table precedes the first explanation and contains many numbers.
                    if len(re.findall(r"\b(?:3[5-9]|4[0-5])\.", text)) > 2:
                        continue
                    records.append({**row, "page": page_no})

        anchors = [index for index, row in enumerate(records) if QUESTION.match(row["text"])]
        if not anchors:
            raise ValueError(f"EBS 국어 해설지에서 {section_name} 문항을 찾지 못했습니다.")

        renderer = pdfium.PdfDocument(str(request.path))
        candidates = []
        try:
            for anchor_index, start in enumerate(anchors):
                end = anchors[anchor_index + 1] if anchor_index + 1 < len(anchors) else len(records)
                block = records[start:end]
                match = QUESTION.match(block[0]["text"])
                number, title = match[1], match[2]
                body, answer = solution_markdown(number, title, [row["text"] for row in block[1:]])
                regions = []
                by_page = defaultdict(list)
                for row in block:
                    by_page[row["page"]].append(row)
                for page_no, page_rows in by_page.items():
                    width, height = page_sizes[page_no]
                    bbox = [28, max(35, min(row["top"] for row in page_rows) - 2),
                            width - 28, min(height - 35, max(row["bottom"] for row in page_rows) + 2)]
                    region = {
                        "id": identity(request.document_id, page_no, *bbox),
                        "document": request.document_id,
                        "page": page_no,
                        "bbox": bbox,
                        "width": width,
                        "height": height,
                    }
                    filename = f"{region['id']}.png"
                    render_region(renderer, region, request.workdir / filename)
                    region["image"] = filename
                    regions.append(region)
                candidates.append({
                    "id": identity(request.source_id, self.format_id, academic_year, section_name, number),
                    "number": number,
                    "body": body,
                    "answer": answer,
                    "regions": regions,
                    "source_format": self.format_id,
                    "academic_year": academic_year,
                    "track": request.track,
                    "match_basis": ["academic_year", "track", "question_number"],
                })
        finally:
            renderer.close()

        found = {candidate["number"] for candidate in candidates}
        expected = {str(number) for number in range(35, 46)}
        warnings = []
        if found != expected:
            warnings.append(f"EBS 해설 문항 불일치: 기대 {sorted(expected)}, 추출 {sorted(found)}")
        return FormatResult(candidates, warnings, {
            "solution_format": self.format_id,
            "solution_academic_year": academic_year,
            "solution_track": section_name,
        })
