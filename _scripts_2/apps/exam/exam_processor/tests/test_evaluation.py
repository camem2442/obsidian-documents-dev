import copy
import json
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from _scripts_2.apps.exam.exam_processor.tests.evaluation_fixture import create_fixture, JOB_ID
from _scripts_2.apps.exam.exam_processor.storage.evaluation import Evaluation, SCOPES
from _scripts_2.apps.exam.exam_processor.storage.sample_review import SampleReview
from _scripts_2.apps.exam.exam_processor.storage.bulk_approval import BulkApproval
from _scripts_2.apps.exam.exam_processor.storage.store import Store, Conflict
from _scripts_2.apps.exam.exam_processor.server import create_app

MOD='_scripts_2.apps.exam.exam_processor.storage.evaluation'

class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=create_fixture(self.root);self.svc=Evaluation(self.store)
        self.folder=self.store.job_dir(JOB_ID)

    def view(self): return self.svc.get(JOB_ID)
    def payload(self,action,**kw):
        v=self.view()
        return dict(action=action,revision=v['revision'],basis_token=v['basis_token'],request_id=uuid.uuid4().hex,**kw)
    def act(self,action,**kw): return self.svc.act(JOB_ID,self.payload(action,**kw))
    def label(self,**kw):
        return self.act('label',**dict(dict(item_id='q1',target='question',verdict='error_found',scope=list(SCOPES),note='SYNTHETIC observation',supersedes=None),**kw))
    def dataset(self,**kw):
        return self.act('dataset',selection=dict(dict(origin='synthetic_fixture',tiers=['GREEN','YELLOW','RED','NOT_READY'],targets=['question','issue'],full_scope_only=True),**kw))
    def hashes(self,runtime=True):
        return {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file() and (runtime or p.name!='runtime.json')}
    def issue(self): return next(i for i in self.view()['items'] if i['id']=='q2')['issues'][0]['token']

    def test_empty_readonly_missing_time_and_dataset(self):
        before=self.hashes();v=self.view();self.view()
        self.assertEqual(before,self.hashes());self.assertEqual(v['summary']['recorded'],0)
        self.assertEqual(v['summary']['unjudged_questions'],4);self.assertEqual(v['summary']['unjudged_issues'],1)
        self.assertIsNone(v['summary']['measured_seconds'])
        self.assertEqual(v['metrics'][1]['question_green_miss']['denominator'],0)
        d=self.dataset()['datasets'][0];self.assertEqual(d['included'],[])

    def test_explicit_scope_reason_target_independence(self):
        for kw in ({'note':''},{'scope':[]},{'scope':['all']},{'verdict':'pass'},{'scope':['body','body']}):
            with self.assertRaises(ValueError):self.label(**kw)
        v=self.label(target='issue',item_id='q2',issue_token=self.issue(),verdict='false_positive',scope=['body'])
        self.assertEqual(v['summary']['unjudged_questions'],4)
        v=self.label(item_id='q2',verdict='no_error_in_scope',scope=['body'])
        self.assertEqual(v['summary']['recorded'],2)
        self.assertEqual(v['metrics'][1]['question_comparable'],0)
        self.assertEqual(v['metrics'][1]['issue_false_positive'],dict(numerator=1,denominator=1,meaning='명시 판정된 비교 가능 AI 지적 중 오탐 비율; 전체 FPR 아님'))
        v=self.label(item_id='q3',verdict='deferred')
        self.assertEqual(v['summary']['deferred'],1)
        self.assertEqual(v['summary']['unjudged_questions'],2)
        self.assertEqual(self.dataset()['datasets'][0]['included'],[])

    def test_correction_history_and_no_content_or_approval_mutation(self):
        before=self.hashes(False);queue=self.store.get(JOB_ID)['review_queue_summary']
        first=self.label()['labels'][0]
        with self.assertRaises(Conflict):self.label(verdict='no_error_in_scope')
        with self.assertRaises(ValueError):self.label(supersedes=first['id'],verdict='no_error_in_scope')
        v=self.label(supersedes=first['id'],correction_reason='Synthetic correction',verdict='no_error_in_scope')
        self.assertEqual(v['labels'][0]['note'],first['note']);self.assertIn('superseded',v['labels'][0]['exclusions'])
        self.assertEqual(v['labels'][1]['supersedes'],first['id']);self.assertEqual(v['summary']['current'],1)
        self.assertEqual(before,self.hashes(False));self.assertEqual(queue,self.store.get(JOB_ID)['review_queue_summary'])

    def test_duplicate_conflict_and_concurrent_stores(self):
        p=self.payload('label',item_id='q1',target='question',verdict='deferred',scope=['body'],note='synthetic',supersedes=None)
        self.svc.act(JOB_ID,p);self.svc.act(JOB_ID,p)
        self.assertEqual(len(self.view()['labels']),1)
        with self.assertRaises(Conflict):self.svc.act(JOB_ID,dict(p,note='changed request'))
        p=self.payload('label',item_id='q3',target='question',verdict='deferred',scope=['body'],note='synthetic',supersedes=None)
        other=Evaluation(Store(self.root,self.root/'exports'))
        def run(svc):
            try:svc.act(JOB_ID,dict(p,request_id=uuid.uuid4().hex));return 'ok'
            except Conflict:return 'conflict'
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sorted(pool.map(run,[self.svc,other])),['conflict','ok'])
        self.assertEqual(len(self.view()['labels']),2)

    def test_save_failure_atomic_and_retry_input_preserved(self):
        p=self.payload('label',item_id='q1',target='question',verdict='error_found',scope=['body'],note='keep exact input',supersedes=None)
        before=self.hashes()
        with patch('_scripts_2.apps.exam.exam_processor.storage.json_io.os.replace',side_effect=OSError('fixture')):
            with self.assertRaises(OSError):self.svc.act(JOB_ID,p)
        self.assertEqual(before,self.hashes());self.svc.act(JOB_ID,p)
        self.assertEqual(self.view()['labels'][0]['note'],'keep exact input')

    def test_invalidation_matrix_and_stale_request(self):
        for change in ('content','review','dependency','classification','source','asset','policy','classifier','manifest'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as root:
                store=create_fixture(root);svc=Evaluation(store);folder=store.job_dir(JOB_ID)
                if change=='asset':
                    from _scripts_2.apps.exam.exam_core.domain.canonical.schema import AssetDomain
                    (folder/'assets').mkdir(exist_ok=True);(folder/'assets/figure.png').write_bytes((folder/'regions/fixture.png').read_bytes())
                    r=store.records.get(folder,'q1');r.assets.append(AssetDomain('figure','body','regions/fixture.png','assets/figure.png','image/png'));store.records.save(folder,r)
                v=svc.get(JOB_ID);p=dict(action='label',revision=v['revision'],basis_token=v['basis_token'],request_id=uuid.uuid4().hex,item_id='q1',target='question',verdict='error_found',scope=list(SCOPES),note='synthetic',supersedes=None)
                svc.act(JOB_ID,p)
                stale=dict(p,request_id=uuid.uuid4().hex,revision=svc.get(JOB_ID)['revision'],item_id='q3')
                if change in ('content','dependency'):
                    r=store.records.get(folder,'q1' if change=='content' else 'p1');r.body+='changed';store.records.save(folder,r)
                elif change in ('review','classification'):
                    r=store.reviews.get(folder,'q1');r.note='changed';r.warnings=['new'] if change=='classification' else [];store.reviews.save(folder,r)
                elif change=='source':(folder/'regions/fixture.png').write_bytes(b'new')
                elif change=='asset':
                    (folder/'assets/figure.png').write_bytes(b'changed synthetic asset bytes')
                elif change=='manifest':
                    data=json.loads((folder/'job.json').read_text());data['source']='changed';(folder/'job.json').write_text(json.dumps(data))
                target=MOD+'.POLICY' if change=='policy' else '_scripts_2.apps.exam.exam_processor.storage.sample_review.CLASSIFIER_VERSION'
                from contextlib import nullcontext
                with (patch(target,'changed') if change in ('policy','classifier') else nullcontext()):
                    self.assertFalse(svc.get(JOB_ID)['labels'][0]['current'])
                    with self.assertRaises(Conflict):svc.act(JOB_ID,stale)

    def test_immutable_dataset_reproducibility_and_historical_source(self):
        self.label();v=self.dataset();d1=self.svc.dataset(JOB_ID,v['datasets'][0]['id'])
        d2=self.svc.dataset(JOB_ID,self.dataset()['datasets'][-1]['id'])
        self.assertEqual(d1['composition_hash'],d2['composition_hash']);self.assertNotEqual(d1['id'],d2['id'])
        self.assertEqual(len(d1['included']),1)
        self.assertTrue(d1['evidence'][next(iter(d1['evidence']))]['source_blobs_base64'])
        r=self.store.records.get(self.folder,'q1');r.body='new current text';self.store.records.save(self.folder,r)
        self.assertEqual(d1,self.svc.dataset(JOB_ID,d1['id']))
        d3=self.dataset()['datasets'][-1];self.assertEqual(d3['included'],[]);self.assertIn('basis_changed',d3['excluded'][0]['reasons'])
        self.assertEqual(len(self.view()['datasets']),3)

    def test_missing_source_or_tampered_snapshot_excluded(self):
        (self.folder/'regions/fixture.png').unlink()
        self.label();self.assertIn('missing_source_bytes',self.view()['labels'][0]['exclusions'])
        self.assertEqual(self.dataset()['datasets'][-1]['included'],[])
        r=self.store.runtime.load(self.folder);e=next(iter(r.evaluation['evidence'].values()));e['records']['q1']['body']='tamper';self.store.runtime.save(self.folder,r)
        self.assertIn('evidence_integrity',self.view()['labels'][0]['exclusions'])
        self.assertIn('record_snapshot_mismatch',self.view()['labels'][0]['exclusions'])

    def test_metrics_fixed_fixture_and_origin_separation(self):
        self.label() # GREEN error, full scope
        self.label(item_id='q2',verdict='error_found') # YELLOW error
        self.label(item_id='q2',target='issue',issue_token=self.issue(),verdict='false_positive',scope=['body'])
        self.label(item_id='q3',verdict='no_error_in_scope',scope=['body'])
        self.label(item_id='q4',verdict='deferred')
        human,synthetic=self.view()['metrics']
        self.assertEqual(human['question_green_miss']['denominator'],0)
        self.assertEqual((synthetic['question_green_miss']['numerator'],synthetic['question_green_miss']['denominator']),(1,2))
        self.assertEqual((synthetic['issue_false_positive']['numerator'],synthetic['issue_false_positive']['denominator']),(1,1))
        self.assertEqual(len(synthetic['question_excluded']),2)
        self.assertEqual(len(self.view()['green_errors']),1)
        issue_label=next(x for x in self.view()['labels'] if x['target']=='issue')
        for tier in (None,'NOT_READY'):
            metric=Evaluation._metrics([dict(issue_label,tier=tier)],'synthetic_fixture')
            self.assertEqual(metric['issue_false_positive']['denominator'],0)
            self.assertEqual(metric['issue_excluded'][0]['reasons'],['classification_not_comparable'])
        self.assertEqual(self.dataset(origin='human_explicit')['datasets'][-1]['included'],[])
        self.assertEqual(len(self.dataset(full_scope_only=False)['datasets'][-1]['included']),4)

    def test_timer_start_pause_resume_end_and_no_unmeasured_zero(self):
        owner='window-one'
        with patch(MOD+'.time.monotonic',return_value=100):v=self.act('start',owner=owner)
        t=v['timers'][0];self.assertIsNone(t['measured_seconds'])
        with patch(MOD+'.time.monotonic',return_value=105):v=self.act('tick',owner=owner,timer_id=t['id'])
        self.assertEqual(v['summary']['measured_seconds'],5)
        with patch(MOD+'.time.monotonic',return_value=107):self.act('pause',owner=owner,timer_id=t['id'],reason='tab_hidden')
        with patch(MOD+'.time.monotonic',return_value=900):self.act('resume',owner=owner,timer_id=t['id'])
        with patch(MOD+'.time.monotonic',return_value=904):v=self.act('end',owner=owner,timer_id=t['id'])
        self.assertEqual(v['summary']['measured_seconds'],11)
        self.assertEqual(v['timers'][0]['status'],'ended');self.assertEqual(len(v['timers'][0]['intervals']),3)
        self.assertEqual(v['summary']['recorded'],0)

    def test_timer_hidden_gap_restart_duplicate_window_and_no_inferred_tail(self):
        with patch(MOD+'.time.monotonic',return_value=100):v=self.act('start',owner='window-one')
        t=v['timers'][0]
        with patch(MOD+'.time.monotonic',return_value=102):
            with self.assertRaises(Conflict):self.act('tick',owner='window-two',timer_id=t['id'])
            with self.assertRaises(Conflict):self.act('start',owner='window-two')
        with patch(MOD+'.time.monotonic',return_value=120):
            self.assertEqual(self.view()['timers'][0]['status'],'interrupted')
            self.assertIsNone(self.view()['summary']['measured_seconds'])
            self.act('resume',owner='window-two',timer_id=t['id'])
        self.svc=Evaluation(Store(self.root,self.root/'exports'))
        self.assertEqual(self.view()['timers'][0]['status'],'interrupted')
        self.act('end',owner='window-three',timer_id=t['id'])
        self.assertIsNone(self.view()['summary']['measured_seconds'])
        self.assertEqual(self.view()['summary']['unmeasured_tails'],2)

    def test_r3_r4_r5_actions_do_not_generate_labels_and_link_ids(self):
        f=self.store.focused_review(JOB_ID,'q2');c=f['cards'][0]
        self.store.resolve_focused_issue(JOB_ID,'q2',dict(revision=f['revision'],review_revision=f['review_revision'],token=c['token'],action='skip',note='synthetic workflow skip'))
        self.assertEqual(self.view()['labels'],[])
        r3=next(a for a in self.view()['actions'] if a['source']=='R3')
        sample=SampleReview(self.store);v=sample.get(JOB_ID)
        v=sample.act(JOB_ID,dict(action='start',count=1,seed='test',revision=v['revision'],basis_token=v['basis_token']))
        sid=v['active_session_id'];rid=v['sessions'][-1]['selected'][0]
        sample.act(JOB_ID,dict(action='verdict',session_id=sid,item_id=rid,verdict='pass',revision=v['revision'],basis_token=v['basis_token']))
        bulk=BulkApproval(self.store);v=bulk.get(JOB_ID)
        v=bulk.act(JOB_ID,dict(action='plan',targets=[rid],revision=v['revision'],basis_token=v['basis_token']))
        plan=v['plans'][-1]
        bulk.act(JOB_ID,dict(action='execute',revision=v['revision'],plan_id=plan['id'],targets=plan['targets'],confirmed=True,confirmation_token=plan['confirmation_token']))
        v=self.view();self.assertEqual(v['labels'],[])
        self.assertTrue(any(a['source']=='R4' for a in v['actions']));self.assertTrue(any(a['source']=='R5' for a in v['actions']))
        self.assertFalse(sample.get(JOB_ID)['sessions'][-1]['valid'])
        v=self.label(item_id='q2',action_ids=[r3['id']]);self.assertEqual(v['labels'][0]['action_links'][0]['id'],r3['id'])
        with self.assertRaises(ValueError):self.label(item_id='q3',action_ids=[r3['id']])

    def test_r3_apply_edit_provenance_and_failed_action_rollback(self):
        for action in ('apply','edit','skip'):
            with self.subTest(action=action),tempfile.TemporaryDirectory() as root:
                store=create_fixture(root);svc=Evaluation(store);f=store.focused_review(JOB_ID,'q2');c=f['cards'][0]
                p=dict(revision=f['revision'],review_revision=f['review_revision'],token=c['token'],action=action,text='changed direct text',note='synthetic skip')
                before={str(x):x.read_bytes() for x in Path(root).rglob('*') if x.is_file()}
                with patch.object(store.runtime,'save',side_effect=OSError('fixture')):
                    with self.assertRaises(OSError):store.resolve_focused_issue(JOB_ID,'q2',p)
                self.assertEqual(before,{str(x):x.read_bytes() for x in Path(root).rglob('*') if x.is_file()})
                store.resolve_focused_issue(JOB_ID,'q2',p)
                self.assertEqual(svc.get(JOB_ID)['labels'],[])
                event=next(x for x in svc.get(JOB_ID)['actions'] if x['source']=='R3')
                self.assertEqual(event['action'],action);self.assertEqual(event['issue_token'],c['token'])

    def test_legacy_and_corrupt_runtime_fail_closed_http(self):
        client=TestClient(create_app(self.store));url=f'/api/jobs/{JOB_ID}/evaluation';headers={'x-exam-token':client.get('/api/config').json()['token']}
        self.assertEqual(client.get(url).status_code,200)
        p=self.payload('label',item_id='q1',target='question',verdict='deferred',scope=['body'],note='x')
        with patch.object(self.store.runtime,'save',side_effect=OSError('fixture')):
            self.assertEqual(client.post(url,headers=headers,json=p).status_code,500)
        job=self.store.get(JOB_ID);job['storage_version']=1;(self.folder/'job.json').write_text(json.dumps(job))
        before=self.hashes();self.assertFalse(client.get(url).json()['available'])
        self.assertEqual(client.post(url,headers=headers,json=p).status_code,409);self.assertEqual(before,self.hashes())

    def test_runtime_unknown_or_corrupt_no_overwrite(self):
        path=self.folder/'runtime.json'
        original=path.read_text()
        for bad in ('{broken', '{"evaluation": []}', '{"evaluation": {"revision": "x"}}', original[:-1]+',"future_field":true}'):
            path.write_text(bad);before=self.hashes()
            with self.assertRaises(ValueError):self.view()
            self.assertEqual(before,self.hashes())
        path.write_text(original)
        self.label();self.assertEqual(self.store.runtime.load(self.folder).ai_log,[{'message':'preserve fixture runtime'}])

    def test_dataset_integrity_failure_and_invalid_selection_preserved(self):
        self.label();before=self.hashes()
        for choice in ({},{'origin':'made_up','tiers':['GREEN'],'targets':['question'],'full_scope_only':True}):
            with self.assertRaises(ValueError):self.act('dataset',selection=choice)
        self.assertEqual(before,self.hashes())
        d=self.dataset()['datasets'][0]
        runtime=self.store.runtime.load(self.folder);runtime.evaluation['datasets'][0]['selection']['origin']='human_explicit';self.store.runtime.save(self.folder,runtime)
        with self.assertRaises(ValueError):self.svc.dataset(JOB_ID,d['id'])

    def test_timer_save_failure_duplicate_and_old_request(self):
        p=self.payload('start',owner='window-one');before=self.hashes()
        with patch.object(self.store.runtime,'save',side_effect=OSError('fixture')):
            with self.assertRaises(OSError):self.svc.act(JOB_ID,p)
        self.assertEqual(before,self.hashes())
        self.svc.act(JOB_ID,p);self.svc.act(JOB_ID,p);self.assertEqual(len(self.view()['timers']),1)
        with self.assertRaises(Conflict):self.svc.act(JOB_ID,dict(p,request_id=uuid.uuid4().hex))

    def test_recorded_human_origin_not_inferred_and_not_mixed(self):
        # Pure synthetic test exercises the human-origin code path only; never production data.
        (self.folder/'evaluation-fixture.json').unlink()
        v=self.label();self.assertIsNone(v['labels'][0]['actor']);self.assertEqual(v['labels'][0]['origin'],'human_explicit')
        self.assertEqual(self.dataset()['datasets'][0]['included'],[])
        self.assertEqual(len(self.dataset(origin='human_explicit')['datasets'][-1]['included']),1)
