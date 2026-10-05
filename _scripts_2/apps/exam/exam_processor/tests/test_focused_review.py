import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from _scripts_2.apps.exam.exam_processor.tests.focused_fixture import create_fixture, JOB_ID
from _scripts_2.apps.exam.exam_processor.server import create_app


class FocusedReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = create_fixture(self.root)
        self.client = TestClient(create_app(self.store))
        self.headers = {'x-exam-token': self.client.get('/api/config').json()['token']}

    def path(self, name):
        return f'/api/jobs/{JOB_ID}/items/{name}/focused-review'

    def view(self, name):
        return self.client.get(self.path(name)).json()

    def payload(self, name, action, **kwargs):
        v = self.view(name)
        return {'revision': v['revision'], 'review_revision': v['review_revision'],
                'token': v['cards'][0]['token'], 'action': action, **kwargs}

    def post(self, name, payload):
        return self.client.post(self.path(name), headers=self.headers, json=payload)

    def hashes(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def item(self, name, response):
        return next(i for i in response.json()['items'] if i['id'] == name)

    def test_read_only_cards_and_real_classifier(self):
        before = self.hashes()
        for name in ('apply', 'skip', 'edit', 'warning', 'waiver', 'ambiguous', 'stale', 'multi'):
            view = self.view(name)
            self.assertEqual(view['tier'], 'YELLOW', name)
            self.assertTrue(view['eligible'])
        self.assertTrue(self.view('apply')['cards'][0]['can_apply'])
        self.assertIn('alphx', self.view('apply')['preview']['body'])
        self.assertEqual(self.hashes(), before)

    def test_apply_revision_snapshot_invalidation_and_duplicate(self):
        payload = self.payload('apply', 'apply')
        response = self.post('apply', payload)
        self.assertEqual(response.status_code, 200, response.text)
        item = self.item('apply', response)
        self.assertEqual(item['body'], 'alpha + beta = gamma.')
        self.assertNotEqual(item['revision'], payload['revision'])
        self.assertEqual(item['review'], 'pending')
        self.assertEqual(item['review_decision']['tier'], 'NOT_READY')
        self.assertFalse(item['approval_gate']['eligible'])
        self.assertTrue(item['audit']['issues'][0]['applied'])
        state = self.store.reviews.get(self.store.job_dir(JOB_ID), 'apply')
        self.assertFalse(state.history[-1]['audit']['issues'][0].get('applied', False))
        self.assertEqual(item['audit']['revision'], payload['revision'])
        self.assertEqual(response.json()['review_queue_summary']['pending']['YELLOW'], 7)
        before = self.hashes()
        self.assertEqual(self.post('apply', payload).status_code, 409)
        self.assertEqual(self.post('apply', self.payload('apply', 'apply')).status_code, 409)
        self.assertEqual(self.hashes(), before)

    def test_skip_requires_reason_preserves_history_and_no_approval(self):
        payload = self.payload('skip', 'skip', note='  원본과 비교한 판단 근거  ')
        empty = dict(payload, note=' ')
        before = self.hashes()
        self.assertEqual(self.post('skip', empty).status_code, 400)
        self.assertEqual(self.hashes(), before)
        response = self.post('skip', payload)
        self.assertEqual(response.status_code, 200)
        item = self.item('skip', response)
        self.assertEqual(item['revision'], payload['revision'])
        self.assertEqual(item['review'], 'pending')
        self.assertEqual(item['audit']['issues'][0]['skip_note'], '원본과 비교한 판단 근거')
        self.assertEqual(item['ai_runs'][-1]['run_type'], 'audit_skip_decision')
        decisions = json.loads(self.store.audit_skip_decisions_path().read_text())
        self.assertEqual(decisions['decisions'][0]['note'], '원본과 비교한 판단 근거')
        self.assertEqual(self.view('skip')['cards'][0]['status'], 'skipped')
        self.assertEqual(self.post('skip', payload).status_code, 409)
        self.assertFalse((self.root/'learning'/'human-findings.json').exists())

    def test_direct_edit_uses_standard_metadata_and_invalidates(self):
        response = self.post('edit', self.payload('edit', 'edit', text='alpha + beta = gamma.\n\n직접 확인 $x^2$'))
        self.assertEqual(response.status_code, 200, response.text)
        item = self.item('edit', response)
        self.assertIn('직접 확인', item['standard_preview']['body'])
        self.assertEqual(item['review_decision']['tier'], 'NOT_READY')
        self.assertEqual(item['review'], 'pending')

    def test_stale_audit_and_ambiguous_target_fail_closed(self):
        for name in ('stale', 'ambiguous'):
            v = self.view(name)
            self.assertFalse(v['cards'][0]['can_apply'])
            self.assertFalse(v['cards'][0]['can_edit'])
            before = self.hashes()
            self.assertEqual(self.post(name, self.payload(name, 'apply')).status_code, 409)
            self.assertEqual(self.hashes(), before)
        self.assertFalse(self.view('stale')['cards'][0]['can_skip'])

    def test_missing_or_multiple_source_regions_and_inconsistent_section(self):
        folder = self.store.job_dir(JOB_ID)
        for mode in ('missing', 'multiple', 'wrong-section', 'overlap'):
            record = self.store.records.get(folder, 'apply')
            review = self.store.reviews.get(folder, 'apply')
            if mode == 'missing':
                record.provenance.source_regions[0]['image'] = 'missing.png'
            elif mode == 'multiple':
                record.provenance.source_regions[0]['image'] = 'fixture.png'
                record.provenance.source_regions.append(copy.deepcopy(record.provenance.source_regions[0]))
            elif mode == 'wrong-section':
                record.provenance.source_regions = record.provenance.source_regions[:1]
                review.audit['issues'][0]['section'] = 'solution'
            else:
                record.body = 'aaa'
                review.audit['issues'][0].pop('section', None)
                review.audit['issues'][0]['extracted'] = 'aa'
            self.store.records.save(folder, record)
            self.store.reviews.save(folder, review)
            self.assertFalse(self.view('apply')['cards'][0]['can_apply'], mode)

    def test_warning_waiver_without_invented_suggestions(self):
        for name in ('warning', 'waiver'):
            card = self.view(name)['cards'][0]
            self.assertEqual(card['kind'], name)
            self.assertNotIn('suggestion', card)
            self.assertFalse(card.get('can_apply'))
            self.assertTrue(card['blocked'])

    def test_review_only_conflict_and_same_revision_replaced_audit(self):
        payload = self.payload('apply', 'apply')
        folder = self.store.job_dir(JOB_ID)
        self.store._review_svc.update_note(folder, 'apply', '동시 검수 메모')
        self.assertEqual(self.post('apply', payload).status_code, 409)
        payload = self.payload('apply', 'apply')
        state = self.store.reviews.get(folder, 'apply')
        state.audit['issues'][0]['suggestion'] = 'OTHER'
        self.store.reviews.save(folder, state)
        payload['review_revision'] = self.view('apply')['review_revision']
        self.assertEqual(self.post('apply', payload).status_code, 409)

    def test_content_conflict_and_concurrent_duplicate(self):
        payload = self.payload('apply', 'apply')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.post('apply', payload).status_code, range(2)))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(len(self.store.get(JOB_ID)['items'][0]['history']), 1)

    def test_failed_record_review_and_skip_ledger_writes_restore_all_bytes(self):
        for name, owner, method, action in (
                ('apply', self.store.records, 'save', 'apply'),
                ('apply', self.store.reviews, 'save', 'apply'),
                ('skip', self.store, '_record_audit_skip_decision', 'skip')):
            before = self.hashes()
            payload = self.payload(name, action, note='실패 시 보존 검증')
            with patch.object(owner, method, side_effect=OSError('injected write failure')):
                response = self.post(name, payload)
            self.assertEqual(response.status_code, 500, response.text)
            self.assertEqual(self.hashes(), before)
            self.assertTrue(self.view(name)['cards'][0]['can_apply'])
        self.assertEqual(self.post('apply', self.payload('apply', 'apply')).status_code, 200)

    def test_legacy_read_only_and_auth(self):
        folder = self.store.job_dir(JOB_ID)
        job = self.store.get(JOB_ID)
        job.pop('storage_version')
        (folder/'job.json').write_text(json.dumps(job))
        before = self.hashes()
        view = self.view('apply')
        self.assertFalse(view['cards'][0]['can_apply'])
        self.assertFalse(view['cards'][0]['can_skip'])
        self.assertEqual(self.client.post(self.path('apply'), json={}).status_code, 403)
        self.assertEqual(self.hashes(), before)
