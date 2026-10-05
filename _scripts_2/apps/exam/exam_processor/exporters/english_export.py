"""Publish a reviewed English exam using the shared canonical Markdown profile."""
from __future__ import annotations

import copy
import json
import posixpath
import re
import shutil
from pathlib import Path

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord, canonical_relation_errors
from ..domain.models import digest
from .profiles.markdown import project_to_markdown
from .rebuild import write_canonical_manifest
from .set_export import apply_export_asset_refs


def output_stem(number):
    number = str(number or "")
    return number.zfill(2) if number.isdigit() else number


def write_english_batch(stage, job, original_dir, *, records=None, review_states=None):
    job_dict = job.to_dict() if hasattr(job, "to_dict") else dict(job)
    fmt = job.format if hasattr(job, "format") else job_dict.get("format")
    source_title = job.source if hasattr(job, "source") else job_dict.get("source", "")
    documents_spec = job.documents if hasattr(job, "documents") else job_dict.get("documents", {})
    input_docs = job_dict.get("input_documents", {})

    if fmt != "kice_english":
        raise ValueError("평가원 영어 작업만 영어 배치로 출력할 수 있습니다.")

    if records is None:
        from _scripts_2.apps.exam.exam_processor.domain.canonical_mapping import item_to_canonical
        raw_items = job_dict.get("items", [])
        ordered_records = [item_to_canonical(job_dict, item) for item in raw_items]
    else:
        ordered_records = list(records)

    if any(r.review.human_approval != "approved" for r in ordered_records):
        raise ValueError("영어 문항·공통 지문·대본을 모두 검수·승인한 뒤 출력하세요.")

    stage, original_dir = Path(stage), Path(original_dir)
    exported_records, paths, manifest = [], {}, []

    for rec in ordered_records:
        canonical_rec = copy.deepcopy(rec)
        kind = canonical_rec.question.content_kind
        folder = {"question": "questions", "passage": "passages", "script": "scripts"}.get(kind)
        if folder is None:
            raise ValueError(f"지원하지 않는 영어 자료 유형: {kind}")
        number = output_stem(canonical_rec.question.question_number)
        relative = Path(folder) / f"{number}.md"
        if relative in paths.values():
            raise ValueError(f"영어 출력 파일명이 중복됩니다: {relative}")

        plan = {(a.source_path or a.path): a.path for a in canonical_rec.assets}
        apply_export_asset_refs(canonical_rec, plan, original_dir)
        exported_records.append(canonical_rec)
        paths[canonical_rec.question.question_id] = relative

        output_folder = stage / folder
        output_folder.mkdir(parents=True, exist_ok=True)
        json_path = output_folder / f"{number}.json"
        json_path.write_text(canonical_rec.to_json(), encoding="utf-8")
        manifest.append({
            "record_id": canonical_rec.question.question_id,
            "json_path": str(json_path.relative_to(stage)),
            "markdown_path": str(relative),
        })

        for asset in canonical_rec.assets:
            asset_path = asset.source_path or asset.path
            source = (original_dir / asset_path).resolve()
            target = (output_folder / asset.path).resolve()
            if not source.is_relative_to(original_dir.resolve()) or not target.is_relative_to(output_folder.resolve()):
                raise ValueError("영어 에셋 경로가 작업 영역을 벗어났습니다.")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError("영어 에셋 경로가 중복됩니다.")
            shutil.copy2(source, target)
            if digest(target) != asset.sha256:
                raise ValueError("영어 에셋 복사 검증에 실패했습니다.")

    issues = canonical_relation_errors(exported_records)
    if issues:
        raise ValueError("영어 정규 관계 오류: " + "; ".join(issues))

    def link(source, target_id):
        target = paths.get(target_id)
        if target is None:
            raise ValueError(f"영어 연결 대상을 찾을 수 없습니다: {target_id}")
        return posixpath.relpath(target.as_posix(), source.parent.as_posix())

    for canonical_rec in exported_records:
        relative = paths[canonical_rec.question.question_id]
        passage_ids = canonical_rec.relations.passage_ids
        if len(passage_ids) > 1:
            raise ValueError("문항당 공통 지문은 하나만 지원합니다.")
        markdown = project_to_markdown(
            canonical_rec,
            passage_file=link(relative, passage_ids[0]) if passage_ids else "",
            script_files=[link(relative, script_id)
                          for script_id in canonical_rec.relations.listening_script_ids],
        )
        (stage / relative).write_text(markdown, encoding="utf-8")

    for markdown_path in stage.rglob("*.md"):
        content = markdown_path.read_text(encoding="utf-8")
        for link_target in re.findall(r"!\[\[([^\]]+)\]\]", content):
            target = (markdown_path.parent / link_target).resolve()
            if not target.is_relative_to(stage.resolve()) or not target.is_file():
                raise ValueError(f"영어 출력 연결이 없거나 배치 밖입니다: {markdown_path.name}: {link_target}")

    write_canonical_manifest(stage, manifest)
    documents = {}
    for role, filename in documents_spec.items():
        source = original_dir / filename
        if source.is_file():
            documents[role] = {
                "path": str(source),
                "original_path": input_docs.get(role, ""),
                "sha256": digest(source),
            }
    (stage / "source-documents.json").write_text(
        json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    index = [f"# {source_title}", "", "검수·승인된 문항. 사용자 해설은 별도 추가 예정.", ""]
    for canonical_rec in exported_records:
        if canonical_rec.question.content_kind == "question":
            relative = paths[canonical_rec.question.question_id]
            index.append(f"- [{canonical_rec.question.question_number}번]({relative.as_posix()})")
    (stage / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
