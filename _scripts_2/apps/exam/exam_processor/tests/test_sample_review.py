import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from _scripts_2.apps.exam.exam_processor.tests.sample_fixture import create_fixture, JOB_ID
from _scripts_2.apps.exam.exam_processor.storage.sample_review import SampleReview, select_sample
from _scripts_2.apps.exam.exam_processor.server import create_app


class SampleReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = create_fixture(self.root)
        self.folder = self.store.job_dir(JOB_ID)
        self.client = TestClient(create_app(self.store))
        self.headers = {'x-exam-token': self.client.get('/api/config').json()['token']}
        self.path = f'/api/jobs/{JOB_ID}/sample-review'

    def get(self):
        r = self.client.get(self.path)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def payload(self, action, **kw):
        view = self.get()
        return {'action': action, 'revision': view['revision'], 'basis_token': view['basis_token'],
                'session_id': view['active_session_id'], **kw}

    def post(self, payload, status=200):
        response = self.client.post(self.path, headers=self.headers, json=payload)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def start(self, count=2, seed='reproducible'):
        return self.post(self.payload('start', count=count, seed=seed))

    def hashes(self, runtime=True):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()
                and (runtime or p.name != 'runtime.json')}

    def test_read_only_and_invalid_counts_and_zero(self):
        before = self.hashes()
        self.assertEqual(self.get()['candidate_count'], 4)
        for count in (0, -1, 5, 1.2, True, '2', None):
            self.post(self.payload('start', count=count, seed='x'), 400)
        for seed in ('', ' ', None, 1, 'x'*129):
            self.post(self.payload('start', count=1, seed=seed), 400)
        self.assertEqual(before, self.hashes())
        manifest = json.loads((self.folder/'job.json').read_text())
        manifest['warnings'] = ['explicit zero candidate fixture']
        (self.folder/'job.json').write_text(json.dumps(manifest))
        self.assertEqual(self.get()['candidate_count'], 0)
        self.post(self.payload('start', count=1, seed='x'), 400)

    def test_reproducible_selection_restart_resume_history_and_runtime_retention(self):
        before = self.hashes(runtime=False)
        view = self.start(4)
        first = view['sessions'][0]
        self.assertEqual(len(set(first['selected'])), 4)
        self.post(self.payload('pause'))
        reloaded = SampleReview(type(self.store)(self.root, self.root/'exports')).get(JOB_ID)
        self.assertEqual(reloaded['sessions'][0]['status'], 'paused')
        self.post(self.payload('verdict', item_id=first['selected'][0], verdict='pass'), 409)
        self.post(self.payload('resume'))
        second = self.start(4)['sessions'][1]
        self.assertEqual(first['selected'], second['selected'])
        self.assertEqual(first['selected'], select_sample(list(reversed(first['basis']['candidates'])), 4, first['seed']))
        state = self.store.runtime.load(self.folder, JOB_ID)
        state.ai_progress = {'other': 'runtime writer'}
        self.store.runtime.save(self.folder, state)
        self.assertEqual(len(self.get()['sessions']), 2)
        self.assertEqual(self.store.runtime.load(self.folder).ai_log, [{'message':'preserve fixture runtime'}])
        self.assertEqual(before, self.hashes(runtime=False))

    def test_navigation_no_write_incomplete_and_all_pass_never_approves(self):
        before = self.hashes(runtime=False)
        original = self.store.get(JOB_ID)
        s = self.start()['sessions'][0]
        snapshot = self.hashes()
        self.get(); self.get()
        self.assertEqual(snapshot, self.hashes())
        view = self.post(self.payload('verdict', item_id=s['selected'][0], verdict='pass', note='checked source'))
        self.assertEqual(view['sessions'][0]['status'], 'incomplete')
        view = self.post(self.payload('verdict', item_id=s['selected'][1], verdict='pass'))
        self.assertEqual(view['sessions'][0]['status'], 'passed')
        self.assertEqual(view['job']['review_queue_summary'], original['review_queue_summary'])
        self.assertEqual(before, self.hashes(runtime=False))
        self.assertTrue(all(i['review']=='pending' for i in view['job']['items'] if i['kind']=='question'))

    def test_problem_reason_required_immutable_verdict_and_old_session_readonly(self):
        s = self.start()['sessions'][0]
        rid = s['selected'][0]
        snapshot = self.hashes()
        self.post(self.payload('verdict', item_id=rid, verdict='problem', note=' '), 400)
        self.assertEqual(snapshot, self.hashes())
        self.post(self.payload('verdict', item_id=rid, verdict='problem', note='  원본 차이 확인  '))
        self.post(self.payload('verdict', item_id=rid, verdict='pass'), 409)
        view = self.post(self.payload('verdict', item_id=s['selected'][1], verdict='pass'))
        self.assertEqual(view['sessions'][0]['status'], 'problem')
        self.assertEqual(view['sessions'][0]['results'][rid]['note'], '원본 차이 확인')
        old = copy.deepcopy(view['sessions'][0])
        self.start()
        self.post(self.payload('resume', session_id=s['id']), 409)
        self.assertEqual(self.get()['sessions'][0], old)

    def test_duplicate_and_concurrent_verdict_and_start(self):
        p = self.payload('start', count=2, seed='x')
        self.post(p); self.post(p, 409)
        s = self.get()['sessions'][0]
        p = self.payload('verdict', item_id=s['selected'][0], verdict='pass')
        def post(_):
            return self.client.post(self.path, headers=self.headers, json=p).status_code
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(post, range(2))), [200, 409])
        self.post(p, 409)
        self.assertEqual(len(self.get()['sessions'][0]['events']), 2)

    def test_save_failure_atomic_and_history_preserved(self):
        s = self.start()['sessions'][0]
        p = self.payload('verdict', item_id=s['selected'][0], verdict='problem', note='keep me')
        before = self.hashes()
        with patch('_scripts_2.apps.exam.exam_processor.storage.json_io.os.replace', side_effect=OSError('disk failure')):
            self.post(p, 500)
        self.assertEqual(before, self.hashes())
        self.post(p)
        self.assertEqual(self.get()['sessions'][0]['counts']['problem'], 1)

    def test_content_edit_stale_and_old_request_rejected(self):
        s = self.start()['sessions'][0]
        rid = s['selected'][0]
        p = self.payload('verdict', item_id=rid, verdict='pass')
        item = next(i for i in self.store.get(JOB_ID)['items'] if i['id']==rid)
        self.store.update(JOB_ID, rid, item['revision'], {'body':'edited source transcription'})
        before = self.hashes()
        self.post(p, 409)
        self.post(self.payload('verdict', item_id=rid, verdict='pass'), 409)
        view = self.get()
        self.assertEqual(view['sessions'][0]['status'], 'stale')
        self.assertTrue(any('내용 revision' in r for r in view['sessions'][0]['invalidation_reasons']))
        self.assertEqual(before, self.hashes())
        current = next(i for i in view['job']['items'] if i['id']==rid)
        self.assertEqual(current['review'], 'pending')
        self.assertFalse(current['approval_gate']['eligible'])

    def test_review_candidate_dependency_policy_and_source_invalidation(self):
        for change in ('review', 'candidate', 'dependency', 'policy', 'source', 'classification'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as root:
                store = create_fixture(root); svc = SampleReview(store); folder = store.job_dir(JOB_ID)
                view = svc.get(JOB_ID)
                p = {'action':'start','count':1,'seed':'x','revision':view['revision'],'basis_token':view['basis_token']}
                svc.act(JOB_ID,p)
                if change == 'review':
                    state = store.reviews.get(folder,'q2');state.note='changed';store.reviews.save(folder,state)
                elif change == 'candidate':
                    raw=json.loads((folder/'job.json').read_text());raw['record_ids'].remove('q2');(folder/'job.json').write_text(json.dumps(raw))
                elif change == 'dependency':
                    rec = store.records.get(folder,'p1');rec.body='different dependency';store.records.save(folder,rec)
                elif change == 'source':
                    (folder/'regions'/'fixture.png').write_bytes(b'changed bytes')
                elif change == 'classification':
                    state=store.reviews.get(folder,'q2');state.warnings=['new warning'];store.reviews.save(folder,state)
                if change == 'policy':
                    with patch('_scripts_2.apps.exam.exam_processor.storage.sample_review.POLICY_VERSION','green-sample/2'):
                        self.assertFalse(svc.get(JOB_ID)['sessions'][0]['valid'])
                else:
                    self.assertFalse(svc.get(JOB_ID)['sessions'][0]['valid'])

    def test_missing_source_blocks_pass_all_dependencies_shown(self):
        view=self.start(4);self.assertEqual(len(view['materials']['q1']['parts']),2)
        self.assertIn('alpha + beta', view['materials']['q1']['parts'][1]['preview']['body'])
        self.assertTrue(view['materials']['q1']['can_pass'])
        (self.folder/'regions'/'fixture.png').unlink()
        view=self.start(4)
        self.assertFalse(view['materials']['q1']['can_pass'])
        self.post(self.payload('verdict',item_id='q1',verdict='pass'),409)
        self.post(self.payload('verdict',item_id='q1',verdict='problem',note='원본 없음'))

    def test_legacy_readonly_no_migration(self):
        job=self.store.get(JOB_ID);job['storage_version']=1
        (self.folder/'job.json').write_text(json.dumps(job))
        before=self.hashes()
        view=self.get();self.assertFalse(view['available'])
        self.post(self.payload('start',count=1,seed='x'),409)
        self.assertEqual(before,self.hashes())

    def test_corrupt_runtime_fail_closed_and_embedded_runtime_preserved(self):
        path=self.folder/'runtime.json'
        for content in ('{broken', '{"sample_review": []}'):
            path.write_text(content)
            before=self.hashes()
            self.assertEqual(self.client.get(self.path).status_code,400)
            self.assertEqual(before,self.hashes())
        path.unlink()
        raw=json.loads((self.folder/'job.json').read_text());raw['ai_log']=[{'legacy':'retained'}]
        (self.folder/'job.json').write_text(json.dumps(raw))
        self.start()
        self.assertEqual(self.store.runtime.load(self.folder).ai_log,[{'legacy':'retained'}])

    def test_conflicting_verdicts_only_one_commits_and_completed_pass_invalidates(self):
        s = self.start(1)['sessions'][0]
        p = self.payload('verdict', item_id=s['selected'][0], verdict='pass')
        other = dict(p, verdict='problem', note='concurrent problem')
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda payload: self.client.post(self.path, headers=self.headers, json=payload), (p, other)))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        winner = next(r.json() for r in responses if r.status_code == 200)
        self.assertEqual(self.get()['sessions'], winner['sessions'])
        s = self.start(1)['sessions'][-1]
        self.post(self.payload('verdict', item_id=s['selected'][0], verdict='pass'))
        state = self.store.reviews.get(self.folder, 'q4')
        self.store.reviews.save(self.folder, state)  # revision alone, even no semantic change
        v = self.get()['sessions'][-1]
        self.assertEqual(v['status'], 'stale')
        self.assertEqual(v['outcome'], 'passed')  # historical outcome never current eligibility
        self.assertFalse(v['valid'])
