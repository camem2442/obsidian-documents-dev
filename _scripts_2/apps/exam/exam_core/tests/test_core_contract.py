"""Standalone Core boundary and persisted contract acceptance."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from _scripts_2.apps.exam.exam_core.domain.canonical import CanonicalQuestionRecord
from _scripts_2.apps.exam.exam_core.domain.services.relation_service import RelationService
from _scripts_2.apps.exam.exam_core.indexing.origin_index import OriginIndex, OriginCollisionError
from _scripts_2.apps.exam.exam_core.storage.aggregate_views import build_question_aggregate
from _scripts_2.apps.exam.exam_core.storage.artifact_repository import ArtifactRepository
from _scripts_2.apps.exam.exam_core.storage.kice_master_repository import KiceMasterRepository
from _scripts_2.apps.exam.exam_core.storage.relation_repository import RelationRepository, RelationConcurrencyError

FIXTURE = Path(__file__).parent / 'fixtures/canonical-v0.3.json'


class CoreContractTests(unittest.TestCase):
    def record(self):
        return CanonicalQuestionRecord.from_dict(json.loads(FIXTURE.read_text()))

    def test_independent_import_without_processor_or_app_dependencies(self):
        root = Path(__file__).resolve().parents[5]
        code = '''
import importlib, importlib.abc, pkgutil, sys
sys.path.insert(0, sys.argv[1])
class BlockApps(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if ('exam_processor' in fullname or fullname.startswith(('fastapi', 'pydantic', '_scripts_2.ai_core', '_scripts_2.vault_paths'))):
            raise AssertionError('Core imported application dependency: ' + fullname)
sys.meta_path.insert(0, BlockApps())
import _scripts_2.apps.exam.exam_core as core
for module in pkgutil.walk_packages(core.__path__, core.__name__ + '.'):
    if '.tests' not in module.name:
        importlib.import_module(module.name)
assert not any('exam_processor' in name for name in sys.modules)
'''
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, '-I', '-c', code, str(root)], cwd=tmp, check=True)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_explicit_paths_required_and_construction_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'not-created'
            for repo in (KiceMasterRepository, RelationRepository, ArtifactRepository):
                with self.assertRaises(TypeError):
                    repo()
                with self.assertRaises(TypeError):
                    repo(None)
                repo(root)
            self.assertFalse(root.exists())

    def test_baseline_json_round_trip_preserves_all_fields(self):
        # Generated with the pre-cutover c2214b29 schema, not the new implementation.
        expected = json.loads(FIXTURE.read_text())
        record = self.record()
        self.assertEqual(record.to_dict(), expected)
        self.assertEqual(record.to_json() + '\n', FIXTURE.read_text())
        self.assertEqual(record.canonical_origin_key(), 'kice:2026:09:math:prob_stat::29')

    def test_relation_edit_does_not_mutate_approved_master_and_stale_write_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            masters, links = KiceMasterRepository(root), RelationRepository(root)
            record = self.record(); masters.save(record)
            path = masters.records_dir / (record.question.question_id + '.json')
            before = path.read_bytes()
            service = RelationService(links, masters)
            first = service.add_solution(record.question.question_id, 'solution-fixture')
            service.add_solution(record.question.question_id, 'solution-second', expected_revision=first.relation_revision)
            with self.assertRaises(RelationConcurrencyError):
                service.add_solution(record.question.question_id, 'solution-stale', expected_revision=first.relation_revision)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(masters.get(record.question.question_id).to_dict(), record.to_dict())

    def test_aggregate_read_only_and_index_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); masters, links = KiceMasterRepository(root), RelationRepository(root)
            record = self.record(); masters.save(record)
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
            view = build_question_aggregate(record.question.question_id, masters, links)
            self.assertEqual(view.master.to_dict(), record.to_dict())
            self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()})
            index = OriginIndex(); index.build_from_repository(masters)
            with self.assertRaises(OriginCollisionError):
                index.add(record.canonical_origin_key(), 'other-id')

    def test_master_rejects_embedded_solution(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = self.record(); record.solution = 'not master content'
            with self.assertRaises(ValueError):
                KiceMasterRepository(Path(tmp)).save(record)
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
