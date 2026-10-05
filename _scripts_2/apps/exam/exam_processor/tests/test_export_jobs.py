"""EX-N01 synthetic export contract, independent of real PDFs/providers."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from _scripts_2.apps.exam.exam_processor.storage.store import Store, Conflict
from _scripts_2.apps.exam.exam_processor.tests.batch_jobs_fixture import create_fixture, JOB_IDS
from _scripts_2.apps.exam.exam_processor.storage.export_jobs import ExportJobs


class ExportJobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = create_fixture(Path(self.tmp.name) / 'data')
        for jid in JOB_IDS:
            folder = self.store.job_dir(jid)
            for item in self.store.get(jid)['items']:
                rec = self.store.records.get(folder, item['id'])
                rev = self.store.reviews.get(folder, item['id'])
                rev.audit = dict(revision=rec.provenance.revision_id, status='completed', result='no_difference', issues=[])
                self.store.reviews.save(folder, rev)
                self.store.review(jid, item['id'], item['revision'], 'approve')
        self.exports = ExportJobs(self.store)

    def plan(self, ids=None):
        ids = ids or JOB_IDS
        preview = self.exports.preview(ids)
        return self.exports.plan(ids, preview['plan_hash'], uuid.uuid4().hex)

    def execute(self, plan):
        return self.exports.execute(plan['id'], plan['revision'], plan['plan_hash'])

    def test_preview_is_read_only_and_contains_revisions(self):
        before = {str(p): p.read_bytes() for p in self.store.root.rglob('*') if p.is_file()}
        result = self.exports.preview(JOB_IDS)
        self.assertEqual(len(result['plan']['entries']), 2)
        self.assertTrue(result['plan']['entries'][0]['items'])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.store.root.rglob('*') if p.is_file()})

    def test_partial_approval_excludes_whole_job(self):
        jid = JOB_IDS[0]
        item = self.store.get(jid)['items'][0]
        self.store.update(jid, item['id'], item['revision'], {'body': item['body'] + ' changed'})
        result = self.exports.preview(JOB_IDS)
        self.assertEqual(len(result['plan']['entries']), 1)
        self.assertIn('승인', result['plan']['excluded'][0]['reason'])

    def test_plan_replay_and_request_reuse_conflict(self):
        p = self.exports.preview(JOB_IDS)
        key = uuid.uuid4().hex
        first = self.exports.plan(JOB_IDS, p['plan_hash'], key)
        self.assertEqual(first, self.exports.plan(JOB_IDS, p['plan_hash'], key))
        with self.assertRaises(Conflict):
            self.exports.plan([JOB_IDS[0]], p['plan_hash'], key)

    def test_stale_plan_rejected_before_any_export(self):
        p = self.plan()
        jid = JOB_IDS[1]
        item = self.store.get(jid)['items'][0]
        self.store.update(jid, item['id'], item['revision'], {'body': 'Changed synthetic body'})
        with self.assertRaises(Conflict):
            self.execute(p)
        self.assertFalse(self.store.output.exists())

    def test_wrong_confirmation_rejected(self):
        p = self.plan()
        with self.assertRaises(Conflict):
            self.exports.execute(p['id'], p['revision'], 'wrong')
        self.assertEqual(self.exports.get(p['id'])['status'], 'planned')

    def test_completed_output_survives_restart_and_duplicate_execute(self):
        p = self.plan()
        result = self.execute(p)
        self.assertEqual([x['status'] for x in result['entries']], ['completed', 'completed'])
        before = {str(p): p.read_bytes() for p in self.store.output.rglob('*') if p.is_file()}
        self.assertEqual(self.execute(p)['id'], p['id'])
        recovered = ExportJobs(Store(self.store.root, self.store.output)).recover(p['id'])
        self.assertEqual([x['status'] for x in recovered['entries']], ['completed', 'completed'])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.store.output.rglob('*') if p.is_file()})
        self.assertFalse(self.exports.preview(JOB_IDS)['plan']['entries'])

    def test_partial_failure_preserves_success(self):
        p = self.plan()
        real = self.store.export
        def export(jid, *args, **kwargs):
            if jid == JOB_IDS[1]:
                raise OSError('synthetic output failure')
            return real(jid, *args, **kwargs)
        with patch.object(self.store, 'export', side_effect=export):
            result = self.execute(p)
        self.assertEqual([x['status'] for x in result['entries']], ['completed', 'failed'])
        self.assertTrue(Path(result['entries'][0]['output']).exists())

    def test_output_after_receipt_gap_is_recovered_without_reexport(self):
        p = self.plan([JOB_IDS[0]])
        real = self.store.export
        def export(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError('synthetic interruption after publish')
        with patch.object(self.store, 'export', side_effect=export):
            result = self.execute(p)
        self.assertEqual(result['entries'][0]['status'], 'uncertain')
        with patch.object(self.store, 'export', side_effect=AssertionError('must not reexport')):
            recovered = self.exports.recover(p['id'])
        self.assertEqual(recovered['entries'][0]['status'], 'recovered')

    def test_edited_output_stays_uncertain_and_preserved(self):
        p = self.plan([JOB_IDS[0]])
        result = self.execute(p)
        output = Path(result['entries'][0]['output'])
        note = next(output.rglob('*.md'))
        note.write_text('USER EDIT')
        recovered = self.exports.recover(p['id'])
        self.assertEqual(recovered['entries'][0]['status'], 'uncertain')
        self.assertEqual(note.read_text(), 'USER EDIT')

    def test_no_automatic_reexecution_of_uncertain(self):
        p = self.plan([JOB_IDS[0]])
        value = self.exports._read(p['id'])
        value['status'] = 'running'
        value['entries'][0]['status'] = 'running'
        self.exports._save(value)
        result = self.exports.recover(p['id'])
        self.assertEqual(result['entries'][0]['status'], 'uncertain')
        with patch.object(self.store, 'export', side_effect=AssertionError('must not run')):
            self.exports.execute(p['id'], p['revision'], p['plan_hash'])

    def test_manifest_corruption_fail_closed(self):
        p = self.plan()
        path = self.exports._path(p['id'])
        value = json.loads(path.read_text())
        value['plan']['destination'] = '/different'
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            self.exports.get(p['id'])

    def test_asset_drift_invalidates_plan(self):
        p = self.plan()
        (self.store.job_dir(JOB_IDS[0]) / 'regions/fixture.png').write_bytes(b'changed')
        with self.assertRaises(Conflict):
            self.execute(p)

    def test_destination_symlink_rejected(self):
        target = Path(self.tmp.name) / 'actual'
        target.mkdir()
        link = Path(self.tmp.name) / 'link'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.exports.preview(JOB_IDS, str(link))

    def test_missing_current_audit_excluded(self):
        jid = JOB_IDS[0]
        folder = self.store.job_dir(jid)
        rev = self.store.reviews.get(folder, 'q1')
        rev.audit = None
        self.store.reviews.save(folder, rev)
        result = self.exports.preview([jid])
        self.assertFalse(result['plan']['entries'])
        self.assertTrue(result['plan']['excluded'])

    def test_other_plan_cannot_repeat_completed_basis(self):
        first, second = self.plan(), self.plan()
        self.execute(first)
        with self.assertRaises(Conflict):
            self.execute(second)

    def test_corrupted_payload_manifest_is_uncertain(self):
        p = self.plan([JOB_IDS[0]])
        result = self.execute(p)
        manifest = Path(result['entries'][0]['output']) / 'manifest.json'
        manifest.write_text('{broken')
        self.assertEqual(self.exports.recover(p['id'])['entries'][0]['status'], 'uncertain')

    def test_output_path_collision_preserves_existing_file(self):
        p = self.plan()
        target = self.store.output / ('exam-export-' + p['id'])
        target.mkdir(parents=True)
        (target / 'keep.txt').write_text('USER FILE')
        with self.assertRaises(Conflict):
            self.execute(p)
        self.assertEqual((target / 'keep.txt').read_text(), 'USER FILE')

    def test_api_confirmation_token_and_restart_observation(self):
        from fastapi.testclient import TestClient
        from _scripts_2.apps.exam.exam_processor.server import create_app
        client = TestClient(create_app(self.store))
        self.assertEqual(client.post('/api/export-plans/preview', json={'job_ids': JOB_IDS}).status_code, 403)
        headers = {'x-exam-token': client.get('/api/config').json()['token']}
        preview = client.post('/api/export-plans/preview', json={'job_ids': JOB_IDS}, headers=headers).json()
        request = dict(action='plan', job_ids=JOB_IDS, preview_hash=preview['plan_hash'], request_id=uuid.uuid4().hex)
        response = client.post('/api/export-plans', json=request, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        p = response.json()
        bad = dict(action='execute', plan_id=p['id'], revision=p['revision'], confirmed_plan_hash='wrong')
        self.assertEqual(client.post('/api/export-plans', json=bad, headers=headers).status_code, 409)
        bad['confirmed_plan_hash'] = p['plan_hash']
        response = client.post('/api/export-plans', json=bad, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'finished')
        restarted = TestClient(create_app(Store(self.store.root, self.store.output)))
        self.assertEqual(restarted.get('/api/export-plans/' + p['id']).json()['status'], 'finished')
        self.assertEqual(restarted.post('/api/export-plans', json=bad, headers=headers).status_code, 403)

    def test_process_restart_recovers_persisted_output_without_reexport(self):
        import os
        import subprocess
        import sys
        p = self.plan([JOB_IDS[0]])
        self.execute(p)
        script = """import sys,json
from _scripts_2.apps.exam.exam_processor.storage.store import Store
from _scripts_2.apps.exam.exam_processor.storage.export_jobs import ExportJobs
s=Store(sys.argv[1],sys.argv[2]); e=ExportJobs(s)
s.export=lambda *a,**k: (_ for _ in ()).throw(AssertionError('must not export'))
print(json.dumps(e.recover(sys.argv[3])))
"""
        result = subprocess.run([sys.executable, '-c', script, str(self.store.root), str(self.store.output), p['id']],
                                capture_output=True, text=True, check=True, env=os.environ.copy(), timeout=30)
        self.assertEqual(json.loads(result.stdout)['entries'][0]['status'], 'completed')

    def test_malformed_job_selection_rejected(self):
        for value in (None, [], [JOB_IDS[0], JOB_IDS[0]], [{}], [12]):
            with self.assertRaises(ValueError):
                self.exports.preview(value)

    def test_unsafe_export_context_excluded(self):
        folder = self.store.job_dir(JOB_IDS[0])
        rec = self.store.records.get(folder, 'q1')
        rec.question.question_set_id = '../../outside'
        self.store.records.save(folder, rec)
        self.assertFalse(self.exports.preview([JOB_IDS[0]])['plan']['entries'])

    def test_input_batch_review_export_pipeline(self):
        from _scripts_2.apps.exam.exam_processor.storage.input_jobs import InputJobs
        from _scripts_2.apps.exam.exam_processor.storage.batch_jobs import BatchJobs
        from _scripts_2.apps.exam.exam_processor.tests.input_jobs_fixture import source_pdf, processor
        from _scripts_2.apps.exam.exam_processor.storage.export_evidence import file_hash
        root = Path(self.tmp.name) / 'pipeline'
        root.mkdir()
        source = root / 'synthetic.pdf'
        source_pdf(source)
        source_hash = file_hash(source)
        store = Store(root / 'data', root / 'output')
        inputs = InputJobs(store)
        units = [dict(id='synthetic', path=str(source), format='kice_korean',
                      source='EX-N01 synthetic source', track='화법과 작문', pages='1')]
        pre = inputs.preview(units)
        inp = inputs.act(dict(action='plan', request_id=uuid.uuid4().hex, inputs=units, preview_hash=pre['plan_hash']))
        inputs.act(dict(action='execute', request_id=uuid.uuid4().hex, plan_id=inp['id'], revision=inp['revision'], confirmed_plan_hash=inp['plan_hash']))
        inputs.run(inp['id'])
        jid = inputs.get(inp['id'])['entries'][0]['job_id']
        batches = BatchJobs(store, processor)
        pre = batches.preview([jid], 'audit')
        batch = batches.act(dict(action='plan', request_id=uuid.uuid4().hex, job_ids=[jid], operation='audit', preview_hash=pre['plan_hash']))
        batches.act(dict(action='execute', request_id=uuid.uuid4().hex, batch_id=batch['id'], revision=batch['revision'], confirmed_plan_hash=batch['plan_hash']))
        batches.run(batch['id'])
        exports = ExportJobs(store)
        self.assertFalse(exports.preview([jid])['plan']['entries'])
        for item in store.get(jid)['items']:
            store.review(jid, item['id'], item['revision'], 'approve')
        pre = exports.preview([jid])
        plan = exports.plan([jid], pre['plan_hash'], uuid.uuid4().hex)
        result = exports.execute(plan['id'], plan['revision'], plan['plan_hash'])
        self.assertEqual(result['entries'][0]['status'], 'completed')
        self.assertEqual(file_hash(source), source_hash)
        self.assertEqual(ExportJobs(Store(store.root, store.output)).recover(plan['id'])['entries'][0]['status'], 'completed')
        self.assertFalse((store.root / 'library/kice/records').exists())

    def test_image_bytes_links_and_originals_preserved(self):
        from PIL import Image
        from _scripts_2.apps.exam.exam_processor.storage.export_evidence import file_hash
        jid = JOB_IDS[0]
        folder = self.store.job_dir(jid)
        source = folder / 'assets' / 'synthetic.png'
        source.parent.mkdir()
        Image.new('RGB', (8, 8), 'red').save(source)
        job = self.store.get(jid)
        job['items'][0]['body'] += '\n![[assets/synthetic.png]]'
        job['items'][0]['assets'] = [dict(id='synthetic', path='assets/synthetic.png', section='body')]
        self.store.save(job)
        before = file_hash(source)
        plan = self.plan([jid])
        result = self.execute(plan)
        self.assertEqual(result['entries'][0]['status'], 'completed', result['entries'][0])
        output = Path(result['entries'][0]['output'])
        self.assertTrue(any(file_hash(p) == before for p in output.rglob('*.png')))
        self.assertEqual(file_hash(source), before)
