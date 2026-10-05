"""Durable input plans and creation receipts. No AI, approval or content mutation."""
import copy
import fcntl
import json
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..ingestion.input_plan import POLICY, STUDY, inspect
from ..ingestion.study_service import StudyIngestion
from .batch_jobs import valid_id
from .evaluation import now
from .json_io import atomic_json_save
from .sample_review import digest
from .store import Store, Conflict


class InputJobs:
    def __init__(self, store):
        self.store = store
        self.root = store.root / 'input-plans'

    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.store.lock, (self.root / '.lock').open('a+b') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def path(self, pid): return self.root / (valid_id(pid)+'.json')

    def read(self, pid):
        v = json.loads(self.path(pid).read_text())
        if v.get('integrity') != digest({k:x for k,x in v.items() if k!='integrity'}):
            raise Conflict('입력 생성 기록이 손상되었습니다. 덮어쓰지 않습니다.')
        return v

    def save(self, v):
        v['revision'] = uuid.uuid4().hex
        v['integrity'] = digest({k:x for k,x in v.items() if k!='integrity'})
        atomic_json_save(self.path(v['id']), v)

    def live(self, pid):
        p = self.path(pid).with_suffix('.worker')
        if not p.exists(): return False
        with p.open('rb') as f:
            try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return True
        return False

    def get(self, pid):
        v = self.read(pid)
        v['worker_live'] = self.live(pid)
        if not v['worker_live']:
            if v['status']=='running': v['status']='interrupted'
            for e in v['entries']:
                if e['status']=='running': e['status']='uncertain'
            for a in v['attempts']:
                if a['status']=='running': a['status']='uncertain'
        v['counts'] = {s:sum(e['status']==s for e in v['entries']) for s in
                       ('pending','running','completed','failed','uncertain','excluded','stale','abandoned')}
        return v

    def list(self): return [self.get(p.stem) for p in sorted(self.root.glob('*.json'))]

    def evidence_matches(self, e):
        fresh = inspect(e['input'])
        return fresh['identity']==e['identity'] and fresh['evidence']==e['evidence'] and fresh['state']=='ready'

    def existing(self, entry):
        matches = []
        legacy_id = None
        v = entry['input']
        if entry['state'] == 'ready' and v['format'] not in STUDY:
            from ..domain.models import identity
            from ..ingestion.formats import FORMATS
            hashes = {k:x['sha256'] for k,x in entry['evidence'].items()}
            sid = identity('source',v['source'],v['track'],hashes['path']) if v['format']=='kice_english' else identity('source',v['source'],v['track'])
            legacy_id = identity(sid,hashes['path'],hashes.get('solution',''),v['pages'],FORMATS[v['format']].parser_version)
            if v['format']=='kice_english': legacy_id=identity(legacy_id,v['format'],hashes.get('script',''),hashes.get('answer',''))
            if v['format']=='hanwangi_2026_probability':
                from ..pipeline.workbook import read_structure
                legacy_id=identity(legacy_id,v['format'],read_structure(v['structure']),json.loads(Path(v['layout']).read_text()))
        for base in (self.store.root/'jobs', self.store.root/'ingestion'/'jobs'):
            for p in base.glob('*/job.json'):
                m = json.loads(p.read_text())
                receipt = m.get('input_creation', {})
                if receipt.get('identity')==entry['identity'] or (p.parent.name==legacy_id and m.get('format')==v['format']):
                    matches.append({'job_id':p.parent.name,'basis':'input_identity',
                                    'legacy':m.get('storage_version',1)<2})
                elif m.get('input_path')==entry['input']['path']:
                    matches.append({'job_id':p.parent.name,'basis':'path_only_unverified',
                                    'legacy':m.get('storage_version',1)<2})
        return matches

    def preview(self, inputs):
        if not isinstance(inputs,list) or len(inputs)>100: raise ValueError('입력은 최대 100개입니다.')
        entries = [inspect(x) for x in inputs]
        if len({e['id'] for e in entries})!=len(entries): raise ValueError('입력 단위 ID가 중복됩니다.')
        seen = set()
        for e in entries:
            e['existing_jobs'] = self.existing(e)
            if e['state']=='ready' and e['identity'] in seen:
                e['state']='duplicate'; e['reasons'].append('같은 내용·역할·범위·옵션의 입력이 앞에 있습니다. 하나만 선택하세요.')
            if e['included']: seen.add(e['identity'])
            if e['state']=='ready' and any(x['basis']=='input_identity' for x in e['existing_jobs']):
                e['state']='existing'; e['reasons'].append('이미 생성된 동일 입력입니다. 신규 생성 대신 기존 작업을 여세요.')
            if e['input']['format'] in STUDY and e['state']=='ready':
                try:
                    service=StudyIngestion(Path(e['input']['repo_root']),self.store.root)
                    service.artifacts.validate_integrity()
                    a=service.artifacts.get_by_path(Path(e['input']['path']).relative_to(service.repo_root).as_posix())
                    if a:
                        jid='study-'+uuid.uuid5(uuid.NAMESPACE_URL,a.artifact_id).hex
                        if self.store.job_dir(jid).exists():
                            e['state']='existing';e['existing_jobs'].append({'job_id':jid,'basis':'study_artifact','legacy':False})
                            e['reasons'].append('기존 공부 draft입니다. 내용/옵션 변경은 별도 명시 revision 절차를 사용하세요.')
                except (ValueError,OSError) as exc:
                    e['state']='missing_conditions';e['reasons'].append(str(exc))
        plan={'schema':'input-plan/1','policy':POLICY,'entries':entries,
              'included':[e['id'] for e in entries if e['included']],
              'excluded':[{'id':e['id'],'reason':e['input']['exclude_reason']} for e in entries if not e['included']]}
        return {'plan':plan,'plan_hash':digest(plan)}

    def complete_job(self, jid, receipt=None):
        folder=self.store.job_dir(jid)
        m=self.store.jobs.load_manifest(folder)
        if m.storage_version!=2 or not m.record_ids or len(set(m.record_ids))!=len(m.record_ids):
            raise ValueError('완성된 canonical manifest가 없습니다.')
        if receipt is not None and m.metadata.get('input_creation')!=receipt:
            raise Conflict('이 요청의 생성 영수증이 아닙니다.')
        for rid in m.record_ids:
            record=self.store.records.get(folder,rid)
            review=self.store.reviews.get(folder,rid)
            if review.applies_to_revision_id!=record.provenance.revision_id:
                raise ValueError('canonical/review 완료 근거가 일치하지 않습니다.')
            names = ['regions/'+r['image'] for r in record.provenance.source_regions if r.get('image')]
            names += [a.path for a in record.assets]
            for name in names:
                path = (folder/name).resolve()
                if not path.is_relative_to(folder.resolve()) or not path.is_file():
                    raise ValueError('완료에 필요한 원본 영역/에셋이 없습니다.')
        for name in m.documents.values():
            if not (folder/name).is_file(): raise ValueError('원본 사본이 없습니다.')
        return m

    def reserved(self, identity, owner):
        for p in self.root.glob('*.json'):
            v=self.read(p.stem)
            for e in v['entries']:
                if e['identity']==identity and v['id']!=owner and e['status'] in ('pending','running','uncertain','completed'):
                    if v['status']!='planned' or e['status']!='pending':
                        raise Conflict('같은 입력이 다른 생성 기록에 예약되었거나 완료/불명확 상태입니다.')

    def act(self,p):
        req=p.get('request_id')
        if not isinstance(req,str) or not 8<=len(req)<=128: raise ValueError('요청 ID가 필요합니다.')
        with self.lock():
            action=p.get('action')
            if action=='plan':
                pid=digest(['input-plan',req])[:32]
                if self.path(pid).exists():
                    v=self.read(pid)
                    if v['requests'].get(req)!=digest(p): raise Conflict('같은 요청 ID의 내용이 다릅니다.')
                    return self.get(pid)
                preview=self.preview(p.get('inputs'))
                if p.get('preview_hash')!=preview['plan_hash']: raise Conflict('입력이 바뀌었습니다. 미리보기를 다시 확인하세요.')
                entries=preview['plan']['entries']
                if any(e['included'] and e['state']!='ready' for e in entries): raise ValueError('조건이 충족된 신규 입력만 포함하세요.')
                if any(not e['included'] and not e['input']['exclude_reason'] for e in entries): raise ValueError('제외 사유가 필요합니다.')
                v={'id':pid,'created_at':now(),**preview,'status':'planned','stop_requested':False,
                   'entries':[dict(copy.deepcopy(e),status='pending' if e['included'] else 'excluded',job_id=None) for e in entries],
                   'attempts':[],'events':[],'requests':{}}
            else:
                v=self.read(p.get('plan_id'))
                if req in v['requests']:
                    if v['requests'][req]!=digest(p): raise Conflict('같은 요청 ID의 내용이 다릅니다.')
                    return self.get(v['id'])
                if action!='pause' and p.get('revision')!=v['revision']: raise Conflict('다른 창에서 기록이 바뀌었습니다. 재조회하세요.')
                if v['plan']['policy']!=POLICY or v['status']=='ended': raise Conflict('읽기 전용 기록입니다.')
                if action=='pause':
                    if v['status'] not in ('running','paused'): raise Conflict('실행 중인 계획이 아닙니다.')
                    v['stop_requested']=True
                elif action in ('recover','resume','retry','end'):
                    if self.live(v['id']): raise Conflict('현재 입력의 생성 완료를 기다리세요.')
                    if v['status']=='planned': raise Conflict('먼저 명시적으로 생성을 시작하세요.')
                    self.recover(v)
                    if action=='recover': v['status']='paused'
                    elif action=='end':
                        if not p.get('reason','').strip(): raise ValueError('종료 사유가 필요합니다.')
                        v['status']='ended'
                        for e in v['entries']:
                            if e['status']=='pending': e['status']='abandoned'
                    else:
                        if any(e['status']=='uncertain' for e in v['entries']): raise Conflict('결과 불명확 입력이 있습니다. 자동 재생성하지 않습니다.')
                        ids=[e['id'] for e in v['entries'] if e['status']=='pending']
                        if action=='retry':
                            ids=p.get('entry_ids')
                            if not isinstance(ids,list) or not ids or len(set(ids))!=len(ids) or not p.get('reason','').strip(): raise ValueError('실패 대상과 재시도 사유가 필요합니다.')
                            if set(ids)-{e['id'] for e in v['entries'] if e['status']=='failed'}: raise Conflict('확인된 실패만 재시도할 수 있습니다.')
                        self.prepare(v,ids)
                elif action=='execute':
                    if v['status']!='planned' or p.get('confirmed_plan_hash')!=v['plan_hash']: raise Conflict('저장된 계획의 명시 확인이 필요합니다.')
                    # Recheck every fixed input, including excluded evidence. Never silently change selection.
                    fresh=self.preview([e['input'] for e in v['plan']['entries']])
                    if fresh['plan_hash']!=v['plan_hash']: raise Conflict('원본·관련 파일·조건/기존 작업이 변경된 오래된 계획입니다.')
                    self.prepare(v,[e['id'] for e in v['entries'] if e['status']=='pending'])
                else: raise ValueError('지원하지 않는 입력 계획 동작입니다.')
            v['events'].append({'action':action,'at':now(),'request_id':req,'reason':p.get('reason')})
            v['requests'][req]=digest(p)
            self.save(v)
            return self.get(v['id'])

    def prepare(self,v,ids):
        if not ids: raise ValueError('생성 대상이 없습니다.')
        for e in v['entries']:
            if e['id'] in ids:
                if not self.evidence_matches(e): raise Conflict('원본 또는 조건이 변경되었습니다. 새 계획을 확인하세요.')
                self.reserved(e['identity'],v['id'])
                if self.existing(e) and any(x['basis']=='input_identity' for x in self.existing(e)): raise Conflict('동일 입력의 기존 작업을 확인하세요.')
                e['status']='pending'
        v.update(status='running',stop_requested=False,run_entry_ids=ids)

    def recover(self,v):
        for e in v['entries']:
            if e['status'] not in ('running','uncertain'): continue
            a=next(a for a in v['attempts'] if a['id']==e['attempt_id'])
            jid=a.get('job_id')
            # Study IDs are resolved by the service; find only an exact embedded receipt.
            if not jid:
                for path in (self.store.root/'ingestion'/'jobs').glob('*/job.json'):
                    if json.loads(path.read_text()).get('input_creation')==a['receipt']: jid=path.parent.name
            try:
                if not jid: raise ValueError('요청에 대응하는 완료 job 없음')
                self.complete_job(jid,a['receipt'])
                e.update(status='completed',job_id=jid,message='요청 영수증과 canonical 완료 근거로 복구')
            except (ValueError,OSError): e.update(status='uncertain',message='요청의 완료 근거를 확인할 수 없습니다. 자동 재인입 금지')
            a.update(status=e['status'],finished_at=now(),job_id=jid)

    def create(self,e,a):
        v=e['input']; receipt=a['receipt']
        if v['format'] in STUDY:
            svc=StudyIngestion(Path(v['repo_root']),self.store.root)
            folder=svc.ingest(Path(v['path']),adapter=v['format'],subject=v['subject'],
                              unit=[x.strip() for x in v['unit'].split('/') if x.strip()],
                              question_type=v['question_type'],creation_receipt=receipt)
            self.complete_job(folder.name,receipt)
            return folder.name
        # Existing importer writes in an unpublished attempt workspace. Publication is one rename.
        staging=self.root/'staging'/a['id']
        staged=Store(staging, self.store.output)
        job=staged.import_file(v['path'],v['source'],v['track'],v['pages'],v['solution'],
                               format_id=v['format'],structure=v['structure'],layout=v['layout'],
                               script=v['script'],answer=v['answer'],creation_receipt=receipt)
        manifest=InputJobs(staged).complete_job(job['id'],receipt)
        import hashlib
        for role, name in manifest.documents.items():
            key='path' if role=='problem' else role
            if key in e['evidence'] and hashlib.sha256((staged.job_dir(job['id'])/name).read_bytes()).hexdigest()!=e['evidence'][key]['sha256']:
                raise Conflict('복사된 원본이 계획 근거와 다릅니다.')
        if not self.evidence_matches(e): raise Conflict('생성 중 원본이 변경되었습니다. 결과를 공개하지 않습니다.')
        target=self.store.job_dir(job['id'])
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists(): raise Conflict('기존 job 경로를 덮어쓰지 않습니다.')
        staged.job_dir(job['id']).rename(target)
        self.complete_job(job['id'],receipt)
        return job['id']

    def run(self,pid):
        with self.path(pid).with_suffix('.worker').open('a+b') as lease:
            try: fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: return
            while True:
                with self.lock():
                    v=self.read(pid)
                    if v['status']!='running' or any(e['status']=='running' for e in v['entries']): return
                    if v['stop_requested']: v['status']='paused';self.save(v);return
                    e=next((e for e in v['entries'] if e['status']=='pending' and e['id'] in v['run_entry_ids']),None)
                    if not e:
                        v['status']='paused' if any(e['status']=='pending' for e in v['entries']) else 'finished'
                        self.save(v);return
                    if not self.evidence_matches(e):
                        e.update(status='stale',message='원본·관련 파일이 바뀌었습니다. 새 계획 필요');self.save(v);continue
                    aid=uuid.uuid4().hex
                    jid=None if e['input']['format'] in STUDY else digest(['input-job',e['identity']])[:32]
                    receipt={'plan_id':pid,'entry_id':e['id'],'attempt_id':aid,'identity':e['identity'],'job_id':jid,'source_sha256':e['evidence']['path']['sha256'],'request_id':v['events'][-1]['request_id']}
                    a={'id':aid,'entry_id':e['id'],'job_id':jid,'receipt':receipt,'status':'running','started_at':now()}
                    v['attempts'].append(a);e.update(status='running',attempt_id=aid)
                    self.save(v)
                try:
                    jid=self.create(e,a); status,message='completed','job 준비 완료 · 생성 단계에서 AI/검수/승인/출력을 수행하지 않음'
                except Exception as exc:
                    # A parser validation failure is retryable only when no completed job was published.
                    status='failed' if isinstance(exc,ValueError) and not (jid and (self.store.job_dir(jid)/'job.json').exists()) and e['input']['format'] not in STUDY else 'uncertain'
                    message=str(exc) if isinstance(exc,ValueError) else '생성 또는 저장 완료 불명확. 명시 복구로 완료 근거 확인 필요'
                with self.lock():
                    v=self.read(pid)
                    target=next(x for x in v['entries'] if x['id']==e['id'])
                    target.update(status=status,message=message,job_id=jid if status=='completed' else None)
                    attempt=next(x for x in v['attempts'] if x['id']==aid)
                    attempt.update(status=status,message=message,finished_at=now(),job_id=jid)
                    self.save(v)

    def batch_preview(self,pid,entry_ids,operation,batches):
        v=self.get(pid)
        if not isinstance(entry_ids,list) or not entry_ids or len(set(entry_ids))!=len(entry_ids): raise ValueError('넘길 완료 job을 명시적으로 선택하세요.')
        selected=[e for e in v['entries'] if e['id'] in entry_ids and e['status']=='completed']
        if len(selected)!=len(entry_ids): raise Conflict('생성 완료한 job만 넘길 수 있습니다.')
        for e in selected: self.complete_job(e['job_id'])
        return batches.preview([e['job_id'] for e in selected],operation)
