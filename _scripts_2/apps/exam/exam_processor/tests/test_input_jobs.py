import copy
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from _scripts_2.apps.exam.exam_processor.storage.store import Store, Conflict
from _scripts_2.apps.exam.exam_processor.storage.input_jobs import InputJobs
from _scripts_2.apps.exam.exam_processor.storage.batch_jobs import BatchJobs
from _scripts_2.apps.exam.exam_processor.ingestion.input_plan import inspect, catalog


def snapshot(root): return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


class InputJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'data');self.svc=InputJobs(self.store)
        self.source=self.root/'source.md';self.source.write_text('[1~2] 다음 글을 읽으시오.\n\n지문입니다.\n\n### 1. 첫 질문?\n① 하나\n② 둘\n\n### 2. 둘째 질문?\n① 하나\n② 둘\n')
        self.input=dict(id='one',path=str(self.source),format='korean_notes',source='합성 출처',track='매체',included=True)

    def plan(self,units=None):
        units=units if units is not None else [self.input]
        pre=self.svc.preview(units)
        return self.svc.act(dict(action='plan',request_id=uuid.uuid4().hex,inputs=units,preview_hash=pre['plan_hash']))

    def act(self,v,action,**extra):
        return self.svc.act(dict(action=action,plan_id=v['id'],revision=v['revision'],request_id=uuid.uuid4().hex,**extra))

    def start(self,units=None):
        v=self.plan(units);return self.act(v,'execute',confirmed_plan_hash=v['plan_hash'])

    def test_readonly_zero_input_validation_and_full_catalog(self):
        before=snapshot(self.root)
        self.assertEqual(self.svc.preview([])['plan']['entries'],[])
        self.assertEqual(len(catalog()),6)
        self.assertEqual(inspect(self.input)['state'],'ready')
        for changes,state in [({'path':'/missing'},'missing_conditions'),({'format':''},'ambiguous'),
                              ({'format':'unknown'},'unsupported'),({'source':''},'missing_conditions'),
                              ({'pages':'1'},'missing_conditions'),({'track':'영어'},'missing_conditions')]:
            self.assertEqual(inspect(dict(self.input,**changes))['state'],state)
        self.assertEqual(snapshot(self.root),before)
        v=self.plan([])
        with self.assertRaises(ValueError): self.act(v,'execute',confirmed_plan_hash=v['plan_hash'])

    def test_plan_only_explicit_execute_canonical_receipt_and_no_ai(self):
        v=self.plan();self.assertFalse((self.store.root/'jobs').exists())
        with self.assertRaises(Conflict): self.act(v,'execute',confirmed_plan_hash='wrong')
        v=self.act(v,'execute',confirmed_plan_hash=v['plan_hash']);self.svc.run(v['id'])
        v=self.svc.get(v['id']);self.assertEqual(v['counts']['completed'],1)
        jid=v['entries'][0]['job_id'];self.svc.complete_job(jid,v['attempts'][0]['receipt'])
        self.assertTrue(all(i['review']=='pending' for i in self.store.get(jid)['items']))
        self.assertFalse((self.store.job_dir(jid)/'runtime.json').exists())
        self.svc.run(v['id']);self.assertEqual(len(self.store.list()),1)
        self.assertEqual(self.svc.preview([self.input])['plan']['entries'][0]['state'],'existing')

    def test_identity_content_paths_options_and_relations(self):
        other=self.root/'copy.md';other.write_bytes(self.source.read_bytes())
        a=inspect(self.input);b=inspect(dict(self.input,path=str(other)))
        self.assertEqual(a['identity'],b['identity']);self.assertNotEqual(a['evidence'],b['evidence'])
        self.assertNotEqual(a['identity'],inspect(dict(self.input,source='다른 출처'))['identity'])
        solution=self.root/'solution.md';solution.write_text('### 1. 답\n정답 ①\n')
        self.assertEqual(inspect(dict(self.input,solution=str(solution)))['state'],'missing_conditions')
        c=inspect(dict(self.input,solution=str(solution),relationship_confirmed=True))
        self.assertEqual(c['state'],'ready');self.assertNotEqual(c['identity'],a['identity'])
        self.assertEqual(inspect(dict(self.input,solution=str(self.source),relationship_confirmed=True))['state'],'missing_conditions')
        entries=self.svc.preview([self.input,dict(self.input,id='two',path=str(other))])['plan']['entries']
        self.assertEqual(entries[1]['state'],'duplicate')

    def test_exclusions_selection_and_failed_form_preserved(self):
        units=[self.input,dict(self.input,id='two',format='unknown',included=False,exclude_reason='지원 형식 아님')]
        v=self.start(units);self.svc.run(v['id']);v=self.svc.get(v['id'])
        self.assertEqual(v['counts']['completed'],1);self.assertEqual(v['counts']['excluded'],1)
        self.assertEqual(v['plan']['excluded'],[{'id':'two','reason':'지원 형식 아님'}])

    def test_stale_source_related_file_and_options(self):
        for role in ('path','solution'):
            with self.subTest(role=role):
                sol=self.root/'sol.md';sol.write_text('### 1. 답\n정답 ①\n')
                unit=dict(self.input,solution=str(sol),relationship_confirmed=True)
                v=self.plan([unit]);Path(unit[role]).write_text(Path(unit[role]).read_text()+'\n변경')
                with self.assertRaises(Conflict):self.act(v,'execute',confirmed_plan_hash=v['plan_hash'])
        pre=self.svc.preview([self.input])
        with self.assertRaises(Conflict):self.svc.act(dict(action='plan',request_id=uuid.uuid4().hex,inputs=[dict(self.input,source='변경')],preview_hash=pre['plan_hash']))

    def test_resend_lost_response_and_two_plans_reserve_same_identity(self):
        a=self.plan();b=self.plan()
        payload=dict(action='execute',plan_id=a['id'],revision=a['revision'],request_id=uuid.uuid4().hex,confirmed_plan_hash=a['plan_hash'])
        first=self.svc.act(payload);self.assertEqual(self.svc.act(payload)['id'],first['id'])
        with self.assertRaises(Conflict):self.act(b,'execute',confirmed_plan_hash=b['plan_hash'])
        self.svc.run(a['id']);self.svc.act(payload);self.svc.run(a['id']);self.assertEqual(len(self.store.list()),1)

    def test_partial_failure_retry_preserves_success_and_attempts(self):
        units=[self.input,dict(self.input,id='two',source='두번째')]
        v=self.start(units);original=self.svc.create
        def create(e,a):
            if e['id']=='two': raise ValueError('합성 파서 실패')
            return original(e,a)
        with patch.object(self.svc,'create',side_effect=create):self.svc.run(v['id'])
        v=self.svc.get(v['id']);jid=v['entries'][0]['job_id'];before=snapshot(self.store.job_dir(jid))
        self.assertEqual(v['counts']['failed'],1)
        v=self.act(v,'retry',entry_ids=['two'],reason='합성 재시도');self.svc.run(v['id']);v=self.svc.get(v['id'])
        self.assertEqual(v['counts']['completed'],2);self.assertEqual(len(v['attempts']),3)
        self.assertEqual(snapshot(self.store.job_dir(jid)),before)

    def test_receipt_save_failure_restart_recovers_exact_completed_job(self):
        v=self.start();save=self.svc.save
        def fail(value):
            if any(e['status']=='completed' for e in value['entries']):raise OSError('receipt failure')
            save(value)
        with patch.object(self.svc,'save',side_effect=fail),self.assertRaises(OSError):self.svc.run(v['id'])
        service=InputJobs(Store(self.store.root));v=service.get(v['id']);self.assertEqual(v['counts']['uncertain'],1)
        self.svc=service;v=self.act(v,'recover');self.assertEqual(v['counts']['completed'],1)
        self.assertEqual(len(self.store.list()),1)

    def test_creation_failure_unknown_never_auto_reingests(self):
        v=self.start()
        with patch.object(self.svc,'create',side_effect=OSError('disk')):self.svc.run(v['id'])
        v=self.svc.get(v['id']);self.assertEqual(v['counts']['uncertain'],1)
        v=self.act(v,'recover');self.assertEqual(v['counts']['uncertain'],1)
        with self.assertRaises(Conflict):self.act(v,'resume')
        v=self.act(v,'end',reason='불명확 보존');self.assertEqual(v['counts']['uncertain'],1)
        p=self.plan()
        with self.assertRaises(Conflict):self.act(p,'execute',confirmed_plan_hash=p['plan_hash'])

    def test_pause_resume_and_concurrent_worker(self):
        entered=threading.Event();release=threading.Event();original=self.svc.create
        def slow(e,a):entered.set();release.wait(5);return original(e,a)
        v=self.start([self.input,dict(self.input,id='two',source='둘째')])
        with patch.object(self.svc,'create',side_effect=slow):
            t=threading.Thread(target=self.svc.run,args=(v['id'],));t.start()
            try:
                self.assertTrue(entered.wait(5));self.svc.run(v['id']);self.act(self.svc.get(v['id']),'pause')
            finally:release.set();t.join(10)
        v=self.svc.get(v['id']);self.assertEqual(v['counts']['completed'],1);self.assertEqual(v['counts']['pending'],1)
        v=self.act(v,'resume');self.svc.run(v['id']);self.assertEqual(self.svc.get(v['id'])['counts']['completed'],2)

    def test_study_both_adapters_existing_namespace_and_batch_exclusions(self):
        vault=self.root/'vault';vault.mkdir()
        for i,(fmt,body) in enumerate([('numbered-qa-v1','\t1. 질문\n\t\t○ 답\n'),('numbered-table-v1','| | | |\n|---|---|---|\n|1|질문|답|\n')]):
            source=vault/f'{i}.md';source.write_text(body)
            u=dict(id=str(i),path=str(source),repo_root=str(vault),format=fmt,subject='철학',question_type='서술',unit='단원')
            v=self.start([u]);self.svc.run(v['id']);v=self.svc.get(v['id'])
            self.assertEqual(v['counts']['completed'],1)
            jid=v['entries'][0]['job_id'];self.assertTrue(jid.startswith('study-'))
            self.assertEqual(self.store.get(jid)['items'][0]['body'],'질문')
            pre=self.svc.batch_preview(v['id'],[str(i)],'audit',BatchJobs(self.store))
            self.assertEqual(pre['plan']['planned_calls'],0);self.assertTrue(pre['plan']['excluded'])
            self.assertEqual(self.svc.preview([u])['plan']['entries'][0]['state'],'existing')
        self.assertEqual(len(self.store.list()),2)

    def test_unreadable_and_missing_files_do_not_mutate(self):
        before=snapshot(self.root)
        original=Path.read_bytes
        def read(p):
            if p.resolve()==self.source.resolve():raise PermissionError('unreadable fixture')
            return original(p)
        with patch.object(Path,'read_bytes',read):self.assertEqual(inspect(self.input)['state'],'missing_conditions')
        self.assertEqual(snapshot(self.root),before)

    def test_incomplete_manifest_is_not_completion_evidence(self):
        v=self.start();self.svc.run(v['id']);v=self.svc.get(v['id']);jid=v['entries'][0]['job_id']
        folder=self.store.job_dir(jid);next((folder/'review').glob('*.json')).unlink()
        with self.assertRaises(ValueError):self.svc.complete_job(jid)
        with self.assertRaises(ValueError):self.svc.batch_preview(v['id'],['one'],'audit',BatchJobs(self.store))

    def test_http_token_explicit_confirmation_and_plan_response(self):
        from fastapi.testclient import TestClient
        from _scripts_2.apps.exam.exam_processor.server import create_app
        c=TestClient(create_app(self.store));token=c.get('/api/config').json()['token'];h={'x-exam-token':token}
        self.assertEqual(c.post('/api/input-plans/preview',json={'inputs':[]}).status_code,403)
        self.assertEqual(c.get('/api/input-formats').status_code,200)
        pre=c.post('/api/input-plans/preview',json={'inputs':[self.input]},headers=h).json()
        p=dict(action='plan',request_id=uuid.uuid4().hex,inputs=[self.input],preview_hash=pre['plan_hash'])
        v=c.post('/api/input-plans',json=p,headers=h).json()
        self.assertEqual(c.post('/api/input-plans',json=p,headers=h).json()['id'],v['id'])
        self.assertEqual(c.post('/api/input-plans',json=dict(action='execute',plan_id=v['id'],revision=v['revision'],request_id=uuid.uuid4().hex),headers=h).status_code,409)

    def test_supported_pdf_range_identity_and_batch_execution(self):
        from _scripts_2.apps.exam.exam_processor.tests.input_jobs_fixture import source_pdf, processor
        path=self.root/'source.pdf';source_pdf(path)
        u=dict(id='pdf',path=str(path),format='kice_korean',source='Synthetic PDF',track='화법과 작문')
        self.assertNotEqual(inspect(u)['identity'],inspect(dict(u,pages='1'))['identity'])
        self.assertEqual(inspect(dict(u,pages='2'))['state'],'missing_conditions')
        v=self.start([u]);self.svc.run(v['id']);v=self.svc.get(v['id'])
        self.assertEqual(v['counts']['completed'],1)
        b=BatchJobs(self.store,processor);pre=self.svc.batch_preview(v['id'],['pdf'],'audit',b)
        self.assertEqual(pre['plan']['planned_calls'],2)
        bv=b.act(dict(action='plan',request_id=uuid.uuid4().hex,job_ids=pre['plan']['job_ids'],operation='audit',preview_hash=pre['plan_hash']))
        self.assertEqual(bv['attempts'],[])
        b.act(dict(action='execute',request_id=uuid.uuid4().hex,batch_id=bv['id'],revision=bv['revision'],confirmed_plan_hash=bv['plan_hash']))
        b.run(bv['id']);self.assertEqual(b.get(bv['id'])['counts']['completed'],2)
        self.assertTrue(all(i['review']=='pending' for i in self.store.get(v['entries'][0]['job_id'])['items']))

    def test_hanwangi_existing_ingestion_and_changed_layout(self):
        from _scripts_2.apps.exam.exam_processor.tests.test_workbook import WorkbookTests
        fixture=WorkbookTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        structure=fixture.confirmed();layout=fixture.layout()
        sp=fixture.root/'structure.json';lp=fixture.root/'layout.json'
        sp.write_text(json.dumps(structure));lp.write_text(json.dumps(layout))
        u=dict(id='math',path=str(fixture.root/'problem.pdf'),solution=str(fixture.root/'solution.pdf'),
               format='hanwangi_2026_probability',source='Synthetic workbook',track='확률과 통계',
               structure=str(sp),layout=str(lp),relationship_confirmed=True)
        self.assertEqual(inspect(u)['state'],'ready')
        v=self.start([u]);self.svc.run(v['id']);self.assertEqual(self.svc.get(v['id'])['counts']['completed'],1)
        # A distinct confirmed layout remains a distinct input; source hash binding is enforced.
        layout['evidence']='explicit second fixture';lp.write_text(json.dumps(layout))
        self.assertNotEqual(inspect(u)['identity'],v['entries'][0]['identity'])
        structure['documents']['problem']['sha256']='0'*64;sp.write_text(json.dumps(structure))
        self.assertEqual(inspect(u)['state'],'missing_conditions')

    def test_existing_legacy_is_reference_only(self):
        old=self.store.import_file(self.input['path'],self.input['source'],self.input['track'],format_id='korean_notes')
        folder=self.store.job_dir(old['id']);m=json.loads((folder/'job.json').read_text());m['storage_version']=1
        (folder/'job.json').write_text(json.dumps(m));before=snapshot(folder)
        pre=self.svc.preview([self.input]);e=pre['plan']['entries'][0]
        self.assertEqual(e['state'],'existing');self.assertTrue(e['existing_jobs'][0]['legacy'])
        with self.assertRaises(ValueError):self.plan()
        self.assertEqual(snapshot(folder),before)

    def test_unpublished_partial_save_never_visible_and_no_completion_guess(self):
        v=self.start()
        from _scripts_2.apps.exam.exam_processor.storage.record_repository import RecordRepository
        def partial(repo, folder, records):
            repo.save(folder, records[0])
            raise OSError('partial storage failure after first canonical record')
        with patch.object(RecordRepository,'save_many',partial):self.svc.run(v['id'])
        self.assertEqual(self.store.list(),[])
        v=self.svc.get(v['id']);self.assertEqual(v['counts']['uncertain'],1)
        self.assertEqual(self.act(v,'recover')['counts']['uncertain'],1)

    def test_plan_save_failure_and_same_request_retry(self):
        pre=self.svc.preview([self.input]);payload=dict(action='plan',request_id=uuid.uuid4().hex,inputs=[self.input],preview_hash=pre['plan_hash'])
        with patch.object(self.svc,'save',side_effect=OSError('disk')),self.assertRaises(OSError):self.svc.act(payload)
        self.assertEqual(self.svc.list(),[]);v=self.svc.act(payload)
        self.assertEqual(v['plan'],pre['plan']);self.assertEqual(self.store.list(),[])

    def test_english_role_files_and_header_conditions(self):
        from types import SimpleNamespace
        from _scripts_2.apps.exam.exam_processor.tests.input_jobs_fixture import source_pdf
        path=self.root/'english.pdf';source_pdf(path)
        script=self.root/'script.pdf';source_pdf(script)
        answer=self.root/'answer.png';answer.write_bytes(b'synthetic answer bytes')
        class PDF:
            pages=[SimpleNamespace(extract_text=lambda:'2027학년도 9월 영어')]
            def __enter__(self):return self
            def __exit__(self,*args):pass
        u=dict(id='english',path=str(path),format='kice_english',source='2027학년도 9월',track='영어',
               script=str(script),answer=str(answer),relationship_confirmed=True)
        with patch('pdfplumber.open',return_value=PDF()):
            e=inspect(u);self.assertEqual(e['state'],'ready')
            self.assertEqual(set(e['evidence']),{'path','script','answer'})
            self.assertEqual(inspect(dict(u,source='2026학년도 수능'))['state'],'missing_conditions')
            answer.write_bytes(b'different answer')
            self.assertNotEqual(inspect(u)['identity'],e['identity'])
            self.assertEqual(inspect(dict(u,relationship_confirmed=False))['state'],'missing_conditions')

    def test_retry_selection_does_not_run_remaining_pending(self):
        v=self.start([self.input,dict(self.input,id='two',source='second')])
        # Durable checkpoint corresponding to a failed first input and stopped second input.
        raw=self.svc.read(v['id']);raw['status']='paused';raw['entries'][0]['status']='failed';self.svc.save(raw)
        v=self.act(self.svc.get(v['id']),'retry',entry_ids=['one'],reason='선택 실패만')
        self.svc.run(v['id']);v=self.svc.get(v['id'])
        self.assertEqual(v['counts']['completed'],1);self.assertEqual(v['counts']['pending'],1)
        self.assertEqual(v['status'],'paused')

    def test_running_source_change_excludes_only_changed_input(self):
        v=self.start();self.source.write_text(self.source.read_text()+'changed')
        self.svc.run(v['id']);v=self.svc.get(v['id'])
        self.assertEqual(v['counts']['stale'],1);self.assertEqual(self.store.list(),[])
