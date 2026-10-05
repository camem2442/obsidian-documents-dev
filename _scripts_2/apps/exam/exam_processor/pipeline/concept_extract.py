"""Concept workbook blocks: text-only extract without fig assets."""
import re

from ..domain.text_layout import normalize_markdown

CONCEPT_EXTRACT = """
이 항목은 교재 '개념' 블록이다. 원본 영역의 글·수식·개념 상자·Pattern·CHECK·등식·색 요약줄을 모두 body Markdown으로만 전사한다.
diagrams는 반드시 빈 배열 []이다. {{diagram:...}} 자리표시, 이미지 링크, fig1 같은 자료 분리를 쓰지 않는다.
시각적 개념 상자(교과서 개념, 실전 개념, 초록/회색 머리말, 가로 구분선 안의 등식)도 그림으로 남기지 말고 Markdown 구조(제목, 인용 블록, 문단, $$ 수식)로 옮긴다.
"""

MATH_CONCEPT_EXTRACT = CONCEPT_EXTRACT + """
수식은 $...$, $$...$$로 보존한다. “합의 법칙을 쓴다.” = “수형도의 뒤가 다르다.” 같은 등식 줄은 그대로 문장으로 적는다.
"""


def concept_text_extract(item, section):
    return item.get("kind") == "concept" and section == "body"


def merge_concept_diagrams_into_body(data):
    """Fold model-produced diagrams into body; drop asset placeholders."""
    body = data.get("body") or ""
    merged = False
    for diagram in data.get("diagrams") or []:
        if not isinstance(diagram, dict):
            continue
        markdown = normalize_markdown(diagram.get("markdown") or "")
        if not markdown.strip():
            continue
        placeholder = "{{diagram:" + str(diagram.get("id", "")) + "}}"
        if placeholder in body:
            body = body.replace(placeholder, "\n\n" + markdown + "\n\n")
        else:
            body = body.rstrip() + "\n\n" + markdown + "\n\n"
        merged = True
    body = re.sub(r"\{\{diagram:[^}]+\}\}\s*", "", body)
    return {"body": normalize_markdown(body), "diagrams": []}, merged


def flatten_concept_material_body(body):
    """Remove MATERIAL wrappers and asset links; keep transcription as plain body."""
    if not body or "<!-- MATERIAL:" not in body:
        return normalize_markdown(body or "")

    def replace_block(match):
        inner = match.group(2)
        inner = re.sub(r"!\[\[[^\]]+\]\]\s*", "", inner)
        return normalize_markdown(inner.strip()) + "\n\n"

    text = re.sub(
        r"<!-- MATERIAL:(\w+) START -->\s*(.*?)\s*<!-- MATERIAL:\1 END -->",
        replace_block,
        body,
        flags=re.DOTALL,
    )
    return normalize_markdown(text)
