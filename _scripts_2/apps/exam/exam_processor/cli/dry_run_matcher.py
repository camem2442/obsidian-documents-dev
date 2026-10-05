"""Dry-Run Matcher CLI — 0-Write full vault evaluation of KS notes against KICE Master.

Invariants:
1. True 0-Write on SSOT: Verified via pre/post directory snapshot comparison of protected roots.
2. Ephemeral Snapshots: Uses in-memory ArtifactRecords without mutating ArtifactRepository.
3. SSOT Path Fidelity: Defaults to DATA_ROOT/library/kice and reads authoritative path-index.json correctly.
4. Granular Skip Reason Decomposition: Categorizes skipped notes (NON_ORIGIN_NOTE, NON_KICE_AUTHORITY, WORKBOOK_OCCURRENCE, EBS_ITEM, TOMBSTONE, EXCLUDED_PATH).
5. Explicit Master Status: Reports library_root, master_record_count, origin_index_entry_count, and flags INCONCLUSIVE_EMPTY_MASTER_LIBRARY when empty.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import DATA_ROOT, DOCUMENTS_ROOT, KS_ROOT
from _scripts_2.apps.exam.exam_core.domain.artifact_models import ArtifactRecord
from ..domain.match_models import MatchDecision
from ..domain.services.evidence_matcher import EvidenceMatcher
from ..domain.services.origin_evidence_parser import OriginEvidenceParser
from _scripts_2.apps.exam.exam_core.indexing.origin_index import OriginIndex
from _scripts_2.apps.exam.exam_core.storage.artifact_repository import ArtifactIndexCorruptionError
from _scripts_2.apps.exam.exam_core.storage.kice_master_repository import KiceMasterRepository


def _compute_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_excluded_path(p: Path) -> bool:
    if p.name.startswith(".") or any(part.startswith(".") for part in p.parts):
        return True
    if any(part in ("00 미분류", "00") for part in p.parts):
        return True
    return False


def _snapshot_protected_roots(roots: List[Path], ignore_paths: Optional[List[Path]] = None) -> Dict[str, Tuple[int, int]]:
    """Capture file snapshot (size, mtime_ns) across protected roots for mutation verification."""
    ignored = {p.resolve() for p in (ignore_paths or []) if p}
    snapshot: Dict[str, Tuple[int, int]] = {}
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if root.resolve() not in ignored:
                stat = root.stat()
                snapshot[str(root.resolve())] = (stat.st_size, stat.st_mtime_ns)
            continue
        for p in root.rglob("*"):
            if p.is_file() and p.resolve() not in ignored:
                try:
                    stat = p.stat()
                    snapshot[str(p.resolve())] = (stat.st_size, stat.st_mtime_ns)
                except Exception:
                    pass
    return snapshot


def categorize_skip_reason(rejection_reason: Optional[str]) -> str:
    """Categorize reason string into standardized skip category."""
    if not rejection_reason:
        return "NON_ORIGIN_NOTE"
    reason_lower = rejection_reason.lower()
    if "hanwangi" in reason_lower or "해" in reason_lower:
        return "WORKBOOK_OCCURRENCE"
    if "ebs" in reason_lower:
        return "EBS_ITEM"
    if any(auth in reason_lower for auth in ["경찰", "사관", "교육청", "학평", "전국연합", "한능검", "leet", "meet", "peet", "공무원"]):
        return "NON_KICE_AUTHORITY"
    if "absent" in reason_lower or "deleted" in reason_lower:
        return "TOMBSTONE"
    if "does not match kice 6-digit" in reason_lower or "non-standard" in reason_lower:
        return "NON_ORIGIN_NOTE"
    return "NON_ORIGIN_NOTE"


def run_dry_run_matcher(
    ks_root: Path,
    library_root: Path,
    repo_root: Optional[Path] = None,
    output_path: Optional[Path] = None,
    max_samples_per_category: int = 5,
) -> Dict[str, Any]:
    """Execute pure dry-run match evaluation over all markdown notes in ks_root."""
    artifacts_root = library_root.parent / "artifacts"
    protected_roots = [ks_root, library_root, artifacts_root]
    snap_before = _snapshot_protected_roots(protected_roots, ignore_paths=[output_path] if output_path else None)

    start_time = time.perf_counter()

    master_repo = KiceMasterRepository(library_root=library_root)
    origin_index = OriginIndex()

    master_ids = master_repo.list_ids()
    master_record_count = len(master_ids)

    index_file = library_root / "indexes" / "origin_index.json"
    if index_file.is_file():
        origin_index.load(index_file)
    else:
        origin_index.build_from_repository(master_repo)

    origin_index_entry_count = len(origin_index)

    # Load known path index if present (read-only dict mapping path -> artifact_id)
    known_path_index: Dict[str, str] = {}
    path_idx_file = artifacts_root / "path-index.json"
    if path_idx_file.is_file():
        try:
            data = json.loads(path_idx_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ArtifactIndexCorruptionError(
                    f"path-index.json corrupted: expected dict, got {type(data).__name__}"
                )
            known_path_index = data
        except Exception as exc:
            raise ArtifactIndexCorruptionError(f"Failed to load path-index.json at {path_idx_file}: {exc}") from exc

    matcher = EvidenceMatcher(
        master_repo=master_repo,
        origin_index=origin_index,
        parser=OriginEvidenceParser(),
    )

    discovered_files = 0
    excluded_files = 0
    eligible_files = 0

    counts = {d.value: 0 for d in MatchDecision}
    skip_reason_counts: Dict[str, int] = {
        "NON_ORIGIN_NOTE": 0,
        "NON_KICE_AUTHORITY": 0,
        "WORKBOOK_OCCURRENCE": 0,
        "EBS_ITEM": 0,
        "TOMBSTONE": 0,
        "EXCLUDED_PATH": 0,
    }

    samples: Dict[str, List[Dict[str, Any]]] = {d.value: [] for d in MatchDecision}

    if ks_root.exists():
        for p in ks_root.rglob("*.md"):
            discovered_files += 1

            if _is_excluded_path(p):
                excluded_files += 1
                skip_reason_counts["EXCLUDED_PATH"] += 1
                continue

            eligible_files += 1

            rel_path = (
                p.relative_to(repo_root).as_posix()
                if repo_root and p.is_relative_to(repo_root)
                else p.as_posix()
            )

            content_sha = _compute_sha256(p)
            file_size = p.stat().st_size
            artifact_id = known_path_index.get(rel_path) or f"ephemeral-{content_sha[:12]}"

            ephemeral_artifact = ArtifactRecord(
                artifact_id=artifact_id,
                source_path=rel_path,
                content_sha256=content_sha,
                file_size=file_size,
                title=p.stem,
                present=True,
            )

            report = matcher.match_artifact(ephemeral_artifact)
            counts[report.decision] = counts.get(report.decision, 0) + 1

            if report.decision == MatchDecision.MATCH_SKIPPED.value:
                reason_cat = categorize_skip_reason(report.notes[0] if report.notes else None)
                skip_reason_counts[reason_cat] = skip_reason_counts.get(reason_cat, 0) + 1

            if len(samples[report.decision]) < max_samples_per_category:
                samples[report.decision].append({
                    "source_path": rel_path,
                    "decision": report.decision,
                    "candidate_count": len(report.candidates),
                    "candidates": [
                        {
                            "question_id": c.question_id,
                            "origin_key": c.origin_key,
                            "status": c.status,
                            "relation_hint": c.relation_type_hint,
                            "evidences": [e.normalized_string() for e in c.evidences],
                        }
                        for c in report.candidates
                    ],
                    "notes": report.notes,
                    "fingerprint": report.report_fingerprint,
                })

    duration_sec = time.perf_counter() - start_time

    snap_after = _snapshot_protected_roots(protected_roots, ignore_paths=[output_path] if output_path else None)
    zero_disk_mutation_on_ssot = (snap_before == snap_after)

    validation_status = (
        "VALIDATED"
        if master_record_count > 0
        else "INCONCLUSIVE_EMPTY_MASTER_LIBRARY"
    )

    return {
        "execution_duration_sec": round(duration_sec, 4),
        "validation_status": validation_status,
        "library_metadata": {
            "library_root": str(library_root),
            "master_record_count": master_record_count,
            "origin_index_entry_count": origin_index_entry_count,
        },
        "zero_disk_mutation_on_ssot": zero_disk_mutation_on_ssot,
        "scanned_summary": {
            "discovered_files": discovered_files,
            "excluded_files": excluded_files,
            "eligible_files": eligible_files,
            "matched_files": counts.get(MatchDecision.MATCH_EXACT_ORIGIN.value, 0) + counts.get(MatchDecision.MATCH_SINGLE_CANDIDATE.value, 0),
        },
        "decision_distribution": counts,
        "skip_reason_distribution": skip_reason_counts,
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="0-Write Dry-Run Matcher for KS Vault")
    parser.add_argument("--ks-root", type=str, default=str(KS_ROOT), help="Path to KS vault directory")
    parser.add_argument("--library-root", type=str, default=str(DATA_ROOT / "library" / "kice"), help="Path to KICE library root")
    parser.add_argument("--repo-root", type=str, default=str(DOCUMENTS_ROOT), help="Repository root directory")
    parser.add_argument("--output", type=str, default="", help="Optional output JSON path (SSOT-zero-write mode)")
    parser.add_argument("--stdout", action="store_true", help="Force print JSON report to stdout (Disk-zero-write mode)")

    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    ks_root = Path(args.ks_root).resolve()
    library_root = Path(args.library_root).resolve()
    output_path = Path(args.output).resolve() if args.output else None

    report = run_dry_run_matcher(
        ks_root=ks_root,
        library_root=library_root,
        repo_root=repo_root,
        output_path=output_path,
    )

    report_json = json.dumps(report, ensure_ascii=False, indent=2)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_json, encoding="utf-8")
        print(f"Report saved to {output_path} (SSOT-zero-write mode)")
    else:
        print(report_json)


if __name__ == "__main__":
    main()
