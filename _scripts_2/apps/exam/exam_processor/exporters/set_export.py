"""A combined passage/questions note and separate question/solution notes."""
import copy
import json
import re
import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord, canonical_relation_errors
from ..domain.models import digest, format_output_markdown
from .profiles.markdown import project_to_markdown
from .rebuild import write_canonical_manifest


def asset_export_plan(item_or_record, original_dir, reserved=None):
    """Rename assets only; content decisions belong to a reviewed item revision."""
    reserved = set() if reserved is None else reserved
    plan = {}
    original_dir = Path(original_dir)
    if hasattr(item_or_record, "question"):
        assets = [
            {"path": a.source_path or a.path, "section": a.section}
            for a in item_or_record.assets
        ]
        number = item_or_record.question.question_number
    else:
        assets = item_or_record["assets"]
        number = item_or_record["number"]

    for asset in assets:
        source = original_dir / asset["path"]
        if not source.resolve().is_relative_to(original_dir.resolve()) or not source.is_file():
            raise ValueError("에셋 원본 파일이 없거나 작업 폴더 밖에 있습니다.")
        if asset["path"] in plan:
            raise ValueError("같은 에셋 경로가 중복 등록되었습니다.")
        section = asset["section"]
        if section not in ("body", "solution") or not re.fullmatch(r"[A-Za-z0-9_-]+", number):
            raise ValueError("에셋 소유 항목 또는 영역이 잘못되었습니다.")
        filename = source.name
        parent = f"assets/{number}/{section}"
        target = f"{parent}/{filename}"
        suffix = 2
        while target in reserved:
            target = f"{parent}/{source.stem}-{suffix}{source.suffix}"
            suffix += 1
        reserved.add(target)
        plan[asset["path"]] = target
    return plan


def rewrite_assets(content, plan):
    def replace(match):
        source = match[1]
        if source not in plan:
            raise ValueError(f"등록되지 않은 이미지 참조입니다: {source}")
        return f"![[{plan[source]}]]"
    return re.sub(r"!\[\[([^\]]+)\]\]", replace, content)


def apply_export_asset_refs(record: CanonicalQuestionRecord, *args):
    if len(args) == 2:
        plan, original_dir = args
    elif len(args) == 3:
        _, plan, original_dir = args
    else:
        raise ValueError("Invalid arguments to apply_export_asset_refs")

    root = Path(original_dir).resolve()
    for asset in record.assets:
        source_path = asset.source_path or asset.path
        if source_path not in plan:
            raise ValueError(f"정규 에셋 참조를 출력 경로에 연결할 수 없습니다: {source_path}")
        source = (root / source_path).resolve()
        target_path = PurePosixPath(plan[source_path])
        if (not source.is_relative_to(root) or not source.is_file()
                or not target_path.parts or target_path.is_absolute() or ".." in target_path.parts
                or "\\" in plan[source_path]):
            raise ValueError(f"정규 에셋 경로가 안전하지 않거나 원본이 없습니다: {source_path}")
        asset.path = plan[source_path]
        asset.sha256 = digest(source)
    record.body = rewrite_assets(record.body, plan)
    record.solution = rewrite_assets(record.solution, plan)


def verify_export_assets(stage, manifest):
    """Verify copied bytes and all local image links before publishing the batch."""
    expected = {entry["output"] for entry in manifest}
    if len(expected) != len(manifest):
        raise ValueError("출력 이미지 경로가 중복됩니다.")
    for entry in manifest:
        target = stage / entry["output"]
        if not target.is_file() or digest(target) != entry["sha256"]:
            raise ValueError(f"검수본과 출력 이미지가 다릅니다: {entry['output']}")
    contents = [(note, note.read_text(encoding="utf-8")) for note in stage.rglob("*.md")]
    for note in stage.rglob("*.json"):
        data = json.loads(note.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "schema_version" in data and "question" in data:
            contents.append((note, data.get("body", "") + "\n" + data.get("solution", "")))
    for note, content in contents:
        for link in re.findall(r"!\[\[([^\]]+)\]\]", content):
            target = (note.parent / link).resolve()
            if not target.is_relative_to(stage.resolve()) or str(target.relative_to(stage.resolve())) not in expected:
                raise ValueError(f"출력 이미지 참조가 일치하지 않습니다: {note.name}: {link}")


def write_sets(stage, job, code, original_dir, *, records=None, review_states=None):
    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", code):
        raise ValueError("출처 코드는 2606처럼 영문·숫자·하이픈·밑줄로 입력하세요.")

    source_title = job.source if hasattr(job, "source") else job.get("source", "")
    job_dict = job.to_dict() if hasattr(job, "to_dict") else dict(job)

    if records is None:
        from _scripts_2.apps.exam.exam_processor.domain.canonical_mapping import item_to_canonical
        raw_items = job_dict.get("items", [])
        ordered_records = [item_to_canonical(job_dict, item) for item in raw_items]
    else:
        ordered_records = list(records)

    contexts = list(dict.fromkeys(
        rec.question.question_set_id or rec.source.source_set_id or rec.source.section_code or ""
        for rec in ordered_records
    ))
    written = 0
    asset_manifest = []
    canonical_manifest_records = []
    exported_records = []

    for index, context in enumerate(contexts, 1):
        members = [
            r for r in ordered_records
            if (r.question.question_set_id or r.source.source_set_id or r.source.section_code or "") == context
        ]
        questions = [r for r in members if r.question.content_kind == "question"]
        if not any(r.review.human_approval == "approved" for r in questions):
            continue
        if any(r.review.human_approval != "approved" for r in members):
            raise ValueError(f"{context}: 지문과 소속 문항을 모두 승인한 뒤 세트를 저장하세요.")

        folder = stage / f"{code} {index}"
        folder.mkdir(parents=True, exist_ok=True)
        passages = [r for r in members if r.question.content_kind == "passage"]
        combined_name = f"{code}_{index}.md"
        combined = f"# {source_title} [{context}]\n\n## 공통 지문\n\n"
        reserved = set()
        passage_plans = {r.question.question_id: asset_export_plan(r, original_dir, reserved) for r in passages}
        question_plans = {r.question.question_id: asset_export_plan(r, original_dir, reserved) for r in questions}

        combined += "\n\n".join(
            format_output_markdown(rewrite_assets(r.body, passage_plans[r.question.question_id]))
            for r in passages
        )
        combined += "\n\n## 문제\n\n" + "\n\n---\n\n".join(
            format_output_markdown(rewrite_assets(r.body, question_plans[r.question.question_id]))
            for r in questions
        ) + "\n"
        (folder / combined_name).write_text(format_output_markdown(combined), encoding="utf-8")

        for rec in passages:
            passage_record = copy.deepcopy(rec)
            passage_plan = passage_plans[rec.question.question_id]
            apply_export_asset_refs(passage_record, passage_plan, original_dir)
            passage_json = folder / f"passage-{rec.question.question_id}.json"
            passage_json.write_text(passage_record.to_json(), encoding="utf-8")
            exported_records.append(passage_record)
            canonical_manifest_records.append({
                "record_id": passage_record.question.question_id,
                "json_path": str(passage_json.relative_to(stage)),
                "markdown_path": str((folder / f"passage-{rec.question.question_id}.md").relative_to(stage)),
            })

        names = set()
        for rec in questions:
            q_num = rec.question.question_number
            if not re.fullmatch(r"[0-9]+(?:-[0-9]+)?", q_num):
                raise ValueError("지원하지 않는 문항 파일 번호입니다.")
            name = f"{code}{q_num}.md"
            if name in names or name == combined_name:
                raise ValueError("문항 파일명이 중복됩니다.")
            names.add(name)

            canonical_rec = copy.deepcopy(rec)
            canonical_rec.question.display_code = name[:-3]
            apply_export_asset_refs(canonical_rec, question_plans[rec.question.question_id], original_dir)

            content = project_to_markdown(canonical_rec)
            if passages:
                link = f"공통 지문: [{code}_{index}](./{quote(combined_name)}#공통%20지문)\n\n"
                content = re.sub(r"(<!-- SECTION:PROBLEM_START -->\n+)", r"\1" + link, content, count=1)
            (folder / name).write_text(format_output_markdown(content), encoding="utf-8")

            # Canonical JSON sidecar
            json_name = f"{code}{q_num}.json"
            json_path = folder / json_name
            json_path.write_text(canonical_rec.to_json(), encoding="utf-8")
            exported_records.append(canonical_rec)
            canonical_manifest_records.append({
                "record_id": canonical_rec.question.question_id,
                "json_path": str(json_path.relative_to(stage)),
                "markdown_path": str((folder / name).relative_to(stage)),
            })

        for rec in members:
            plan = passage_plans.get(rec.question.question_id, question_plans.get(rec.question.question_id, {}))
            for asset in rec.assets:
                asset_path = asset.source_path or asset.path
                relative = plan[asset_path]
                target = folder / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                asset_manifest.append({
                    "item": rec.question.question_id,
                    "revision": rec.provenance.revision_id,
                    "section": asset.section,
                    "source": asset_path,
                    "output": str(target.relative_to(stage)),
                    "sha256": digest(original_dir / asset_path),
                })
                shutil.copy2(original_dir / asset_path, target)
        written += 1

    if not written:
        raise ValueError("내보낼 승인 세트가 없습니다.")
    relation_issues = canonical_relation_errors(exported_records)
    if relation_issues:
        raise ValueError("정규 관계 오류: " + "; ".join(relation_issues))
    verify_export_assets(stage, asset_manifest)
    (stage / "asset-manifest.json").write_text(
        json.dumps(asset_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_canonical_manifest(stage, canonical_manifest_records)
