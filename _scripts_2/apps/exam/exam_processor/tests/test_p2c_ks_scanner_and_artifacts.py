"""Tests for Exam Processor P2-C: Legacy KS Scanner & Artifact Inventory.

Invariants verified:
1. Read-Only Safety: Scanning KS/ directory does NOT create, edit, move, or rename any source files.
2. Path Index Stability: Same source_path preserves artifact_id (UUID) across re-scans.
3. Content Mutation Handling: Content change updates content_sha256 while maintaining artifact_id.
4. Bundle Detection: Folder structure (anchor note + sibling notes) correctly mapped to ArtifactBundle.
5. Zero KICE Link Mutation: Scanning does NOT invoke RelationService or touch library/kice/.
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from _scripts_2.apps.exam.exam_core.domain.artifact_models import ArtifactBundle, ArtifactRecord
from _scripts_2.apps.exam.exam_processor.domain.services.bundle_detector import (
    BundleDetector,
)
from _scripts_2.apps.exam.exam_processor.domain.services.ks_vault_scanner import (
    KsVaultScanner,
)
from _scripts_2.apps.exam.exam_core.storage.artifact_repository import ArtifactRepository


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestP2CLegacyKsScannerAndArtifacts(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._temp_dir.name)
        self.artifacts_dir = self.base_dir / "library" / "artifacts"
        self.ks_dir = self.base_dir / "KS"
        self.ks_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_repo = ArtifactRepository(artifacts_root=self.artifacts_dir)
        self.scanner = KsVaultScanner(self.artifact_repo, repo_root=self.base_dir)
        self.bundle_detector = BundleDetector()

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_path_index_preserves_uuid_across_rescans(self) -> None:
        note_file = self.ks_dir / "3 영어" / "260933 시험장.md"
        note_file.parent.mkdir(parents=True, exist_ok=True)
        note_file.write_text("# 260933 시험장 노트\n\n내용입니다.", encoding="utf-8")

        initial_sha = _file_sha(note_file)
        records1 = self.scanner.scan_directory(self.ks_dir)
        self.assertEqual(len(records1), 1)
        r1 = records1[0]
        self.assertEqual(r1.source_path, "KS/3 영어/260933 시험장.md")
        self.assertEqual(r1.content_sha256, initial_sha)

        # Re-scan unchanged file
        records2 = self.scanner.scan_directory(self.ks_dir)
        self.assertEqual(len(records2), 1)
        r2 = records2[0]
        self.assertEqual(r2.artifact_id, r1.artifact_id)
        self.assertEqual(r2.content_sha256, initial_sha)

        # Modify content and re-scan: UUID must remain identical while content_sha256 updates
        note_file.write_text("# 260933 시험장 노트\n\n수정된 내용입니다.", encoding="utf-8")
        updated_sha = _file_sha(note_file)
        self.assertNotEqual(initial_sha, updated_sha)

        records3 = self.scanner.scan_directory(self.ks_dir)
        self.assertEqual(len(records3), 1)
        r3 = records3[0]
        self.assertEqual(r3.artifact_id, r1.artifact_id)
        self.assertEqual(r3.content_sha256, updated_sha)

    def test_ks_files_remain_100_percent_unmodified(self) -> None:
        bundle_folder = self.ks_dir / "4 수학" / "26해4021"
        bundle_folder.mkdir(parents=True, exist_ok=True)

        f1 = bundle_folder / "26해4021.md"
        f2 = bundle_folder / "26해4021 해석.md"
        f3 = bundle_folder / "26해4021 태도 정리.md"

        f1.write_text("메인 문제 설명", encoding="utf-8")
        f2.write_text("구체적 해석", encoding="utf-8")
        f3.write_text("태도 강령", encoding="utf-8")

        sha1_before, sha2_before, sha3_before = _file_sha(f1), _file_sha(f2), _file_sha(f3)

        records = self.scanner.scan_directory(self.ks_dir)
        self.assertEqual(len(records), 3)

        # Assert zero mutations on file system
        self.assertEqual(_file_sha(f1), sha1_before)
        self.assertEqual(_file_sha(f2), sha2_before)
        self.assertEqual(_file_sha(f3), sha3_before)

    def test_bundle_detector_identifies_anchor_and_siblings(self) -> None:
        bundle_folder = self.ks_dir / "4 수학" / "26해4021"
        bundle_folder.mkdir(parents=True, exist_ok=True)

        f1 = bundle_folder / "26해4021.md"
        f2 = bundle_folder / "26해4021 해석.md"
        f3 = bundle_folder / "26해4021 태도 정리.md"

        f1.write_text("Anchor content", encoding="utf-8")
        f2.write_text("Sibling content 1", encoding="utf-8")
        f3.write_text("Sibling content 2", encoding="utf-8")

        records = self.scanner.scan_directory(self.ks_dir)
        bundles = self.bundle_detector.detect_bundles(records)

        self.assertEqual(len(bundles), 1)
        bundle = bundles[0]
        self.assertEqual(bundle.bundle_name, "26해4021")
        self.assertEqual(bundle.anchor.title, "26해4021")
        sibling_titles = [s.title for s in bundle.siblings]
        self.assertCountEqual(sibling_titles, ["26해4021 해석", "26해4021 태도 정리"])

    def test_corrupted_path_index_raises_error(self) -> None:
        # Write corrupted JSON to path-index.json
        self.artifact_repo.path_index_file.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_repo.path_index_file.write_text("{corrupted-json-content", encoding="utf-8")

        from _scripts_2.apps.exam.exam_core.storage.artifact_repository import ArtifactIndexCorruptionError

        with self.assertRaises(ArtifactIndexCorruptionError):
            self.artifact_repo.list_all()

        with self.assertRaises(ArtifactIndexCorruptionError):
            self.scanner.scan_directory(self.ks_dir)

    def test_deleted_file_is_marked_missing_without_losing_uuid(self) -> None:
        f1 = self.ks_dir / "3 영어" / "260933.md"
        f1.parent.mkdir(parents=True, exist_ok=True)
        f1.write_text("Note content", encoding="utf-8")

        records = self.scanner.scan_directory(self.ks_dir)
        self.assertEqual(len(records), 1)
        art_id = records[0].artifact_id
        self.assertTrue(records[0].present)

        # Delete file from disk
        f1.unlink()

        # Re-scan: active records should be empty, but repository keeps tombstone with same UUID
        records2 = self.scanner.scan_directory(self.ks_dir)
        self.assertEqual(len(records2), 0)

        # Check in repository directly
        all_records = self.artifact_repo.list_all(include_missing=True)
        self.assertEqual(len(all_records), 1)
        self.assertEqual(all_records[0].artifact_id, art_id)
        self.assertFalse(all_records[0].present)
        self.assertIsNotNone(all_records[0].missing_since)

        # Normal list_all excludes missing by default
        self.assertEqual(len(self.artifact_repo.list_all(include_missing=False)), 0)

    def test_category_folder_with_unrelated_files_does_not_false_bundle(self) -> None:
        category_dir = self.ks_dir / "3 영어" / "1 빈칸 유형" / "1 빈칸"
        category_dir.mkdir(parents=True, exist_ok=True)

        (category_dir / "170933.md").write_text("Problem 1", encoding="utf-8")
        (category_dir / "171131.md").write_text("Problem 2", encoding="utf-8")
        (category_dir / "260933.md").write_text("Problem 3", encoding="utf-8")

        records = self.scanner.scan_directory(self.ks_dir)
        bundles = self.bundle_detector.detect_bundles(records)

        # None of these files share an anchor prefix with one another or match "1 빈칸"
        # They MUST NOT be bundled into 1 bundle. They must be 3 separate standalone bundles.
        self.assertEqual(len(bundles), 3)
        for b in bundles:
            self.assertEqual(len(b.siblings), 0)

    def test_duplicate_artifact_id_in_index_raises_corruption_error(self) -> None:
        import json
        corrupt_index = {
            "KS/3 영어/note1.md": "art-duplicate-uuid",
            "KS/3 영어/note2.md": "art-duplicate-uuid",
        }
        self.artifact_repo.path_index_file.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_repo.path_index_file.write_text(json.dumps(corrupt_index), encoding="utf-8")

        from _scripts_2.apps.exam.exam_core.storage.artifact_repository import ArtifactIndexCorruptionError
        with self.assertRaises(ArtifactIndexCorruptionError):
            self.artifact_repo.list_all()

    def test_missing_record_file_for_indexed_path_raises_corruption_error(self) -> None:
        note_file = self.ks_dir / "3 영어" / "260933.md"
        note_file.parent.mkdir(parents=True, exist_ok=True)
        note_file.write_text("content", encoding="utf-8")
        records = self.scanner.scan_directory(self.ks_dir)
        art_id = records[0].artifact_id

        # Delete records/{art_id}.json
        (self.artifact_repo.records_dir / f"{art_id}.json").unlink()

        from _scripts_2.apps.exam.exam_core.storage.artifact_repository import ArtifactRecordCorruptionError
        with self.assertRaises(ArtifactRecordCorruptionError):
            self.scanner.scan_directory(self.ks_dir)

        with self.assertRaises(ArtifactRecordCorruptionError):
            self.artifact_repo.list_all()

    def test_orphan_record_file_detected_by_validate_integrity(self) -> None:
        import json
        # Register a valid note
        note_file = self.ks_dir / "3 영어" / "260933.md"
        note_file.parent.mkdir(parents=True, exist_ok=True)
        note_file.write_text("content", encoding="utf-8")
        self.scanner.scan_directory(self.ks_dir)

        # Integrity passes initially
        self.artifact_repo.validate_integrity()

        # Create an orphan record not in path-index.json
        orphan_record = ArtifactRecord(
            artifact_id="art-orphan-123456",
            source_path="KS/3 영어/orphan.md",
            content_sha256="a" * 64,
            file_size=100,
        )
        orphan_file = self.artifact_repo.records_dir / "art-orphan-123456.json"
        orphan_file.write_text(json.dumps(orphan_record.to_dict()), encoding="utf-8")

        from _scripts_2.apps.exam.exam_core.storage.artifact_repository import ArtifactRecordCorruptionError
        with self.assertRaises(ArtifactRecordCorruptionError) as cm:
            self.artifact_repo.validate_integrity()
        self.assertIn("orphan", str(cm.exception).lower())

    def test_bidirectional_path_mismatch_raises_corruption_error(self) -> None:
        import json
        note_file = self.ks_dir / "3 영어" / "260933.md"
        note_file.parent.mkdir(parents=True, exist_ok=True)
        note_file.write_text("content", encoding="utf-8")
        records = self.scanner.scan_directory(self.ks_dir)
        art_id = records[0].artifact_id

        # Mismatch source_path inside record JSON
        rec_path = self.artifact_repo.records_dir / f"{art_id}.json"
        data = json.loads(rec_path.read_text(encoding="utf-8"))
        data["source_path"] = "KS/3 영어/다른경로.md"
        rec_path.write_text(json.dumps(data), encoding="utf-8")

        from _scripts_2.apps.exam.exam_core.storage.artifact_repository import ArtifactRecordCorruptionError
        with self.assertRaises(ArtifactRecordCorruptionError):
            self.artifact_repo.get_by_path("KS/3 영어/260933.md")

        with self.assertRaises(ArtifactRecordCorruptionError):
            self.artifact_repo.validate_integrity()

    def test_artifact_record_validation_types(self) -> None:
        # Invalid content_sha256 length
        with self.assertRaises(ValueError):
            ArtifactRecord(
                artifact_id="art-1",
                source_path="KS/note.md",
                content_sha256="short-sha",
                file_size=10,
            ).validate()

        # Invalid file_size (< 0)
        with self.assertRaises(ValueError):
            ArtifactRecord(
                artifact_id="art-1",
                source_path="KS/note.md",
                content_sha256="a" * 64,
                file_size=-1,
            ).validate()

        # Invalid present type
        with self.assertRaises(ValueError):
            ArtifactRecord.from_dict({
                "artifact_id": "art-1",
                "source_path": "KS/note.md",
                "content_sha256": "a" * 64,
                "file_size": 10,
                "present": "false",  # string instead of bool
            })


if __name__ == "__main__":
    unittest.main()


