"""BundleDetector — Detects multi-file KS artifact bundles within directories.

Groups ArtifactRecords by directory and stem matching conventions:
    e.g., 26해4021/
            ├─ 26해4021.md (anchor)
            ├─ 26해4021 해석.md (sibling)
            └─ 26해4021 태도 정리.md (sibling)

Invariants:
    1. 100% Read-Only inspection on ArtifactRecords metadata.
    2. Does NOT perform KICE matching or candidate scoring (deferred to P2-D).
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from _scripts_2.apps.exam.exam_core.domain.artifact_models import ArtifactBundle, ArtifactRecord


class BundleDetector:
    """Detects multi-file KS artifact bundles."""

    def detect_bundles(self, records: List[ArtifactRecord]) -> List[ArtifactBundle]:
        """Group records into ArtifactBundles by folder and strict prefix matching.
        
        A group of files forms a multi-file bundle ONLY if:
        1. An anchor note exists (matching directory name or serving as prefix).
        2. Sibling notes explicitly start with f"{anchor_title} ".
        All other notes remain individual standalone bundles.
        """
        dir_groups: Dict[str, List[ArtifactRecord]] = defaultdict(list)
        for r in records:
            parent_dir = Path(r.source_path).parent.as_posix()
            dir_groups[parent_dir].append(r)

        bundles: List[ArtifactBundle] = []
        for dir_path, dir_records in dir_groups.items():
            dir_name = Path(dir_path).name
            if len(dir_records) == 1:
                bundles.append(
                    ArtifactBundle(
                        bundle_name=dir_records[0].title,
                        anchor=dir_records[0],
                        siblings=[],
                    )
                )
                continue

            # Check for multi-file bundles within this directory
            assigned_ids = set()

            # Find candidate anchors (sorted by title length ascending to prefer base names)
            candidate_anchors = sorted(dir_records, key=lambda r: len(r.title))
            for cand in candidate_anchors:
                if cand.artifact_id in assigned_ids:
                    continue

                prefix = f"{cand.title} "
                matching_siblings = [
                    r
                    for r in dir_records
                    if r.artifact_id != cand.artifact_id
                    and r.artifact_id not in assigned_ids
                    and r.title.startswith(prefix)
                ]

                if matching_siblings:
                    assigned_ids.add(cand.artifact_id)
                    for s in matching_siblings:
                        assigned_ids.add(s.artifact_id)

                    bundles.append(
                        ArtifactBundle(
                            bundle_name=cand.title or dir_name,
                            anchor=cand,
                            siblings=sorted(matching_siblings, key=lambda x: x.title),
                        )
                    )

            # Any unassigned records in this directory become standalone bundles
            for r in dir_records:
                if r.artifact_id not in assigned_ids:
                    bundles.append(
                        ArtifactBundle(
                            bundle_name=r.title,
                            anchor=r,
                            siblings=[],
                        )
                    )

        return sorted(bundles, key=lambda b: b.bundle_name)

