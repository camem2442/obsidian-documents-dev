"""Rebuild Markdown records from a self-contained canonical JSON batch."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shutil
from pathlib import Path, PurePosixPath

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord
from .profiles.markdown import project_to_markdown


MANIFEST_NAME = "canonical-manifest.json"
MANIFEST_VERSION = "1"


def _safe_relative(value):
    path = PurePosixPath(value) if isinstance(value, str) else None
    if (path is None or not value or not path.parts or path.is_absolute() or ".." in path.parts
            or "\\" in value):
        raise ValueError(f"배치 경로는 안전한 상대 경로여야 합니다: {value}")
    return path


def write_canonical_manifest(stage, records):
    ids, json_paths, markdown_paths = set(), set(), set()
    for record in records:
        record_id = record.get("record_id")
        json_path = _safe_relative(record.get("json_path"))
        markdown_path = _safe_relative(record.get("markdown_path"))
        if not record_id or record_id in ids:
            raise ValueError("canonical manifest record_id 값이 비었거나 중복되었습니다.")
        if json_path in json_paths or markdown_path in markdown_paths:
            raise ValueError("canonical manifest 파일 경로가 중복되었습니다.")
        if json_path.parent != markdown_path.parent:
            raise ValueError("각 정규 JSON과 Markdown 출력은 같은 상대 폴더에 있어야 합니다.")
        ids.add(record_id)
        json_paths.add(json_path)
        markdown_paths.add(markdown_path)
    payload = {"manifest_version": MANIFEST_VERSION, "records": records}
    (Path(stage) / MANIFEST_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rebuild_markdown_batch(batch_root, output_root):
    """Render every listed record and copy its verified assets without job data."""
    batch_root, output_root = Path(batch_root).resolve(), Path(output_root).resolve()
    if (batch_root == output_root or batch_root.is_relative_to(output_root)
            or output_root.is_relative_to(batch_root)):
        raise ValueError("원본 배치와 재생성 대상은 서로 겹치지 않는 별도 폴더여야 합니다.")
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError("기존 파일을 덮어쓰지 않도록 비어 있는 재생성 폴더를 지정하세요.")
    manifest_path = batch_root / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("manifest_version") != MANIFEST_VERSION
            or not isinstance(manifest.get("records"), list) or not manifest["records"]):
        raise ValueError("지원하지 않는 canonical manifest 형식입니다.")

    entries = {}
    markdown_paths = set()
    for entry in manifest["records"]:
        record_id = entry.get("record_id")
        json_rel = _safe_relative(entry.get("json_path"))
        markdown_rel = _safe_relative(entry.get("markdown_path"))
        if json_rel.parent != markdown_rel.parent:
            raise ValueError("정규 JSON과 Markdown 출력의 상대 폴더가 다릅니다.")
        if not record_id or record_id in entries or markdown_rel in markdown_paths:
            raise ValueError("canonical manifest에 중복되거나 비어 있는 레코드가 있습니다.")
        json_path = (batch_root / Path(*json_rel.parts)).resolve()
        if not json_path.is_relative_to(batch_root) or not json_path.is_file():
            raise ValueError(f"manifest의 정규 JSON 파일이 없거나 범위를 벗어납니다: {json_rel}")
        record = CanonicalQuestionRecord.from_dict(json.loads(json_path.read_text(encoding="utf-8")))
        if record.question.question_id != record_id:
            raise ValueError(f"manifest와 JSON 레코드 ID가 다릅니다: {json_rel}")
        entries[record_id] = (record, json_path, json_rel, markdown_rel)
        markdown_paths.add(markdown_rel)

    written = []
    copied_assets = {}
    for record, json_path, _, markdown_rel in entries.values():
        destination = (output_root / Path(*markdown_rel.parts)).resolve()
        if not destination.is_relative_to(output_root):
            raise ValueError("Markdown 출력 경로가 대상 폴더 밖입니다.")
        destination.parent.mkdir(parents=True, exist_ok=True)

        asset_paths = {asset.path for asset in record.assets}
        for content in (record.body, record.solution):
            for link in re.findall(r"!\[\[([^\]]+)\]\]", content):
                if link not in asset_paths:
                    raise ValueError(f"정규 JSON에 등록되지 않은 에셋 링크입니다: {link}")

        for asset in record.assets:
            asset_rel = _safe_relative(asset.path)
            source = (json_path.parent / Path(*asset_rel.parts)).resolve()
            target = (destination.parent / Path(*asset_rel.parts)).resolve()
            if not source.is_relative_to(json_path.parent.resolve()) or not source.is_file():
                raise ValueError(f"정규 JSON이 참조하는 에셋이 없거나 범위를 벗어납니다: {asset.path}")
            if not target.is_relative_to(output_root):
                raise ValueError(f"에셋 출력 경로가 대상 폴더 밖입니다: {asset.path}")
            if not asset.sha256 or not re.fullmatch(r"[0-9a-f]{64}", asset.sha256):
                raise ValueError(f"독립 재생성에 필요한 에셋 해시가 없습니다: {asset.path}")
            actual_hash = _sha256(source)
            if actual_hash != asset.sha256:
                raise ValueError(f"에셋 해시가 정규 JSON과 다릅니다: {asset.path}")
            previous = copied_assets.get(target)
            if previous and previous != actual_hash:
                raise ValueError(f"서로 다른 에셋이 같은 출력 경로를 사용합니다: {asset.path}")
            if not previous:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied_assets[target] = actual_hash

        passage_ids = record.relations.passage_ids
        passage_file = ""
        if passage_ids:
            if len(passage_ids) != 1:
                raise ValueError("현재 Markdown 출력은 문항당 지문 관계 하나만 지원합니다.")
            passage_entry = entries.get(passage_ids[0])
            if not passage_entry or passage_entry[0].question.content_kind != "passage":
                raise ValueError(f"연결된 지문 레코드를 배치에서 찾을 수 없습니다: {passage_ids[0]}")
            passage_file = posixpath.relpath(
                passage_entry[3].as_posix(), markdown_rel.parent.as_posix() or "."
            )
        solution_files = []
        for solution_id in record.relations.solution_ids:
            solution_entry = entries.get(solution_id)
            if not solution_entry or solution_entry[0].question.content_kind != "solution":
                raise ValueError(f"연결된 해설 레코드를 배치에서 찾을 수 없습니다: {solution_id}")
            solution_files.append(posixpath.relpath(
                solution_entry[3].as_posix(), markdown_rel.parent.as_posix() or "."
            ))
        script_files = []
        for script_id in record.relations.listening_script_ids:
            script_entry = entries.get(script_id)
            if not script_entry or script_entry[0].question.content_kind != "script":
                raise ValueError(f"연결된 듣기 대본 레코드를 배치에서 찾을 수 없습니다: {script_id}")
            script_files.append(posixpath.relpath(
                script_entry[3].as_posix(), markdown_rel.parent.as_posix() or "."
            ))
        markdown = project_to_markdown(
            record, passage_file=passage_file, solution_files=solution_files, script_files=script_files
        )
        destination.write_text(markdown, encoding="utf-8")
        written.append(destination)
    return written
