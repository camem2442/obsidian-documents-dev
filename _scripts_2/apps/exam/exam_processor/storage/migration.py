"""Atomic migration from v1 job.json (with items[]) to v2 Canonical storage layout."""
from __future__ import annotations

import copy
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from _scripts_2.apps.exam.exam_processor.domain.canonical_mapping import item_to_canonical as legacy_item_to_canonical
from ..domain.job_manifest import JobManifest
from ..domain.review_state import ReviewState
from .job_repository import JobRepository
from .record_repository import RecordRepository
from .review_repository import ReviewRepository
from .revision_repository import RevisionRepository


def is_v1_job(job_dir: Path) -> bool:
    path = job_dir / "job.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("storage_version", 1) < 2 or "items" in data
    except Exception:
        return False


def migrate_job(job_dir: Path, dry_run: bool = False) -> Dict[str, Any]:
    """Migrate a single job folder from v1 to v2 with backup."""
    path = job_dir / "job.json"
    if not path.is_file():
        raise ValueError(f"Job file not found in {job_dir}")

    v1_data = json.loads(path.read_text(encoding="utf-8"))
    if v1_data.get("storage_version", 1) >= 2 and "items" not in v1_data:
        return {"status": "already_v2", "job_id": v1_data.get("id", job_dir.name)}

    job_id = v1_data.get("id", job_dir.name)
    items = v1_data.get("items", [])
    record_ids: List[str] = []

    record_repo = RecordRepository()
    review_repo = ReviewRepository()
    revision_repo = RevisionRepository()
    job_repo = JobRepository()

    records_to_save = []
    reviews_to_save = []

    for item in items:
        record_id = item.get("id")
        if not record_id:
            continue
        record_ids.append(record_id)
        record = legacy_item_to_canonical(v1_data, item)
        rev_id = item.get("revision") or record.provenance.revision_id or str(uuid.uuid4())
        record.provenance.revision_id = rev_id
        review_state = ReviewState(
            record_id=record_id,
            applies_to_revision_id=rev_id,
            published_revision_id=item.get("published_revision", ""),
            note=item.get("note", ""),
            audit=item.get("audit"),
            audit_waiver=item.get("audit_waiver"),
            warnings=list(item.get("warnings", [])),
            quality_checks=list(item.get("quality_checks", [])),
            transcription_pending=list(item.get("transcription_pending", [])),
            source_styles=list(item.get("source_styles", [])),
            style_mapping_errors=list(item.get("style_mapping_errors", [])),
            group_mapping_errors=list(item.get("group_mapping_errors", [])),
            box_checks=list(item.get("box_checks", [])),
            solution_candidates=list(item.get("solution_candidates", [])),
            selected_solution_id=item.get("selected_solution_id", ""),
            solution_match=item.get("solution_match"),
            solution_link_backup=item.get("solution_link_backup"),
            ai_runs=list(item.get("ai_runs", [])),
            material_validation_errors=list(item.get("material_validation_errors", [])),
        )
        records_to_save.append(record)
        reviews_to_save.append((record, review_state))

    manifest_dict = copy.deepcopy(v1_data)
    manifest_dict["storage_version"] = 2
    manifest_dict["record_ids"] = record_ids
    manifest_dict.pop("items", None)
    manifest = JobManifest.from_dict(manifest_dict)

    if not dry_run:
        # Create backup if not already present
        backup_path = job_dir / "job.v1.backup.json"
        if not backup_path.exists():
            shutil.copy2(path, backup_path)

        for rec in records_to_save:
            record_repo.save(job_dir, rec)

        for rec, rev_st in reviews_to_save:
            review_repo.save(job_dir, rev_st)
            revision_repo.save_snapshot(
                job_dir, rec.question.question_id, rev_st.applies_to_revision_id, rec, rev_st
            )

        job_repo.save_manifest(job_dir, manifest)

    return {
        "status": "migrated" if not dry_run else "dry_run",
        "job_id": job_id,
        "items_count": len(record_ids),
    }


def migrate_all_jobs(jobs_dir: Path, dry_run: bool = False) -> List[Dict[str, Any]]:
    """Migrate all v1 jobs in the jobs directory."""
    results = []
    if not jobs_dir.is_dir():
        return results

    for job_path in sorted(jobs_dir.glob("*/job.json")):
        job_folder = job_path.parent
        if is_v1_job(job_folder):
            res = migrate_job(job_folder, dry_run=dry_run)
            results.append(res)
    return results
