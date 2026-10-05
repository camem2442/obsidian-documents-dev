import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from _scripts_2.apps.exam.exam_processor.tests.batch_jobs_fixture import (
    create_fixture,
    fixture_processor,
    JOB_IDS,
)
from _scripts_2.apps.exam.exam_processor.storage.batch_jobs import BatchJobs, execution_lock, job_worker_lease
from _scripts_2.apps.exam.exam_processor.storage.store import Store,Conflict
from _scripts_2.apps.exam.exam_processor.pipeline.work_queue import WorkQueue


class BatchJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=create_fixture(self.root)
        self.calls=[]
        def processor(*args,**kwargs):
            self.calls.append((args[1],args[2]));return fixture_processor(*args,**kwargs)
        self.svc=BatchJobs(self.store,processor)

    def act(self,v,action,**extra):
        return self.svc.act(dict(action=action,batch_id=v['id'],revision=v['revision'],request_id=uuid.uuid4().hex,**extra))

    def plan(self):
        pre=self.svc.preview(JOB_IDS,'audit')
        return self.svc.act(dict(action='plan',job_ids=JOB_IDS,operation='audit',preview_hash=pre['plan_hash'],request_id=uuid.uuid4().hex))

    def start(self):
        p=self.plan();return self.act(p,'execute',confirmed_plan_hash=p['plan_hash'])

    def test_preview_readonly_shared_selection_and_order(self):
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        pre=self.svc.preview(JOB_IDS,'audit');self.assertEqual(pre,self.svc.preview(JOB_IDS,'audit'))
        self.assertEqual(pre['plan']['planned_calls'],6)
        self.assertEqual([e['job_id'] for e in pre['plan']['entries']],[JOB_IDS[0]]*3+[JOB_IDS[1]]*3)
        self.assertTrue(all(e['basis']['files'] for e in pre['plan']['entries']))
        self.assertEqual(self.calls,[]);self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_explicit_plan_execute_and_completed_no_replay(self):
        v=self.plan();self.assertEqual(self.calls,[])
        with self.assertRaises(Conflict):self.act(v,'execute',confirmed_plan_hash='wrong')
        v=self.act(v,'execute',confirmed_plan_hash=v['plan_hash']);self.svc.run(v['id'])
        r=self.svc.get(v['id']);self.assertEqual(r['counts']['completed'],6);self.assertEqual(len(self.calls),6)
        self.svc.run(v['id']);self.assertEqual(len(self.calls),6)
        for jid in JOB_IDS:self.assertTrue(all(i['review']=='pending' for i in self.store.get(jid)['items'] if i['kind']=='question'))

    def test_stale_plan_source_bytes_blocks_entire_start(self):
        v=self.plan();(self.store.job_dir(JOB_IDS[1])/'regions/fixture.png').write_bytes(b'changed')
        with self.assertRaises(Conflict):self.act(v,'execute',confirmed_plan_hash=v['plan_hash'])
        self.assertEqual(self.calls,[])

    def test_pause_live_duplicate_worker_and_individual_queue_reserved(self):
        entered=threading.Event();release=threading.Event()
        def block(*args,**kwargs):
            self.calls.append((args[1],args[2]));entered.set();release.wait(10);return fixture_processor(*args,**kwargs)
        self.svc.queue.processor=block;v=self.start()
        worker=threading.Thread(target=self.svc.run,args=(v['id'],));worker.start()
        try:
            self.assertTrue(entered.wait(5));r=self.svc.get(v['id']);self.assertTrue(r['worker_live'])
            self.svc.run(v['id']);self.assertEqual(len(self.calls),1)
            with self.assertRaises(Conflict):WorkQueue(self.store).prepare(JOB_IDS[0],'audit')
            self.act(r,'pause');release.set();worker.join(10)
            r=self.svc.get(v['id']);self.assertEqual(r['status'],'paused');self.assertEqual(r['counts']['completed'],1)
            self.svc.queue.processor=fixture_processor;r=self.act(r,'resume');self.svc.run(r['id'])
            self.assertEqual(self.svc.get(r['id'])['counts']['completed'],6)
        finally:release.set();worker.join(10)

    def test_partial_failure_retry_only_failed_preserves_attempts(self):
        failed=[False]
        def process(*args,**kwargs):
            self.calls.append((args[1],args[2]))
            if not failed[0]:failed[0]=True;raise ValueError('explicit fixture failure')
            return fixture_processor(*args,**kwargs)
        self.svc.queue.processor=process;v=self.start();self.svc.run(v['id']);v=self.svc.get(v['id'])
        self.assertEqual(v['counts']['failed'],1);self.assertEqual(v['counts']['completed'],5)
        target=next(e['id'] for e in v['entries'] if e['status']=='failed')
        v=self.act(v,'retry',entry_ids=[target],reason='Explicit synthetic retry');self.svc.run(v['id']);v=self.svc.get(v['id'])
        self.assertEqual(len(v['attempts']),7);self.assertEqual(v['counts']['completed'],6)

    def test_selected_retry_does_not_dispatch_unexecuted_items(self):
        entered=threading.Event();release=threading.Event()
        def fail(*args,**kwargs):
            entered.set();release.wait(5);raise ValueError('Synthetic failed call')
        self.svc.queue.processor=fail;v=self.start()
        worker=threading.Thread(target=self.svc.run,args=(v['id'],));worker.start()
        try:
            self.assertTrue(entered.wait(5));self.act(self.svc.get(v['id']),'pause')
        finally:release.set();worker.join(10)
        v=self.svc.get(v['id']);target=next(e['id'] for e in v['entries'] if e['status']=='failed')
        self.svc.queue.processor=fixture_processor
        v=self.act(v,'retry',entry_ids=[target],reason='Only the selected failed entry')
        self.svc.run(v['id']);v=self.svc.get(v['id'])
        self.assertEqual(v['counts']['completed'],1);self.assertEqual(v['counts']['pending'],5)
        self.assertEqual(len(v['attempts']),2);self.assertEqual(v['status'],'paused')

    def test_crash_after_dispatch_does_not_recall_uncertain(self):
        class Crash(BaseException):pass
        def crash(*args,**kwargs):
            self.calls.append((args[1],args[2]));fixture_processor(*args,**kwargs);raise Crash()
        self.svc.queue.processor=crash;v=self.start()
        with self.assertRaises(Crash):self.svc.run(v['id'])
        svc=BatchJobs(Store(self.root,self.root/'exports'),fixture_processor)
        r=svc.get(v['id']);self.assertEqual(r['counts']['uncertain'],1);self.assertFalse(r['worker_live'])
        svc.run(v['id']);self.assertEqual(len(self.calls),1)
        r=svc.act(dict(action='recover',batch_id=r['id'],revision=r['revision'],request_id=uuid.uuid4().hex))
        with self.assertRaises(Conflict):self.act(r,'resume')
        ended=self.act(r,'end',reason='Preserve ambiguous result without another call')
        self.assertEqual(ended['counts']['uncertain'],1);self.assertEqual(ended['status'],'ended')

    def test_atomic_write_failure_plan_and_receipt(self):
        pre=self.svc.preview(JOB_IDS,'audit')
        p=dict(action='plan',job_ids=JOB_IDS,operation='audit',preview_hash=pre['plan_hash'],request_id=uuid.uuid4().hex)
        with patch('_scripts_2.apps.exam.exam_processor.storage.batch_jobs.atomic_json_save',side_effect=OSError('fixture')):
            with self.assertRaises(OSError):self.svc.act(p)
        self.assertEqual(self.svc.list(),[])
        v=self.svc.act(p);self.assertEqual(v['id'],self.svc.act(p)['id'])
        with self.assertRaises(Conflict):self.svc.act(dict(p,operation='extract'))

    def test_ended_is_terminal_and_uncertainty_blocks_new_plans(self):
        class Crash(BaseException):pass
        self.svc.queue.processor=lambda *a,**k: (_ for _ in ()).throw(Crash())
        v=self.start()
        with self.assertRaises(Crash):self.svc.run(v['id'])
        v=self.act(self.svc.get(v['id']),'end',reason='Unknown outcome preserved')
        self.assertEqual(v['counts']['uncertain'],1)
        self.assertEqual(v['counts']['abandoned'],5)
        for action in ('recover','resume','retry','end'):
            with self.assertRaises(Conflict):self.act(v,action,reason='Cannot resurrect')
        preview=self.svc.preview(JOB_IDS,'audit')
        self.assertTrue(all(e['job_id'] != JOB_IDS[0] for e in preview['plan']['entries']))
        with self.assertRaises(Conflict):WorkQueue(self.store).prepare(JOB_IDS[0],'audit')

    def test_stale_pause_is_monotonic_and_receipt_write_failure_is_uncertain(self):
        v=self.start()
        stopped=self.svc.act(dict(action='pause',batch_id=v['id'],revision='old',request_id=uuid.uuid4().hex))
        self.assertTrue(stopped['stop_requested']);self.assertEqual(self.calls,[])
        v=self.act(stopped,'resume')
        original=self.svc._save
        def fail_receipt(value):
            if any(e['status']=='completed' for e in value['entries']):raise OSError('receipt failure')
            return original(value)
        with patch.object(self.svc,'_save',side_effect=fail_receipt):
            with self.assertRaises(OSError):self.svc.run(v['id'])
        after=self.svc.get(v['id'])
        self.assertEqual(after['counts']['uncertain'],1);self.assertEqual(len(self.calls),1)
        self.svc.run(v['id']);self.assertEqual(len(self.calls),1)

    def test_individual_worker_lease_excludes_plan_and_prepare_across_stores(self):
        with execution_lock(self.store):lease=job_worker_lease(self.store,JOB_IDS[0])
        try:
            other=Store(self.root,self.root/'exports')
            pre=BatchJobs(other,fixture_processor).preview(JOB_IDS,'audit')
            self.assertEqual(pre['plan']['planned_calls'],3)
            with self.assertRaises(Conflict):WorkQueue(other).prepare(JOB_IDS[0],'audit')
        finally:lease.close()
        self.assertEqual(self.svc.preview(JOB_IDS,'audit')['plan']['planned_calls'],6)

    def test_legacy_missing_empty_and_policy_exclusions(self):
        path=self.store.job_dir(JOB_IDS[0])/'job.json'
        raw=json.loads(path.read_text());raw['storage_version']=1;path.write_text(json.dumps(raw))
        preview=self.svc.preview([JOB_IDS[0],'a'*32],'audit')
        self.assertEqual(preview['plan']['planned_calls'],0)
        self.assertEqual([e['reasons'][0] for e in preview['plan']['excluded']],['legacy_read_only','missing_job'])
        v=self.svc.act(dict(action='plan',job_ids=[JOB_IDS[0],'a'*32],operation='audit',preview_hash=preview['plan_hash'],request_id=uuid.uuid4().hex))
        with self.assertRaises(ValueError):self.act(v,'execute',confirmed_plan_hash=v['plan_hash'])
        value=self.svc._read(v['id']);value['plan']['policy']='future/unknown';self.svc._save(value)
        with self.assertRaises(Conflict):self.act(value,'execute',confirmed_plan_hash=value['plan_hash'])
        self.assertEqual(self.calls,[])

    def test_stale_request_and_changed_review_before_resume(self):
        v=self.start();paused=self.act(v,'pause')
        with self.assertRaises(Conflict):self.act(v,'resume')
        folder=self.store.job_dir(JOB_IDS[0]);review=self.store.reviews.get(folder,'q1')
        review.note='Synthetic changed review evidence';self.store.reviews.save(folder,review)
        v=self.act(paused,'resume');self.svc.run(v['id']);r=self.svc.get(v['id'])
        self.assertEqual(r['counts']['skipped'],1);self.assertEqual(r['counts']['completed'],5)

    def test_extract_uses_existing_processor_without_approval_or_labels(self):
        from _scripts_2.apps.exam.exam_processor.pipeline.ai import process
        for jid in JOB_IDS:
            folder=self.store.job_dir(jid)
            for rid in ('q1','q2','q3'):
                review=self.store.reviews.get(folder,rid);review.history=[];review.extracted_revision=None
                self.store.reviews.save(folder,review)
        calls=[]
        def processor(store,jid,rid,revision,operation,**kwargs):
            calls.append((jid,rid,operation))
            return process(store,jid,rid,revision,operation,caller=lambda _: {'data':{'body':'Synthetic extracted text','diagrams':[]},'provider':'synthetic_fixture'},**kwargs)
        self.svc.queue.processor=processor
        pre=self.svc.preview(JOB_IDS,'extract')
        v=self.svc.act(dict(action='plan',job_ids=JOB_IDS,operation='extract',preview_hash=pre['plan_hash'],request_id=uuid.uuid4().hex))
        v=self.act(v,'execute',confirmed_plan_hash=v['plan_hash']);self.svc.run(v['id']);r=self.svc.get(v['id'])
        self.assertEqual(pre['plan']['planned_calls'],8)
        self.assertEqual(r['counts']['completed'],8);self.assertEqual(len(calls),8)
        for jid in JOB_IDS:
            self.assertTrue(all(i['review']=='pending' for i in self.store.get(jid)['items'] if i['kind']=='question'))

    def test_concurrent_plans_only_one_can_reserve_same_jobs(self):
        first,second=self.plan(),self.plan()
        other=BatchJobs(Store(self.root,self.root/'exports'),fixture_processor)
        barrier=threading.Barrier(2);results=[]
        def execute(svc,v):
            barrier.wait(5)
            try:
                svc.act(dict(action='execute',batch_id=v['id'],revision=v['revision'],confirmed_plan_hash=v['plan_hash'],request_id=uuid.uuid4().hex))
                results.append('reserved')
            except Conflict:results.append('conflict')
        threads=[threading.Thread(target=execute,args=(svc,v)) for svc,v in ((self.svc,first),(other,second))]
        for thread in threads:thread.start()
        for thread in threads:thread.join(10)
        self.assertEqual(sorted(results),['conflict','reserved']);self.assertEqual(self.calls,[])

    def test_http_plan_confirmation_and_auth_without_external_provider(self):
        from fastapi.testclient import TestClient
        from _scripts_2.apps.exam.exam_processor.server import create_app
        with TestClient(create_app(self.store,batch_processor=fixture_processor)) as client:
            self.assertEqual(client.post('/api/batches/preview',json={'job_ids':JOB_IDS,'operation':'audit'}).status_code,403)
            client.headers['x-exam-token']=client.get('/api/config').json()['token']
            preview=client.post('/api/batches/preview',json={'job_ids':JOB_IDS,'operation':'audit'}).json()
            response=client.post('/api/batches',json={'action':'plan','job_ids':JOB_IDS,'operation':'audit','preview_hash':preview['plan_hash'],'request_id':uuid.uuid4().hex})
            self.assertEqual(response.status_code,200);v=response.json();self.assertEqual(v['attempts'],[])
            response=client.post('/api/batches',json={'action':'execute','batch_id':v['id'],'revision':v['revision'],'confirmed_plan_hash':'wrong','request_id':uuid.uuid4().hex})
            self.assertEqual(response.status_code,409)
            self.assertEqual(client.get('/api/batches/'+v['id']).json()['status'],'planned')
            self.act(v,'execute',confirmed_plan_hash=v['plan_hash'])
            self.assertEqual(client.post('/api/jobs/'+JOB_IDS[0]+'/queue',json={'operation':'audit'}).status_code,409)
            self.assertEqual(client.post('/api/jobs/'+JOB_IDS[0]+'/items/q1/ai',json={'operation':'audit','revision':'revision-q1'}).status_code,409)
