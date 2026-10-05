"""Explicit multi-job plans. Content remains owned by WorkQueue's processor.

All state transitions share a stable file lock. A separate worker lease provides
positive evidence of a live executor; persisted 'running' alone does not.
"""
import copy
import fcntl
import hashlib
import json
import re
import uuid
from contextlib import contextmanager

from .evaluation import now
from .json_io import atomic_json_save
from .sample_review import digest
from .store import Conflict
from ..pipeline.work_queue import WorkQueue

POLICY = 'multi-job-batch/1'
ACTIVE = ('running', 'paused', 'interrupted')


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
        raise ValueError('잘못된 배치 ID입니다.')
    return value


@contextmanager
def execution_lock(store):
    folder = store.root / 'batches'
    folder.mkdir(exist_ok=True)
    with store.lock, (folder / '.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def ensure_job_unclaimed(store, job_id, owner=None):
    for path in (store.root / 'batches').glob('*.json'):
        value = BatchJobs(store)._read(path.stem)
        if value['id'] != owner and value['status'] in ACTIVE and job_id in value['plan']['job_ids']:
            raise Conflict('이 job은 배치 실행에 예약되어 있습니다. 배치를 종료한 뒤 개별 작업을 실행하세요.')
        if value['id'] != owner:
            for entry in value['entries']:
                if entry['job_id'] != job_id or entry['status'] not in ('running', 'uncertain'):
                    continue
                record = store.records.get(store.job_dir(job_id), entry['item'])
                if record and record.provenance.revision_id == entry['basis']['revision']:
                    raise Conflict('이 job의 현재 revision에 결과 불명확 호출이 있습니다. 배치를 종료해도 자동 재호출할 수 없습니다.')


def ensure_no_job_worker(store, job_id):
    """Read-only lease probe; callers serialize dispatch decisions separately."""
    path = store.root / 'batches' / ('.job-' + job_id + '.worker')
    if not path.exists():
        return
    with path.open('rb') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Conflict('이 job의 worker가 다른 실행에서 동작 중입니다.')


def job_worker_lease(store, job_id):
    """Acquire under execution_lock; caller closes on setup failure or worker exit."""
    store.job_dir(job_id)  # existing path validation
    handle=(store.root/'batches'/('.job-'+job_id+'.worker')).open('a+b')
    try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close();raise Conflict('이 job의 worker가 다른 실행에서 동작 중입니다.')
    return handle


def entry_basis(store, job_id, item_id, section):
    folder = store.job_dir(job_id)
    pending, records, reviews, files = [item_id], {}, {}, {}
    while pending:
        rid = pending.pop(0)
        if rid in records: continue
        rec = store.records.get(folder, rid)
        rev = store.reviews.get(folder, rid)
        if not rec or not rev: raise ValueError('문항/검수/의존성 근거가 없습니다.')
        records[rid] = rec.to_dict()
        reviews[rid] = rev.to_dict()
        pending.extend(rec.relations.passage_ids + rec.relations.listening_script_ids)
        regions = rec.provenance.source_regions
        if rid == item_id and section:
            regions = [r for r in regions if r.get('content_role') == section]
        if not regions: raise ValueError('원본 영역이 없습니다.')
        names = ['regions/' + r.get('image', '') for r in regions]
        names += [a.path for a in rec.assets if not section or a.section == section]
        for name in names:
            path = (folder / name).resolve()
            if not name or not path.is_relative_to(folder.resolve()) or not path.is_file():
                raise ValueError('원본/에셋 파일 근거가 없습니다.')
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = json.loads((folder / 'job.json').read_text())
    from ..domain.job_manifest import RUNTIME_KEYS
    manifest = {k: v for k, v in manifest.items() if k not in RUNTIME_KEYS and k != 'last_export'}
    return {'records': {k: digest(v) for k, v in records.items()},
            'reviews': {k: digest(v) for k, v in reviews.items()}, 'files': files,
            'revision': records[item_id]['provenance']['revision_id'], 'manifest': digest(manifest)}


class BatchJobs:
    def __init__(self, store, processor=None, busy=None):
        self.store = store
        self.queue = WorkQueue(store) if processor is None else WorkQueue(store, processor)
        self.busy = busy if busy is not None else set()

    def _path(self, bid): return self.store.root / 'batches' / (valid_id(bid) + '.json')

    def _read(self, bid):
        path = self._path(bid)
        if not path.is_file(): raise ValueError('배치를 찾을 수 없습니다.')
        value = json.loads(path.read_text())
        if value.get('integrity') != digest({k: v for k, v in value.items() if k != 'integrity'}):
            raise ValueError('배치 기록 무결성 오류. 기존 파일을 보존하세요.')
        return value

    def _save(self, value):
        value['revision'] = uuid.uuid4().hex
        value['integrity'] = digest({k: v for k, v in value.items() if k != 'integrity'})
        atomic_json_save(self._path(value['id']), value)

    def live(self, bid):
        path = self._path(bid).with_suffix('.worker')
        if not path.exists(): return False
        with path.open('rb') as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return True
        return False

    def get(self, bid):
        with self.store.lock:
            value = self._read(bid)
            live = self.live(bid)
            value['worker_live'] = live
            if not live:
                if value['status'] == 'running':value['status'] = 'interrupted'
                for entry in value['entries']:
                    if entry['status'] == 'running': entry['status'] = 'uncertain'
                for attempt in value['attempts']:
                    if attempt['status'] == 'running': attempt['status'] = 'uncertain'
            value['counts'] = {s: sum(e['status'] == s for e in value['entries']) for s in
                               ('pending','running','completed','failed','skipped','uncertain','abandoned')}
            return value

    def list(self):
        return [self.get(p.stem) for p in sorted((self.store.root / 'batches').glob('*.json'))]

    def preview(self, job_ids, operation):
        if operation not in ('extract', 'audit') or not isinstance(job_ids, list) or not job_ids or len(job_ids) > 100:
            raise ValueError('1~100개 job과 처리 단계를 선택하세요.')
        if any(not isinstance(j, str) for j in job_ids) or len(set(job_ids)) != len(job_ids):
            raise ValueError('중복 없는 job 목록이 필요합니다.')
        with self.store.lock:
            entries, excluded = [], []
            for jid in job_ids:
                path = self.store.job_dir(jid) / 'job.json'
                if not path.is_file():
                    excluded.append({'job_id':jid,'item':None,'reasons':['missing_job']});continue
                raw = json.loads(path.read_text())
                reason = None
                if raw.get('storage_version', 1) < 2 or 'items' in raw: reason = 'legacy_read_only'
                elif jid in self.busy: reason = 'job_busy'
                else:
                    try:
                        ensure_job_unclaimed(self.store, jid)
                        ensure_no_job_worker(self.store, jid)
                    except Conflict: reason = 'batch_reserved'
                runtime = self.store.runtime.load(self.store.job_dir(jid)) if not reason else None
                if runtime and runtime.queue and any(e['status'] in ('pending','running','uncertain') for e in runtime.queue['entries']):
                    reason = 'unfinished_job_queue'
                if runtime and (runtime.task or {}).get('status') in ('running','uncertain'): reason = 'unfinished_single_task'
                if reason:
                    excluded.append({'job_id': jid, 'item': None, 'reasons': [reason]}); continue
                candidates = self.queue.preview(jid, operation)
                excluded += [dict(e, job_id=jid) for e in candidates['excluded']]
                for e in candidates['entries']:
                    try: basis = entry_basis(self.store, jid, e['item'], e.get('section'))
                    except ValueError as exc:
                        excluded.append(dict(job_id=jid,item=e['item'],reasons=['missing_evidence'],message=str(exc)));continue
                    entries.append(dict(e, job_id=jid, basis=basis, id=digest([jid,e['item'],e.get('section')])[:32]))
            plan = {'policy': POLICY, 'operation': operation, 'job_ids': list(job_ids), 'entries': entries,
                    'excluded': excluded, 'planned_calls': len(entries), 'cost': None}
            plan_hash = digest(plan)
            return {'plan': plan, 'plan_hash': plan_hash, 'display': self.preview_display(plan, plan_hash)}

    def preview_display(self, plan, plan_hash):
        """Ephemeral labels only; never part of a saved plan or execution basis."""
        display = {'plan_hash': plan_hash, 'jobs': {}, 'items': {}}
        for jid in plan['job_ids']:
            try:
                folder = self.store.job_dir(jid)
                raw = json.loads((folder / 'job.json').read_text())
                display['jobs'][jid] = raw.get('source') or jid
                if raw.get('storage_version', 1) < 2 or 'items' in raw:
                    continue
                entries = [e for e in plan['entries'] + plan['excluded'] if e['job_id'] == jid and e.get('item')]
                for e in entries:
                    rid = e['item']
                    rec = self.store.records.get(folder, rid)
                    rev = self.store.reviews.get(folder, rid)
                    if not rec:
                        continue
                    detail = []
                    if 'missing_source_or_existing_transcription' in e.get('reasons', []) and rev:
                        if not any(r.get('content_role') == 'body' for r in rec.provenance.source_regions):
                            detail.append('본문 원본 영역 없음')
                        if rev.extracted_revision:
                            detail.append('기존 전사 revision 기록 있음')
                        if rev.history:
                            detail.append('기존 편집/처리 이력으로 일괄 전사 제외')
                    display['items'][f'{jid}/{rid}'] = {
                        'number': rec.question.question_number, 'kind': rec.question.content_kind,
                        'details': detail,
                    }
            except (OSError, ValueError, KeyError, AttributeError):
                # Optional labels cannot turn a usable plan into a failed preview.
                continue
        return display

    def act(self, p):
        request = p.get('request_id')
        if not isinstance(request, str) or not 8 <= len(request) <= 128: raise ValueError('요청 ID가 필요합니다.')
        with execution_lock(self.store):
            action = p.get('action')
            if action == 'plan':
                bid = digest(['batch',request])[:32]
                if self._path(bid).exists():
                    v=self._read(bid)
                    if v['requests'].get(request) != digest(p):raise Conflict('같은 요청 ID의 내용이 다릅니다.')
                    return self.get(bid)
                preview=self.preview(p.get('job_ids'),p.get('operation'))
                if p.get('preview_hash') != preview['plan_hash']:raise Conflict('대상이 변경되었습니다. 계획을 다시 확인하세요.')
                v={'id':bid,'created_at':now(),'plan':preview['plan'],'plan_hash':preview['plan_hash'],
                   'entries':copy.deepcopy(preview['plan']['entries']),'status':'planned','stop_requested':False,
                   'events':[],'requests':{},'attempts':[]}
            else:
                v=self._read(p.get('batch_id'))
                if request in v['requests']:
                    if v['requests'][request] != digest(p):raise Conflict('같은 요청 ID의 내용이 다릅니다.')
                    return self.get(v['id'])
                # Stop is monotonic and must remain usable while receipts advance revision.
                if action != 'pause' and p.get('revision') != v['revision']:raise Conflict('배치 기록이 변경되었습니다. 재조회하세요.')
                if v['plan']['policy'] != POLICY:raise Conflict('과거 정책의 배치입니다.')
                if v['status'] == 'ended':raise Conflict('종료된 배치입니다. 기록은 읽기 전용입니다.')
                live=self.live(v['id'])
                if action == 'execute':
                    if v['status'] != 'planned' or p.get('confirmed_plan_hash') != v['plan_hash']:raise Conflict('저장한 계획의 명시 확인이 필요합니다.')
                    current=self.preview(v['plan']['job_ids'],v['plan']['operation'])
                    if current['plan_hash'] != v['plan_hash']:raise Conflict('오래된 계획입니다. 새 계획을 만드세요.')
                    if not v['entries']:raise ValueError('실행 가능한 대상이 없습니다.')
                    v['status']='running'
                    v['run_entry_ids']=[e['id'] for e in v['entries'] if e['status']=='pending']
                elif action == 'pause':
                    if v['status'] not in ACTIVE:raise Conflict('실행 중인 배치가 아닙니다.')
                    v['stop_requested']=True
                    if not live:v['status']='paused'
                elif action in ('recover','resume','retry','end'):
                    if live:raise Conflict('worker가 아직 실행 중입니다. 중단 후 종료를 기다리세요.')
                    if v['status'] == 'planned':raise Conflict('먼저 명시적으로 실행하세요.')
                    for e in v['entries']:
                        if e['status']=='running':e.update(status='uncertain',message='호출/저장 완료를 확인할 수 없습니다. 자동 재호출하지 않습니다.')
                    for attempt in v['attempts']:
                        if attempt['status']=='running':attempt.update(status='uncertain',message='완료 영수증 없음; 자동 재호출 금지')
                    if action == 'recover':v['status']='paused'
                    elif action == 'end':
                        reason=p.get('reason')
                        if not isinstance(reason,str) or not reason.strip():raise ValueError('종료 사유가 필요합니다.')
                        # Keep uncertainty in the terminal record, never reclassify it as success.
                        v['status']='ended'
                        for e in v['entries']:
                            if e['status'] == 'pending':e.update(status='abandoned',message='명시적 배치 종료로 미실행')
                    else:
                        if v['status']=='ended':raise Conflict('종료된 배치입니다.')
                        for jid in v['plan']['job_ids']:
                            ensure_job_unclaimed(self.store,jid,v['id'])
                            ensure_no_job_worker(self.store,jid)
                            runtime = self.store.runtime.load(self.store.job_dir(jid))
                            if (runtime.task or {}).get('status') in ('running','uncertain') or any(e['status'] in ('pending','running','uncertain') for e in (runtime.queue or {}).get('entries',[])):
                                raise Conflict('미완료 개별 실행이 있습니다. 새 계획을 확인하세요.')
                            if jid in self.busy:raise Conflict('job이 실행 중입니다.')
                        if any(e['status']=='uncertain' for e in v['entries']):raise Conflict('결과 불명확 항목을 보존하고 배치를 종료하세요. 자동 재시도하지 않습니다.')
                        if action=='retry':
                            ids=p.get('entry_ids');reason=p.get('reason')
                            if not isinstance(ids,list) or not ids or len(set(ids))!=len(ids) or not isinstance(reason,str) or not reason.strip():raise ValueError('실패 대상과 재시도 사유를 명시하세요.')
                            if set(ids)-{e['id'] for e in v['entries'] if e['status']=='failed'}:raise Conflict('실패 항목만 재시도할 수 있습니다.')
                            for e in v['entries']:
                                if e['id'] in ids:e['status']='pending'
                            v['run_entry_ids']=list(ids)
                        else:
                            v['run_entry_ids']=[e['id'] for e in v['entries'] if e['status']=='pending']
                        if not any(e['status']=='pending' for e in v['entries']):raise ValueError('미실행 대상이 없습니다.')
                        v.update(status='running',stop_requested=False)
                else:raise ValueError('지원하지 않는 배치 동작입니다.')
            v['events'].append({'action':action,'at':now(),'request_id':request,'reason':p.get('reason')})
            v['requests'][request]=digest(p);self._save(v)
            return self.get(v['id'])

    def run(self, bid):
        """Caller starts explicitly; a non-blocking OS lease prevents duplicate workers."""
        with self._path(bid).with_suffix('.worker').open('a+b') as lease:
            try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return
            while True:
                with execution_lock(self.store):
                    v=self._read(bid)
                    if v['status']!='running':return
                    if any(e['status']=='running' for e in v['entries']):
                        # A newly acquired lease cannot prove a prior call completed.
                        # The explicit recover action projects/records uncertainty.
                        return
                    if v['stop_requested']:
                        v['status']='paused';self._save(v);return
                    e=next((x for x in v['entries'] if x['status']=='pending' and x['id'] in v.get('run_entry_ids',[])),None)
                    if not e:
                        v['status']='paused' if any(x['status']=='pending' for x in v['entries']) else 'finished'
                        self._save(v);return
                    try:
                        ensure_job_unclaimed(self.store,e['job_id'],bid)
                        ensure_no_job_worker(self.store,e['job_id'])
                        if e['job_id'] in self.busy:raise Conflict('job이 실행 중입니다.')
                        if entry_basis(self.store,e['job_id'],e['item'],e.get('section'))!=e['basis']:raise Conflict('계획 후 내용·원본·검수·의존성이 변경되었습니다.')
                    except ValueError as exc:
                        e.update(status='skipped',message=str(exc));self._save(v);continue
                    aid=uuid.uuid4().hex
                    attempt={'id':aid,'entry_id':e['id'],'started_at':now(),'status':'running','finished_at':None,'message':''}
                    v['attempts'].append(attempt);e.update(status='running',attempt_id=aid)
                    self._save(v)
                # No state lock across a potentially slow call. Pause can be persisted concurrently.
                try:
                    self.queue.execute_entry(e['job_id'],e,v['plan']['operation'])
                    status,message='completed','호출과 기존 처리 함수 반환 완료'
                except Exception as exc:
                    status = 'failed' if isinstance(exc,ValueError) else 'uncertain'
                    message = str(exc) if isinstance(exc,ValueError) else '호출 또는 저장 완료 불명확. 자동 재호출하지 않습니다.'
                with execution_lock(self.store):
                    v=self._read(bid)
                    target=next(x for x in v['entries'] if x['id']==e['id'])
                    target.update(status=status,message=message)
                    attempt=next(x for x in v['attempts'] if x['id']==aid)
                    attempt.update(status=status,message=message,finished_at=now())
                    self._save(v)  # failure leaves durable running -> uncertain, never automatic replay
