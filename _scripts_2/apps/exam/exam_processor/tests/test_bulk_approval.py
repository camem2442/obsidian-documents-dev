import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from _scripts_2.apps.exam.exam_processor.tests.bulk_fixture import create_fixture, JOB_ID
from _scripts_2.apps.exam.exam_processor.storage.bulk_approval import BulkApproval
from _scripts_2.apps.exam.exam_processor.storage.sample_review import SampleReview
from _scripts_2.apps.exam.exam_processor.storage.store import Store, Conflict
from _scripts_2.apps.exam.exam_processor.server import create_app

MODULE = '_scripts_2.apps.exam.exam_processor.storage.bulk_approval'


class BulkApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.store = create_fixture(self.root)
        self.folder = self.store.job_dir(JOB_ID); self.svc = BulkApproval(self.store)

    def get(self):
        return self.svc.get(JOB_ID)

    def act(self, action, **kw):
        v = self.get()
        return self.svc.act(JOB_ID, {'action': action, 'revision': v['revision'], 'basis_token': v['basis_token'], **kw})

    def plan(self, targets=None):
        return self.act('plan', targets=targets or ['q1', 'q2', 'q3'])['plans'][-1]

    def execute(self, plan, **kw):
        return self.act('execute', plan_id=plan['id'], targets=plan['targets'],
                        confirmation_token=plan['confirmation_token'], confirmed=True, **kw)

    def retry(self, plan):
        return self.act('retry', execution_id=self.get()['active_execution_id'], targets=plan['targets'],
                        confirmation_token=plan['confirmation_token'], confirmed=True)

    def recover(self):
        return self.act('recover', execution_id=self.get()['active_execution_id'])

    def snapshot(self, runtime=False):
        return {str(p.relative_to(self.folder)):p.read_bytes() for p in self.folder.rglob('*')
                if p.is_file() and p.name != '.bulk-approval.lock' and (runtime or p.name != 'runtime.json')}

    def approved(self):
        return [i['id'] for i in self.store.get(JOB_ID)['items'] if i['kind']=='question' and i['review']=='approved']

    def test_readonly_invalid_targets_zero_legacy_and_corrupt_runtime(self):
        before = self.snapshot(True); self.get(); self.get(); self.assertEqual(before, self.snapshot(True))
        for targets in ([], ['q1', 'q1'], ['p1'], ['missing'], 'q1', [True]):
            with self.subTest(targets=targets), self.assertRaises((ValueError, Conflict)):
                self.act('plan', targets=targets)
        self.assertEqual(before, self.snapshot(True))
        raw = json.loads((self.folder/'job.json').read_text()); raw['warnings']=['explicit fixture blocker']
        (self.folder/'job.json').write_text(json.dumps(raw))
        self.assertTrue(all(c['reasons'] for c in self.get()['candidates']))
        with self.assertRaises(Conflict): self.plan()
        raw = self.store.get(JOB_ID); raw['storage_version']=1
        (self.folder/'job.json').write_text(json.dumps(raw)); before=self.snapshot(True)
        self.assertFalse(self.get()['available'])
        with self.assertRaises(Conflict): self.plan()
        self.assertEqual(before,self.snapshot(True))

    def test_incomplete_problem_paused_and_stale_sessions_block(self):
        for mode in ('incomplete','problem','paused','stale'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                store=create_fixture(root); svc=SampleReview(store); view=svc.get(JOB_ID)
                payload=dict(action='start',count=1,seed='new',revision=view['revision'],basis_token=view['basis_token'])
                view=svc.act(JOB_ID,payload); s=view['sessions'][-1]
                if mode in ('problem','paused'):
                    svc.act(JOB_ID,dict(action='pause' if mode=='paused' else 'verdict',verdict='problem',note='source mismatch',
                        item_id=s['selected'][0],session_id=s['id'],revision=view['revision'],basis_token=view['basis_token']))
                if mode=='stale': store._review_svc.update_note(store.job_dir(JOB_ID),'q1','changed')
                bulk=BulkApproval(store); v=bulk.get(JOB_ID)
                with self.assertRaises(Conflict): bulk.act(JOB_ID,dict(action='plan',targets=['q1'],revision=v['revision'],basis_token=v['basis_token']))

    def test_preview_confirmation_success_keeps_notes_revisions_and_session(self):
        before=self.snapshot(); sample=copy.deepcopy(self.store.runtime.load(self.folder).sample_review)
        plan=self.plan(); self.assertEqual(before,self.snapshot()); self.assertEqual(self.approved(),[])
        for kw in ({}, {'confirmed':True,'targets':['q1'],'confirmation_token':plan['confirmation_token']},
                   {'confirmed':True,'targets':plan['targets'],'confirmation_token':'bad'}):
            with self.assertRaises(Conflict): self.act('execute',plan_id=plan['id'],**kw)
        with patch.object(self.store._review_svc,'approve',wraps=self.store._review_svc.approve) as approve:
            view=self.execute(plan);self.assertEqual(approve.call_count,3)
        run=view['executions'][-1];self.assertEqual(run['status'],'completed');self.assertEqual(run['counts']['done'],3)
        self.assertEqual(view['sessions'][0]['status'],'stale');self.assertEqual(self.store.runtime.load(self.folder).sample_review,sample)
        self.assertEqual(view['job']['review_queue_summary']['pending']['GREEN'],1)
        self.assertEqual(self.store.runtime.load(self.folder).ai_log,[{'message':'preserve fixture runtime'}])
        for rid in plan['targets']:
            rec=self.store.records.get(self.folder,rid);rev=self.store.reviews.get(self.folder,rid)
            self.assertEqual(rec.provenance.revision_id,'revision-'+rid);self.assertEqual(len(rev.approval_events),1)
            self.assertEqual(rev.approval_events[0]['execution_id'],run['id']);self.assertEqual(rev.note,'preserve existing review note' if rid=='q1' else '')
        for name, content in before.items():
            if not any(name==f'{kind}/{rid}.json' for kind in ('records','review') for rid in plan['targets']):
                self.assertEqual(content,(self.folder/name).read_bytes(),name)
        with self.assertRaises(Conflict):self.execute(plan)
        with self.assertRaises(Conflict):self.retry(plan)

    def test_full_preflight_for_all_change_kinds_zero_approvals(self):
        for mode in ('content','review','candidate','dependency','classification','source','policy','r4_policy','session','gate'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                store=create_fixture(root); bulk=BulkApproval(store); folder=store.job_dir(JOB_ID)
                v=bulk.get(JOB_ID); v=bulk.act(JOB_ID,dict(action='plan',targets=['q1','q2'],revision=v['revision'],basis_token=v['basis_token']));p=v['plans'][-1]
                if mode=='content':store.update(JOB_ID,'q2','revision-q2',{'body':'different'})
                if mode=='review':store._review_svc.update_note(folder,'q2','different')
                if mode=='candidate':
                    raw=json.loads((folder/'job.json').read_text());raw['record_ids'].remove('q4');(folder/'job.json').write_text(json.dumps(raw))
                if mode=='dependency':
                    rec=store.records.get(folder,'p1');rec.review.human_approval='pending';store.records.save(folder,rec)
                if mode=='classification':
                    rev=store.reviews.get(folder,'q2');rev.warnings=['new'];store.reviews.save(folder,rev)
                if mode=='source':(folder/'regions'/'fixture.png').write_bytes(b'changed source')
                if mode=='session':
                    svc=SampleReview(store);s=svc.get(JOB_ID);svc.act(JOB_ID,dict(action='start',count=1,seed='another',revision=s['revision'],basis_token=s['basis_token']))
                target = MODULE+'.POLICY_VERSION' if mode=='policy' else '_scripts_2.apps.exam.exam_processor.storage.sample_review.POLICY_VERSION' if mode=='r4_policy' else MODULE+'.approval_errors'
                replacement = 'new-policy' if mode in ('policy','r4_policy') else (lambda *a,**k:['gate blocked']) if mode=='gate' else None
                from contextlib import nullcontext
                with patch(target,replacement) if replacement else nullcontext():
                    v=bulk.act(JOB_ID,dict(action='execute',revision=v['revision'],plan_id=p['id'],targets=p['targets'],confirmed=True,confirmation_token=p['confirmation_token']))
                self.assertEqual(v['executions'][-1]['status'],'blocked')
                self.assertFalse(any(i['review']=='approved' for i in v['job']['items'] if i['kind']=='question'))
                self.assertTrue(all(not x['attempts'] for x in v['executions'][-1]['items'].values()))

    def test_duplicate_and_concurrent_requests(self):
        p=self.plan();v=self.get();payload=dict(action='execute',revision=v['revision'],plan_id=p['id'],targets=p['targets'],confirmed=True,confirmation_token=p['confirmation_token'])
        def run(_):
            try:self.svc.act(JOB_ID,payload);return 200
            except Conflict:return 409
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sorted(pool.map(run,range(2))),[200,409])
        self.assertEqual(len(self.get()['executions']),1)
        self.assertEqual(len(self.store.reviews.get(self.folder,'q1').approval_events),1)

    def test_prepared_save_failure_no_approval_and_recovery(self):
        p=self.plan();original=self.store.runtime.save
        def save(folder,runtime):
            r=runtime.bulk_approval.get('executions',[])
            if r and any(i['status']=='prepared' for i in r[-1]['items'].values()):raise OSError('prepare disk failure')
            return original(folder,runtime)
        with patch.object(self.store.runtime,'save',side_effect=save),self.assertRaises(OSError):self.execute(p)
        self.assertEqual(self.approved(),[]);self.assertEqual(self.get()['executions'][-1]['status'],'recovery_required')
        with self.assertRaises(Conflict):self.retry(p)
        self.recover();self.assertEqual(self.retry(p)['executions'][-1]['status'],'completed')

    def test_record_and_review_save_failure_rollback_partial_retry(self):
        for repository in ('records','reviews'):
            with self.subTest(repository=repository), tempfile.TemporaryDirectory() as root:
                # Switch only this test's explicit fixture.
                self.store=create_fixture(root);self.svc=BulkApproval(self.store);self.folder=self.store.job_dir(JOB_ID)
                p=self.plan();repo=getattr(self.store,repository);original=repo.save
                def save(folder,obj):
                    rid=obj.record_id if repository=='reviews' else obj.question.question_id
                    if rid=='q2':raise OSError('injected '+repository+' failure')
                    return original(folder,obj)
                with patch.object(repo,'save',side_effect=save):v=self.execute(p)
                r=v['executions'][-1];self.assertEqual(r['status'],'partial');self.assertEqual(self.approved(),['q1'])
                self.assertEqual([x['status'] for x in r['items'].values()],['done','failed','pending'])
                self.assertEqual(self.retry(p)['executions'][-1]['status'],'completed')
                self.assertEqual(len(self.store.reviews.get(self.folder,'q1').approval_events),1)
                self.assertEqual(len(self.get()['executions'][-1]['items']['q2']['attempts']),2)

    def test_approval_saved_checkpoint_failure_restart_receipt_recovery(self):
        p=self.plan();original=self.store.runtime.save
        def save(folder,runtime):
            runs=runtime.bulk_approval.get('executions',[])
            if runs and runs[-1]['items']['q1']['status']=='done':raise OSError('checkpoint failed')
            return original(folder,runtime)
        with patch.object(self.store.runtime,'save',side_effect=save), self.assertRaises(OSError):self.execute(p)
        self.assertEqual(self.approved(),['q1']);self.assertEqual(self.get()['executions'][-1]['items']['q1']['status'],'prepared')
        self.store=Store(self.root,self.root/'exports');self.svc=BulkApproval(self.store)
        before=self.snapshot();v=self.recover();self.assertEqual(v['executions'][-1]['items']['q1']['status'],'done');self.assertEqual(before,self.snapshot())
        self.assertEqual(self.retry(p)['executions'][-1]['status'],'completed')
        self.assertEqual(len(self.store.reviews.get(self.folder,'q1').approval_events),1)

    def test_crash_between_approval_files_remains_uncertain(self):
        p=self.plan()
        class Crash(BaseException):pass
        with patch.object(self.store.reviews,'save',side_effect=Crash),self.assertRaises(Crash):self.execute(p)
        self.assertEqual(self.approved(),['q1']);before=self.snapshot()
        v=self.recover();self.assertEqual(v['executions'][-1]['items']['q1']['status'],'uncertain');self.assertEqual(before,self.snapshot())
        with self.assertRaises(Conflict):self.retry(p)
        with self.assertRaises(Conflict):self.act('close',execution_id=v['active_execution_id'])
        with self.assertRaises(Conflict):self.plan()

    def test_external_approval_not_absorbed_and_changed_completed_item_blocks(self):
        for mode in ('other','completed'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                self.store=create_fixture(root);self.svc=BulkApproval(self.store);self.folder=self.store.job_dir(JOB_ID)
                p=self.plan();original=self.store._review_svc.approve
                def approve(folder,rid,*a,**kw):
                    result=original(folder,rid,*a,**kw)
                    if rid=='q1':
                        if mode=='other':original(folder,'q2','revision-q2')
                        else:self.store._review_svc.update_note(folder,'q1','external note')
                    return result
                with patch.object(self.store._review_svc,'approve',side_effect=approve):v=self.execute(p)
                r=v['executions'][-1];self.assertNotEqual(r['status'],'completed');self.assertEqual(r['items']['q2']['status'],'pending')
                self.assertEqual(r['items']['q1']['status'],'done' if mode=='other' else 'uncertain')
                self.assertEqual(len(self.store.reviews.get(self.folder,'q2').approval_events),0)
                v=self.recover();self.assertNotEqual(v['executions'][-1]['status'],'completed')

    def test_completed_proof_blocks_later_external_edit(self):
        p=self.plan();original=self.store.records.save
        def save(folder,rec):
            if rec.question.question_id=='q2':raise OSError('stop')
            return original(folder,rec)
        with patch.object(self.store.records,'save',side_effect=save):self.execute(p)
        self.store._review_svc.update_note(self.folder,'q1','external edit after checkpoint')
        v=self.retry(p);self.assertEqual(v['executions'][-1]['status'],'blocked');self.assertEqual(self.approved(),['q1'])
        self.assertEqual(len(self.store.reviews.get(self.folder,'q1').approval_events),1)

    def test_new_plan_after_explicit_close_preserves_history(self):
        p=self.plan();self.store._review_svc.update_note(self.folder,'q4','external edit')
        self.execute(p);v=self.get();self.act('close',execution_id=v['active_execution_id'])
        self.assertEqual(self.get()['executions'][-1]['status'],'closed')
        self.assertEqual(self.approved(),[])

    def test_http_save_failure_and_legacy_review_without_receipts(self):
        # Simulates R4-era v2 review file before R5 introduces receipt storage.
        for rid in ('q1','q2','q3','q4','p1'):
            path=self.folder/'review'/(rid+'.json');raw=json.loads(path.read_text());raw.pop('approval_events');path.write_text(json.dumps(raw))
        svc=SampleReview(self.store);v=svc.get(JOB_ID)
        v=svc.act(JOB_ID,dict(action='start',count=1,seed='old-v2',revision=v['revision'],basis_token=v['basis_token']))
        s=v['sessions'][-1];svc.act(JOB_ID,dict(action='verdict',verdict='pass',item_id=s['selected'][0],session_id=s['id'],revision=v['revision'],basis_token=v['basis_token']))
        p=self.plan();client=TestClient(create_app(self.store));token=client.get('/api/config').json()['token'];v=self.get()
        payload=dict(action='execute',revision=v['revision'],plan_id=p['id'],targets=p['targets'],confirmed=True,confirmation_token=p['confirmation_token'])
        with patch.object(self.store.runtime,'save',side_effect=OSError('disk')):
            response=client.post(f'/api/jobs/{JOB_ID}/bulk-approval',json=payload,headers={'x-exam-token':token})
        self.assertEqual(response.status_code,500);self.assertEqual(self.approved(),[])
        self.assertEqual(self.execute(p)['executions'][-1]['status'],'completed')

    def test_cross_store_bulk_lock_and_corrupt_runtime_fail_closed(self):
        import fcntl
        p=self.plan()
        with (self.folder/'.bulk-approval.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(Conflict):self.execute(p)
        self.assertEqual(self.approved(),[])
        path=self.folder/'runtime.json';raw=json.loads(path.read_text());raw['bulk_approval']=[];path.write_text(json.dumps(raw))
        before=self.snapshot(True)
        with self.assertRaises(ValueError):self.get()
        self.assertEqual(before,self.snapshot(True))

    def test_failed_rollback_and_external_approval_after_intent_are_uncertain(self):
        for mode in ('rollback','external'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                self.store=create_fixture(root);self.svc=BulkApproval(self.store);self.folder=self.store.job_dir(JOB_ID)
                p=self.plan()
                if mode=='rollback':
                    with patch.object(self.store.reviews,'save',side_effect=OSError('review write failed')), \
                         patch('_scripts_2.apps.exam.exam_processor.storage.focused_transaction._restore',side_effect=OSError('rollback failed')):
                        v=self.execute(p)
                    self.assertEqual(v['executions'][-1]['items']['q1']['status'],'uncertain')
                else:
                    class Crash(BaseException):pass
                    with patch.object(self.store._review_svc,'approve',side_effect=Crash),self.assertRaises(Crash):self.execute(p)
                    self.store._review_svc.approve(self.folder,'q1','revision-q1',passage_approval_status='approved')
                v=self.recover();self.assertEqual(v['executions'][-1]['items']['q1']['status'],'uncertain')
                with self.assertRaises(Conflict):self.retry(p)
                self.assertEqual(self.get()['executions'][-1]['items']['q2']['status'],'pending')

    def test_unknown_review_fields_block_without_loss_and_embedded_runtime_retained(self):
        path=self.folder/'review'/'q1.json';raw=json.loads(path.read_text());raw['future_field']={'preserve':True};path.write_text(json.dumps(raw))
        before=self.snapshot()
        self.assertTrue(next(c for c in self.get()['candidates'] if c['id']=='q1')['reasons'])
        with self.assertRaises(Conflict):self.plan()
        self.assertEqual(before,self.snapshot())
        # A v2 manifest with no runtime can be queried without any write or migration.
        path=self.folder/'runtime.json';path.unlink()
        manifest=self.folder/'job.json';raw=json.loads(manifest.read_text());raw['ai_log']=[{'embedded':'preserve'}];manifest.write_text(json.dumps(raw))
        before=self.snapshot(True);self.get();self.assertEqual(before,self.snapshot(True))
        svc=SampleReview(self.store);v=svc.get(JOB_ID)
        svc.act(JOB_ID,dict(action='start',count=1,seed='compatibility',revision=v['revision'],basis_token=v['basis_token']))
        self.assertEqual(self.store.runtime.load(self.folder).ai_log,[{'embedded':'preserve'}])
