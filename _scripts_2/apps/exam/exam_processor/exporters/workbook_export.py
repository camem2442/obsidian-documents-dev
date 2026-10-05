"""Publish approved workbook chapters using the shared note and asset contracts."""
import copy
import json
import re
import shutil
from pathlib import Path

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord, canonical_relation_errors
from ..domain.models import digest
from .set_export import apply_export_asset_refs, verify_export_assets
from .rebuild import write_canonical_manifest
from .profiles.workbook_markdown import prepare_workbook_record
from .profiles.markdown import project_to_markdown
from ..storage.store import atomic_json
from ..pipeline.workbook import split_pdfs
from ..ingestion.formats.math.workbook_layout import normalize_number


def write_workbook(stage, job, original_dir, *, records=None, review_states=None):
    job_dict = job.to_dict() if hasattr(job, "to_dict") else dict(job)
    raw_meta = getattr(job, "raw_metadata", {})
    source_title = job.source if hasattr(job, "source") else job_dict.get("source", "")
    layout = job_dict.get("layout") or raw_meta.get("layout", {})
    documents = job_dict.get("documents") or getattr(job, "documents", {})

    structure = copy.deepcopy(job_dict.get("workbook") or raw_meta.get("workbook", {}))
    original_dir = Path(original_dir)

    for role in ("problem", "solution"):
        if "documents" in structure and role in structure["documents"] and role in documents:
            structure["documents"][role]["path"] = str(original_dir / documents[role])

    nodes = {n["id"]: n for n in structure.get("nodes", [])}

    if records is None:
        from _scripts_2.apps.exam.exam_processor.domain.canonical_mapping import item_to_canonical
        raw_items = job_dict.get("items", [])
        ordered_records = [item_to_canonical(job_dict, item) for item in raw_items]
    else:
        ordered_records = list(records)

    selected = list(dict.fromkeys(
        r.source.source_set_id or r.source.section_code
        for r in ordered_records
        if (r.source.source_set_id or r.source.section_code) and r.review.human_approval == "approved"
    ))
    manifest = []
    canonical_manifest_records = []
    exported_records = []
    index = [f"# {source_title}", ""]

    for node_id in selected:
        members = [
            r for r in ordered_records
            if (r.source.source_set_id or r.source.section_code) == node_id
        ]
        if any(r.review.human_approval != "approved" for r in members):
            raise ValueError(f"{node_id}: 해당 구간의 개념·예제·문제를 모두 승인하세요.")
        if node_id not in nodes:
            raise ValueError("출력할 목차를 찾지 못했습니다.")

        folder = stage / node_id
        folder.mkdir(parents=True, exist_ok=True)
        index += [
            f"## {node_id} {nodes[node_id]['title']}", "",
            f"[문제 PDF](./pdfs/{node_id}-problem.pdf) · [해설 PDF](./pdfs/{node_id}-solution.pdf)", ""
        ]
        names = set()

        for rec in members:
            number = normalize_number(rec.question.question_number)
            if not re.fullmatch(r"[A-Za-z0-9_-]+", number):
                number = rec.question.question_id

            kind = rec.question.content_kind
            prefix = "concept-" if kind == "concept" else "example-" if kind == "example" else ""
            name = prefix + number + ".md"
            if name in names:
                raise ValueError("정규화한 문항 파일명이 중복됩니다.")
            names.add(name)

            canonical_rec = copy.deepcopy(rec)
            prepare_workbook_record(
                canonical_rec, job,
                review_states.get(canonical_rec.question.question_id) if review_states else None
            )

            asset_plan = {(a.source_path or a.path): a.path for a in canonical_rec.assets}
            apply_export_asset_refs(canonical_rec, asset_plan, original_dir)

            (folder / name).write_text(project_to_markdown(canonical_rec), encoding="utf-8")
            json_name = prefix + number + ".json"
            json_path = folder / json_name
            json_path.write_text(canonical_rec.to_json(), encoding="utf-8")
            exported_records.append(canonical_rec)
            canonical_manifest_records.append({
                "record_id": canonical_rec.question.question_id,
                "json_path": str(json_path.relative_to(stage)),
                "markdown_path": str((folder / name).relative_to(stage)),
            })
            index.append(f"- [{rec.question.question_number}](./{node_id}/{name})")

            for asset in canonical_rec.assets:
                asset_path = asset.source_path or asset.path
                source = (original_dir / asset_path).resolve()
                target = (folder / asset.path).resolve()
                if not source.is_relative_to(original_dir.resolve()) or not target.is_relative_to(folder.resolve()):
                    raise ValueError("에셋 경로가 작업 폴더 밖입니다.")
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise ValueError("에셋 출력 경로가 중복됩니다.")
                shutil.copy2(source, target)
                manifest.append({
                    "item": canonical_rec.question.question_id,
                    "revision": canonical_rec.provenance.revision_id,
                    "section": asset.section,
                    "output": str(target.relative_to(stage.resolve())),
                    "sha256": digest(source),
                })
        index.append("")

    relation_issues = canonical_relation_errors(exported_records)
    if relation_issues:
        raise ValueError("정규 관계 오류: " + "; ".join(relation_issues))
    split_pdfs(structure, stage / "pdfs", selected)
    (stage / "index.md").write_text("\n".join(index), encoding="utf-8")
    verify_export_assets(stage, manifest)
    atomic_json(stage / "asset-manifest.json", manifest)
    atomic_json(stage / "workbook-manifest.json", {
        "workbook_id": structure.get("id"),
        "exported_nodes": selected,
        "profile": structure.get("profile"),
        "layout": layout,
    })
    write_canonical_manifest(stage, canonical_manifest_records)
