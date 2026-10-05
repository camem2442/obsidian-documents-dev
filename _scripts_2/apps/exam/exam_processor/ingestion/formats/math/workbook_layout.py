"""Validate reviewed workbook regions before they become editable job items."""
import math
import re


def normalize_number(number):
    return re.sub(r"[·ㆍ.\s]+", "-", number.strip()).upper()


def validate_layout(layout, structure):
    if layout.get("schema_version") != 1 or layout.get("workbook_id") != structure["id"]:
        raise ValueError("영역 기록의 버전 또는 원본 교재가 다릅니다.")
    if layout.get("status") != "confirmed" or not layout.get("evidence", "").strip():
        raise ValueError("문항 영역·읽기 순서를 먼저 확인하세요.")
    nodes = {n["id"]: n for n in structure["nodes"]}
    ids = set()
    records = layout.get("items", [])
    if not records:
        raise ValueError("영역 기록에 문항이 없습니다.")
    for record in records:
        key = record.get("id", "")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", key) or key in ids:
            raise ValueError("영역 ID가 잘못되었거나 중복됩니다.")
        ids.add(key)
        node = nodes.get(record.get("node"))
        if node is None or record.get("kind") not in ("question", "concept", "solution"):
            raise ValueError(f"{key}: 목차 또는 항목 종류가 잘못되었습니다.")
        if not isinstance(record.get("number"), str) or not record["number"].strip():
            raise ValueError(f"{key}: 원문 표시 번호가 필요합니다.")
        if not isinstance(record.get("match_key", ""), str):
            raise ValueError(f"{key}: 매칭 키가 잘못되었습니다.")
        regions = record.get("regions", [])
        if not regions:
            raise ValueError(f"{key}: 원본 영역이 없습니다.")
        for region in regions:
            role = region.get("document")
            if role not in ("problem", "solution"):
                raise ValueError(f"{key}: 원본 구분이 잘못되었습니다.")
            span = node["spans"][role]
            if span["status"] != "confirmed" or region.get("page") not in span["pages"]:
                raise ValueError(f"{key}: 확인한 목차 범위 밖의 영역입니다.")
            box = region.get("bbox", [])
            dimensions = [region.get("width"), region.get("height")]
            if (not isinstance(box, list) or len(box) != 4
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in box + dimensions)
                    or not (0 <= box[0] < box[2] <= dimensions[0] and 0 <= box[1] < box[3] <= dimensions[1])):
                raise ValueError(f"{key}: 원본 영역 좌표가 잘못되었습니다.")


def match_records(layout):
    solutions = {}
    for record in layout["items"]:
        if record["kind"] == "solution":
            key = (record["node"], record.get("match_key") or normalize_number(record["number"]))
            solutions.setdefault(key, []).append(record)
    result = {}
    for record in layout["items"]:
        if record["kind"] != "question":
            continue
        key = (record["node"], record.get("match_key") or normalize_number(record["number"]))
        candidates = solutions.get(key, [])
        result[record["id"]] = {"status": "matched" if len(candidates) == 1 else "ambiguous" if candidates else "unmatched",
                                "basis": ["workbook", "chapter", "book_number"],
                                "candidates": candidates}
    return result
