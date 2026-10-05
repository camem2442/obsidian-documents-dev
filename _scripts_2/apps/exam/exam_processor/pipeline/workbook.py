"""Source-bound workbook structures, independent of content transcription."""
import copy
import json
import re
from pathlib import Path

import pypdfium2 as pdfium

from ..domain.models import digest, identity


ROLES = ("problem", "solution")


def document_record(path):
    path = Path(path).expanduser().resolve(strict=True)
    if path.suffix.lower() != ".pdf":
        raise ValueError("문제집 원본은 PDF여야 합니다.")
    with pdfium.PdfDocument(path) as pdf:
        count = len(pdf)
    return {"path": str(path), "sha256": digest(path), "page_count": count}


def new_structure(profile, problem, solution):
    documents = {"problem": document_record(problem), "solution": document_record(solution)}
    if documents["problem"]["sha256"] == documents["solution"]["sha256"]:
        raise ValueError("문제편과 해설편에 같은 파일을 지정했습니다.")
    for role in ROLES:
        if documents[role]["page_count"] != profile["page_counts"][role]:
            raise ValueError(f"{role}: 등록된 판본의 페이지 수와 다릅니다.")
    return {
        "schema_version": 1,
        "id": identity("workbook", profile["id"], *(documents[r]["sha256"] for r in ROLES)),
        "profile": profile["id"], "profile_version": profile["version"],
        "source": profile["source"], "subject": "수학", "track": "확률과 통계",
        "documents": documents, "nodes": copy.deepcopy(profile["nodes"]),
        "samples": copy.deepcopy(profile["samples"]), "history": [],
    }


def read_structure(path):
    with open(path, encoding="utf-8") as stream:
        structure = json.load(stream)
    errors = structure_errors(structure)
    if errors:
        raise ValueError("구조 기록 오류: " + "; ".join(errors))
    return structure


def structure_errors(structure):
    errors = []
    if not isinstance(structure, dict) or structure.get("schema_version") != 1:
        return ["지원하지 않는 구조 기록 버전입니다."]
    documents = structure.get("documents", {})
    for role in ROLES:
        doc = documents.get(role, {})
        if (not isinstance(doc.get("path"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", str(doc.get("sha256", "")))
                or type(doc.get("page_count")) is not int or doc["page_count"] < 1):
            errors.append(f"{role}: 원본 정보가 잘못되었습니다.")
    nodes = structure.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return errors + ["목차가 비어 있습니다."]
    seen = set()
    occupied = {role: {} for role in ROLES}
    for node in nodes:
        if not isinstance(node, dict):
            errors.append("목차 항목이 객체가 아닙니다.")
            continue
        key = node.get("id", "")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", key) or key in seen:
            errors.append(f"중복 또는 잘못된 목차 ID: {key}")
            continue
        seen.add(key)
        if not isinstance(node.get("title"), str) or not node["title"].strip():
            errors.append(f"{key}: 목차 제목이 없습니다.")
        for role in ROLES:
            span = node.get("spans", {}).get(role)
            if not isinstance(span, dict) or span.get("status") not in ("pending", "confirmed"):
                errors.append(f"{key}/{role}: 범위 상태가 잘못되었습니다.")
                continue
            pages = span.get("pages", [])
            if not isinstance(pages, list):
                errors.append(f"{key}/{role}: 페이지 목록이 아닙니다.")
                continue
            total = documents.get(role, {}).get("page_count", 0)
            if any(type(p) is not int or not 1 <= p <= total for p in pages):
                errors.append(f"{key}/{role}: 원본 밖 페이지입니다.")
                continue
            if pages != sorted(set(pages)):
                errors.append(f"{key}/{role}: 페이지가 중복되거나 순서가 잘못되었습니다.")
            if span["status"] == "confirmed":
                if not pages or not str(span.get("evidence", "")).strip():
                    errors.append(f"{key}/{role}: 확정 범위에는 페이지와 확인 근거가 필요합니다.")
                for page in pages:
                    if page in occupied[role]:
                        errors.append(f"{key}/{role}: {occupied[role][page]}와 {page}페이지가 겹칩니다.")
                    occupied[role][page] = key
    errors.extend(inventory_errors(structure, occupied))
    return errors


def inventory_errors(structure, occupied):
    """Validate optional page accounting without assuming a constant printed offset."""
    inventory = structure.get("page_inventory")
    if inventory is None:
        inventory = {role: [] for role in ROLES}
    if not isinstance(inventory, dict) or set(inventory) != set(ROLES):
        return ["페이지 인벤토리에는 문제편과 해설편 기록이 필요합니다."]
    errors = []
    for role in ROLES:
        rows = inventory[role]
        if not isinstance(rows, list):
            errors.append(f"{role}: 페이지 인벤토리는 목록이어야 합니다.")
            continue
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                errors.append(f"{role}: 페이지 기록이 객체가 아닙니다.")
                continue
            page = row.get("page")
            total = structure["documents"].get(role, {}).get("page_count", 0)
            if type(page) is not int or not 1 <= page <= total or page in seen:
                errors.append(f"{role}: 중복 또는 잘못된 인벤토리 페이지입니다.")
                continue
            seen.add(page)
            printed = row.get("printed_page")
            if "printed_page" not in row or (printed is not None and (type(printed) is not int or printed < 1)):
                errors.append(f"{role}/{page}: 인쇄 쪽수는 양의 정수 또는 null이어야 합니다.")
            kind = row.get("kind")
            if kind not in ("node", "front_matter", "part_separator", "back_matter"):
                errors.append(f"{role}/{page}: 페이지 용도가 잘못되었습니다.")
            owner = occupied[role].get(page)
            if (kind == "node" and (not owner or row.get("node") != owner)
                    or kind != "node" and (owner or row.get("node"))):
                errors.append(f"{role}/{page}: 페이지 용도와 목차 범위가 다릅니다.")
            if not isinstance(row.get("evidence"), str) or not row["evidence"].strip():
                errors.append(f"{role}/{page}: 페이지 확인 근거가 없습니다.")
    issues = structure.get("source_issues", [])
    if not isinstance(issues, list):
        return errors + ["원본 이상 항목은 목록이어야 합니다."]
    for issue in issues:
        if (not isinstance(issue, dict) or issue.get("document") not in ROLES
                or not isinstance(issue.get("description"), str) or not issue["description"].strip()
                or issue.get("status") not in ("unresolved", "resolved")):
            errors.append("원본 이상 항목의 문서·설명·상태를 확인하세요.")
    return errors


def verify_sources(structure):
    for role in ROLES:
        actual = document_record(structure["documents"][role]["path"])
        expected = structure["documents"][role]
        if any(actual[k] != expected[k] for k in ("sha256", "page_count")):
            raise ValueError(f"{role}: 원본이 변경되었습니다. 구조를 다시 확인하세요.")


def confirm_span(structure, node_id, role, pages, evidence, printed_first=None, printed_last=None):
    if role not in ROLES or not evidence.strip():
        raise ValueError("원본 구분과 페이지 확인 근거가 필요합니다.")
    candidate = copy.deepcopy(structure)
    node = next((n for n in candidate["nodes"] if n["id"] == node_id), None)
    if node is None:
        raise ValueError("목차 ID를 찾지 못했습니다.")
    for value in (printed_first, printed_last):
        if value is not None and (type(value) is not int or value < 1):
            raise ValueError("인쇄 쪽수는 양의 정수여야 합니다.")
    previous = node["spans"][role]
    span = {"status": "confirmed", "pages": list(pages), "evidence": evidence.strip(),
            "printed_first": printed_first, "printed_last": printed_last}
    if previous == span:
        return candidate
    node["spans"][role] = span
    errors = structure_errors(candidate)
    if errors:
        raise ValueError("; ".join(errors))
    candidate["history"].append({"node": node_id, "document": role, "previous": previous})
    return candidate


def structure_report(structure):
    errors = structure_errors(structure)
    pending = [f"{n['id']}/{r}" for n in structure["nodes"] for r in ROLES
               if n["spans"][r]["status"] != "confirmed"]
    # Unassigned pages include front matter and separators, not automatically missing content.
    unassigned = {}
    for role in ROLES:
        assigned = {p for n in structure["nodes"] if n["spans"][role]["status"] == "confirmed"
                    for p in n["spans"][role]["pages"]}
        unassigned[role] = sorted(set(range(1, structure["documents"][role]["page_count"] + 1)) - assigned)
    inventory = structure.get("page_inventory") or {}
    unclassified = {
        role: sorted(set(range(1, structure["documents"][role]["page_count"] + 1))
                     - {row["page"] for row in inventory.get(role, [])
                        if isinstance(row, dict) and type(row.get("page")) is int})
        for role in ROLES
    } if not errors else {}
    inventory_complete = (bool(inventory) and not errors and not pending
                          and not any(unclassified.values()))
    unresolved_issues = [issue for issue in structure.get("source_issues", [])
                         if isinstance(issue, dict) and issue.get("status") == "unresolved"]
    return {"id": structure["id"], "node_count": len(structure["nodes"]),
            "errors": errors, "pending_spans": pending, "unassigned_pages": unassigned,
            "structure_confirmed": not errors and not pending,
            "inventory_complete": inventory_complete,
            "source_complete": inventory_complete and not unresolved_issues,
            "unclassified_pages": unclassified,
            "source_issues": copy.deepcopy(structure.get("source_issues", [])),
            "content_reviewed": False}


def split_pdfs(structure, destination, node_ids):
    from ..storage.store import atomic_json
    errors = structure_errors(structure)
    if errors:
        raise ValueError("; ".join(errors))
    ids = list(node_ids)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("분할할 목차를 중복 없이 선택하세요.")
    selected = [n for n in structure["nodes"] if n["id"] in ids]
    if {n["id"] for n in selected} != set(ids):
        raise ValueError("알 수 없는 목차 ID입니다.")
    if any(n["spans"][r]["status"] != "confirmed" for n in selected for r in ROLES):
        raise ValueError("선택 구간의 문제편·해설편 페이지를 먼저 확인하세요.")
    verify_sources(structure)
    destination = Path(destination).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=False)
    source_complete = structure_report(structure)["source_complete"]
    manifest = {"schema_version": 1, "workbook_id": structure["id"],
                "documents": structure["documents"], "files": [], "content_reviewed": False,
                "source_complete": source_complete, "status": "incomplete"}
    if structure.get("source_issues"):
        manifest["source_issues"] = copy.deepcopy(structure["source_issues"])
    # Keep an explicit incomplete record if interrupted; never publish partial output as ready.
    atomic_json(destination / "manifest.json", manifest)
    for node in selected:
        for role in ROLES:
            pages = node["spans"][role]["pages"]
            name = f"{node['id']}-{role}.pdf"
            target = destination / name
            with pdfium.PdfDocument(structure["documents"][role]["path"]) as source:
                with pdfium.PdfDocument.new() as output:
                    output.import_pages(source, [p - 1 for p in pages])
                    output.save(target)
                with pdfium.PdfDocument(target) as output:
                    if len(output) != len(pages):
                        raise ValueError("분할 PDF 페이지 수가 다릅니다.")
                    for index, original_page in enumerate(pages):
                        before, after = source[original_page - 1], output[index]
                        try:
                            if before.get_size() != after.get_size():
                                raise ValueError("분할 후 페이지 크기가 달라졌습니다.")
                        finally:
                            before.close()
                            after.close()
            labels = {row["page"]: row["printed_page"]
                      for row in structure.get("page_inventory", {}).get(role, [])}
            manifest["files"].append({"node": node["id"], "document": role, "path": name,
                                      "sha256": digest(target), "source_pages": pages,
                                      "page_map": [{"page": i + 1, "source_page": p,
                                                    **({"printed_page": labels[p]} if p in labels else {})}
                                                   for i, p in enumerate(pages)]})
            atomic_json(destination / "manifest.json", manifest)
    manifest["status"] = "complete"
    atomic_json(destination / "manifest.json", manifest)
    return manifest
