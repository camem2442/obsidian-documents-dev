"""UX projection compatibility and the observed pre-worker status window."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from _scripts_2.apps.exam.exam_processor.tests.batch_jobs_fixture import (
    create_fixture,
    JOB_IDS,
    fixture_processor,
)
from _scripts_2.apps.exam.exam_processor.storage.batch_jobs import BatchJobs
from _scripts_2.apps.exam.exam_processor.storage.sample_review import digest


class UXFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = create_fixture(self.root)
        self.svc = BatchJobs(self.store, fixture_processor)

    def manifest(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_display_is_readonly_and_not_saved_or_hashed(self):
        before = self.manifest()
        pre = self.svc.preview(JOB_IDS, 'audit')
        self.assertEqual(pre['display']['plan_hash'], pre['plan_hash'])
        self.assertEqual(pre['plan_hash'], digest(pre['plan']))
        self.assertEqual(pre['display']['items'][JOB_IDS[0]+'/q1']['number'], '1')
        self.assertEqual(before, self.manifest())
        with patch.object(self.svc, 'preview_display', return_value={'plan_hash': pre['plan_hash'], 'jobs': {}, 'items': {}}):
            fallback = self.svc.preview(JOB_IDS, 'audit')
            self.assertEqual(pre['plan'], fallback['plan'])
            self.assertEqual(pre['plan_hash'], fallback['plan_hash'])
            v = self.svc.act(dict(action='plan', job_ids=JOB_IDS, operation='audit', preview_hash=pre['plan_hash'], request_id=uuid.uuid4().hex))
        raw = json.loads(self.svc._path(v['id']).read_text())
        self.assertNotIn('display', raw)
        self.assertNotIn('display', raw['plan'])

    def test_history_is_not_transcription_and_combined_reason_is_unchanged(self):
        folder = self.store.job_dir(JOB_IDS[0])
        rec = self.store.records.get(folder, 'q1')
        rec.provenance.source_regions = []
        self.store.records.save(folder, rec)
        rev = self.store.reviews.get(folder, 'q1')
        rev.history = [{'note': 'manual edit'}]
        rev.extracted_revision = None
        self.store.reviews.save(folder, rev)
        before = self.manifest()
        pre = self.svc.preview([JOB_IDS[0]], 'extract')
        excluded = next(e for e in pre['plan']['excluded'] if e['item']=='q1')
        self.assertEqual(excluded['reasons'], ['missing_source_or_existing_transcription'])
        details = pre['display']['items'][JOB_IDS[0]+'/q1']['details']
        self.assertIn('본문 원본 영역 없음', details)
        self.assertIn('기존 편집/처리 이력으로 일괄 전사 제외', details)
        self.assertFalse(any('전사 revision' in d for d in details))
        self.assertEqual(before, self.manifest())

    def test_start_before_worker_is_projection_not_persisted_interruption(self):
        pre = self.svc.preview(JOB_IDS, 'audit')
        v = self.svc.act(dict(action='plan', job_ids=JOB_IDS, operation='audit', preview_hash=pre['plan_hash'], request_id=uuid.uuid4().hex))
        started = self.svc.act(dict(action='execute', batch_id=v['id'], revision=v['revision'], confirmed_plan_hash=v['plan_hash'], request_id=uuid.uuid4().hex))
        self.assertEqual(started['status'], 'interrupted')
        self.assertEqual(json.loads(self.svc._path(v['id']).read_text())['status'], 'running')
        self.assertEqual(started['counts']['uncertain'], 0)
        self.svc.run(v['id'])
        self.assertEqual(self.svc.get(v['id'])['counts']['completed'], 6)

    def run_until_dispatch_then_terminate(self, code, *args):
        import subprocess
        import sys
        import time
        marker = self.root / 'dispatched'
        child = subprocess.Popen([sys.executable, '-c', code, str(marker), *map(str, args)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(marker.exists(), 'worker must actually reach dispatched operation')
            self.assertIsNone(child.poll(), 'worker must still be live before termination')
            child.terminate()
            child.wait(timeout=5)
            self.assertNotEqual(child.returncode, 0)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def test_actual_batch_worker_loss_keeps_uncertainty_and_blocks_recall(self):
        from _scripts_2.apps.exam.exam_processor.storage.store import Store, Conflict
        pre = self.svc.preview(JOB_IDS, 'audit')
        v = self.svc.act(dict(action='plan', job_ids=JOB_IDS, operation='audit', preview_hash=pre['plan_hash'], request_id=uuid.uuid4().hex))
        self.svc.act(dict(action='execute', batch_id=v['id'], revision=v['revision'], confirmed_plan_hash=v['plan_hash'], request_id=uuid.uuid4().hex))
        self.run_until_dispatch_then_terminate('''
import sys,time
from pathlib import Path
from _scripts_2.apps.exam.exam_processor.storage.store import Store
from _scripts_2.apps.exam.exam_processor.storage.batch_jobs import BatchJobs
marker,root,bid=sys.argv[1:]
def dispatched(*args,**kwargs):
    Path(marker).write_text('called')
    time.sleep(60)
BatchJobs(Store(root),dispatched).run(bid)
''', self.root, v['id'])
        calls=[]
        service=BatchJobs(Store(self.root), lambda *a,**k: calls.append(a))
        lost=service.get(v['id'])
        self.assertFalse(lost['worker_live'])
        self.assertEqual(lost['counts']['uncertain'],1)
        service.run(v['id'])
        self.assertEqual(calls,[])
        recovered=service.act(dict(action='recover',batch_id=lost['id'],revision=lost['revision'],request_id=uuid.uuid4().hex))
        with self.assertRaises(Conflict):
            service.act(dict(action='resume',batch_id=recovered['id'],revision=recovered['revision'],request_id=uuid.uuid4().hex))

    def test_input_delayed_start_and_actual_worker_loss_are_distinct(self):
        from _scripts_2.apps.exam.exam_processor.storage.store import Store, Conflict
        from _scripts_2.apps.exam.exam_processor.storage.input_jobs import InputJobs
        root=self.root/'inputs'; source=self.root/'synthetic.md'
        source.write_text('### 1. Synthetic question?\n① One\n② Two\n')
        service=InputJobs(Store(root,self.root/'exports'))
        units=[dict(id='one',path=str(source),format='korean_notes',source='Synthetic worker observation',track='매체',included=True)]
        pre=service.preview(units)
        v=service.act(dict(action='plan',inputs=units,preview_hash=pre['plan_hash'],request_id=uuid.uuid4().hex))
        started=service.act(dict(action='execute',plan_id=v['id'],revision=v['revision'],confirmed_plan_hash=v['plan_hash'],request_id=uuid.uuid4().hex))
        self.assertEqual(started['status'],'interrupted')
        self.assertEqual(started['counts']['uncertain'],0)
        self.run_until_dispatch_then_terminate('''
import sys,time
from pathlib import Path
from _scripts_2.apps.exam.exam_processor.storage.store import Store
from _scripts_2.apps.exam.exam_processor.storage.input_jobs import InputJobs
marker,root,pid=sys.argv[1:]
service=InputJobs(Store(root))
def dispatched(*args):
    Path(marker).write_text('called')
    time.sleep(60)
service.create=dispatched
service.run(pid)
''', root,v['id'])
        service=InputJobs(Store(root,self.root/'exports'))
        lost=service.get(v['id'])
        self.assertFalse(lost['worker_live'])
        self.assertEqual(lost['counts']['uncertain'],1)
        recovered=service.act(dict(action='recover',plan_id=lost['id'],revision=lost['revision'],request_id=uuid.uuid4().hex))
        self.assertEqual(recovered['counts']['uncertain'],1)
        with self.assertRaises(Conflict):
            service.act(dict(action='resume',plan_id=recovered['id'],revision=recovered['revision'],request_id=uuid.uuid4().hex))
        self.assertEqual(service.store.list(),[])
