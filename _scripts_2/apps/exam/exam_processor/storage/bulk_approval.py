"""R5 explicit plans, per-item intents and approval receipts.

Single-file saves are atomic. A batch is not: an interrupted prepared item must
be reconciled explicitly. Only a receipt plus an exact reversible delta proves
our approval. R4 remains stale after approvals; its baseline is never rewritten.
Store.lock covers ordinary writers in one server. flock excludes concurrent R5
writers across Store instances. Other processes editing job files are unsupported.
"""
import copy
import fcntl
import json
import uuid
from datetime import datetime, timezone

from ..domain.services.review_service import approval_errors
from .ephemeral_views import resolve_dependency_status
from .focused_transaction import restore_on_failure
from .sample_review import SampleReview, digest
from .store import Conflict

POLICY_VERSION = 'safe-bulk-approval/1'


def now():
    return datetime.now(timezone.utc).isoformat()


def event(run, action, **fields):
    run['events'].append({'at': now(), 'action': action, **fields})


class BulkApproval:
    def __init__(self, store):
        self.store = store
        self.sample = SampleReview(store)

    def _load(self, job_id):
        job, runtime, sample, basis, available = self.sample._load(job_id)
        state = copy.deepcopy(runtime.bulk_approval) if available else {}
        state = state or {'revision': 'empty', 'plans': [], 'executions': [], 'active_execution_id': None}
        if (not isinstance(state.get('revision'), str) or not isinstance(state.get('plans'), list)
                or not isinstance(state.get('executions'), list)):
            raise ValueError('일괄 승인 기록 형식 오류: 기존 runtime을 보존하세요.')
        return job, runtime, sample, basis, available, state

    def _save(self, job_id, state):
        # Preserve other runtime owners, including R4's immutable historical basis.
        folder = self.store.job_dir(job_id)
        runtime = self.store.runtime.load(folder, job_id)
        if not (folder / 'runtime.json').exists():
            raw = json.loads((folder / 'job.json').read_text())
            for key in ('task', 'queue', 'queue_history', 'ai_log', 'ai_progress'):
                if key in raw:
                    setattr(runtime, key, copy.deepcopy(raw[key]))
        state['revision'] = uuid.uuid4().hex
        runtime.bulk_approval = copy.deepcopy(state)
        self.store.runtime.save(self.store.job_dir(job_id), runtime)

    def _session(self, sample, basis):
        session = next((s for s in sample.get('sessions', []) if s['id'] == sample.get('active_session_id')), None)
        if not session:
            raise Conflict('현재 R4 세션이 없습니다.')
        view = self.sample._session(session, basis)
        if view['status'] != 'passed' or not session['selected']:
            raise Conflict('현재 유효하고 모든 표본이 명시적으로 통과한 R4 세션이 필요합니다.')
        return session

    def _gate(self, job_id, job, rid):
        folder = self.store.job_dir(job_id)
        records = {i['id']: self.store.records.get(folder, i['id']) for i in job['items']}
        record = records[rid]
        review = self.store.reviews.get(folder, rid)
        kwargs = {'passage_approval_status': resolve_dependency_status(record.relations.passage_ids, records),
                  'script_approval_status': resolve_dependency_status(record.relations.listening_script_ids, records),
                  'job_warnings': job.get('warnings', [])}
        for ids, key in ((record.relations.passage_ids, 'passage_approval_status'),
                         (record.relations.listening_script_ids, 'script_approval_status')):
            if ids and kwargs[key] != 'approved':
                raise Conflict(rid + ': 연결 자료 승인 근거를 확인하세요.')
        errors = approval_errors(record, review, review.note, **kwargs)
        if errors:
            raise Conflict(rid + ': ' + '; '.join(errors))
        raw = json.loads((folder / 'review' / (rid + '.json')).read_text())
        normalized = review.to_dict()
        # Old v2 files may lack only the newly introduced receipt field.
        if 'approval_events' not in raw:
            normalized.pop('approval_events')
        if normalized != raw:
            raise Conflict(rid + ': 검수 파일을 손실 없이 저장할 수 없습니다. 상세 점검이 필요합니다.')
        record_raw = json.loads((folder / 'records' / (rid + '.json')).read_text())
        if record_raw != record.to_dict():
            raise Conflict(rid + ': 문항 파일 저장 형식을 확인하세요.')
        return record, review, kwargs, raw

    def _validate(self, job_id, plan, run=None):
        job, _, sample, basis, available, _ = self._load(job_id)
        if not available or plan['policy'] != POLICY_VERSION:
            raise Conflict('저장 형식 또는 일괄 승인 정책이 바뀌었습니다.')
        s = next((s for s in sample['sessions'] if s['id'] == plan['session_id']), None)
        if not s or digest(s) != plan['session_digest'] or sample['active_session_id'] != s['id']:
            raise Conflict('확인한 R4 세션 또는 판정이 바뀌었습니다.')
        expected = copy.deepcopy(plan['basis'])
        remaining = list(plan['targets'])
        if run:
            for rid, item in run['items'].items():
                if item['status'] in ('prepared', 'uncertain'):
                    raise Conflict('처리 결과가 불명확합니다. 복구 확인이 먼저 필요합니다.')
                if item['status'] == 'done':
                    raw_record = json.loads((self.store.job_dir(job_id) / 'records' / (rid + '.json')).read_text())
                    if digest(raw_record) != item['after']['records']['digest']:
                        raise Conflict(rid + ': 완료 후 문항 파일 변경이 발견되었습니다.')
                    if any(basis[k].get(rid) != item['after'][k] for k in ('records', 'reviews', 'decisions')):
                        raise Conflict(rid + ': 완료 후 외부 변경이 발견되었습니다.')
                    for key in ('records', 'reviews', 'decisions'):
                        expected[key][rid] = copy.deepcopy(item['after'][key])
                    expected['candidates'].remove(rid)
                    remaining.remove(rid)
        if expected != basis:
            raise Conflict('확인한 근거가 바뀌었습니다: ' + ' / '.join(self.sample._reasons(expected, basis)))
        if not run:
            self._session(sample, basis)
        for rid in remaining:
            if rid not in basis['candidates']:
                raise Conflict(rid + ': 현재 pending GREEN 문항이 아닙니다.')
            self._gate(job_id, job, rid)
        return job, basis, remaining

    def _proof(self, job_id, rid, item):
        """Return unchanged / done / uncertain; never interpret another approval as ours."""
        folder = self.store.job_dir(job_id)
        attempt = item['attempts'][-1]
        rec = json.loads((folder / 'records' / (rid + '.json')).read_text())
        rev = json.loads((folder / 'review' / (rid + '.json')).read_text())
        if digest(rec) == attempt['record_digest'] and digest(rev) == attempt['review_digest']:
            return 'unchanged'
        if rec['review']['human_approval'] != 'approved' or not rev.get('approval_events'):
            return 'uncertain'
        if rev['approval_events'][-1] != attempt['receipt']:
            return 'uncertain'
        rec['review']['human_approval'] = 'pending'
        rev['approval_events'].pop()
        if not attempt['had_approval_events']:
            rev.pop('approval_events')
        rev['state_revision_id'] = attempt['review_revision']
        return 'done' if digest(rec) == attempt['record_digest'] and digest(rev) == attempt['review_digest'] else 'uncertain'

    def _checkpoint_proof(self, job_id, rid, item):
        proof = self._proof(job_id, rid, item)
        item['status'] = {'unchanged': 'failed', 'done': 'done', 'uncertain': 'uncertain'}[proof]
        if proof == 'done':
            basis = self.sample._load(job_id)[3]
            item['after'] = {k: copy.deepcopy(basis[k][rid]) for k in ('records', 'reviews', 'decisions')}
        item['error'] = {'unchanged': '승인 변경 없음 · 근거 재검사 후 재시도 가능', 'done': '',
                         'uncertain': '저장 결과를 입증할 수 없음 · 재승인 금지, 별도 복구 필요'}[proof]
        return proof

    def _execute(self, job_id, state, plan, run):
        try:
            _, _, remaining = self._validate(job_id, plan, run)
        except (ValueError, OSError) as exc:
            run['status'] = 'blocked'; run['error'] = str(exc)
            event(run, 'preflight_blocked', reason=str(exc)); self._save(job_id, state)
            return
        run['status'] = 'running'; run['error'] = ''
        self._save(job_id, state)
        for rid in remaining:
            try:
                job, basis, _ = self._validate(job_id, plan, run)
                record, review, kwargs, raw = self._gate(job_id, job, rid)
            except (ValueError, OSError) as exc:
                run['status'] = 'blocked'; run['error'] = str(exc)
                event(run, 'execution_blocked', item_id=rid, reason=str(exc)); self._save(job_id, state)
                return
            item = run['items'][rid]
            receipt = {'execution_id': run['id'], 'attempt_id': uuid.uuid4().hex, 'plan_id': plan['id'],
                       'policy': POLICY_VERSION, 'at': now(), 'before_record_digest': basis['records'][rid]['digest'],
                       'before_review_digest': basis['reviews'][rid]['digest']}
            item['attempts'].append({'receipt': receipt, 'record_digest': basis['records'][rid]['digest'],
                                     'review_digest': basis['reviews'][rid]['digest'],
                                     'review_revision': raw['state_revision_id'], 'had_approval_events': 'approval_events' in raw})
            item['status'] = 'prepared'; item['error'] = ''
            event(run, 'prepared', item_id=rid, attempt_id=receipt['attempt_id'])
            self._save(job_id, state)  # Must succeed BEFORE any approval write.
            folder = self.store.job_dir(job_id)
            failure = None
            try:
                with restore_on_failure([folder / 'records' / (rid + '.json'), folder / 'review' / (rid + '.json')]):
                    self.store._review_svc.approve(folder, rid, record.provenance.revision_id, review.note,
                                                   approval_receipt=receipt, **kwargs)
            except Exception as exc:
                failure = str(exc)
            proof = self._checkpoint_proof(job_id, rid, item)
            event(run, 'approval_result', item_id=rid, outcome=item['status'], reason=failure or item['error'])
            if failure or proof != 'done':
                run['status'] = 'recovery_required' if proof == 'uncertain' else 'partial'
                run['error'] = failure or item['error']
                self._save(job_id, state)
                return
            self._save(job_id, state)  # Failure leaves durable prepared intent; explicit recovery only.
        # Includes external changes during the last approval, not just between items.
        try:
            self._validate(job_id, plan, run)
        except (ValueError, OSError) as exc:
            run['status'] = 'blocked'; run['error'] = str(exc)
        else:
            run['status'] = 'completed'; state['active_execution_id'] = None
        event(run, run['status'], reason=run.get('error', ''))
        self._save(job_id, state)

    def get(self, job_id):
        with self.store.lock:
            job, _, sample, basis, available, state = self._load(job_id)
            blockers = []
            try:
                if not available:
                    raise Conflict('legacy v1: 조회만 지원합니다. 승인 계획·실행 저장은 v2에서 지원하며 자동 전환하지 않습니다.')
                self._session(sample, basis)
                if not basis['candidates']:
                    raise Conflict('현재 pending GREEN 문항 후보가 없습니다.')
            except Conflict as exc:
                blockers.append(str(exc))
            candidates = []
            for i in job['items']:
                reasons = []
                if i['id'] not in basis['candidates']:
                    reasons = ['현재 pending GREEN 표본 후보 문항이 아닙니다.']
                elif available:
                    try:
                        self._gate(job_id, job, i['id'])
                    except (ValueError, OSError) as exc:
                        reasons = [str(exc)]
                candidates.append({'id': i['id'], 'number': i.get('number'), 'reasons': reasons})
            for run in state['executions']:
                if run['status'] == 'running' or any(i['status'] in ('prepared', 'uncertain') for i in run['items'].values()):
                    run['status'] = 'recovery_required'
                run['counts'] = {s: sum(i['status'] == s for i in run['items'].values()) for s in ('pending', 'prepared', 'done', 'failed', 'uncertain')}
            sessions = [self.sample._session(s, basis) for s in sample.get('sessions', [])]
            return {'available': available, 'policy': POLICY_VERSION, 'basis_token': digest(basis),
                    'blockers': blockers, 'candidates': candidates, 'sessions': sessions,
                    **state, 'job': {**job, 'items': [{k: v for k, v in i.items() if k != 'history'} for i in job['items']]}}

    def act(self, job_id, payload):
        with self.store.lock:
            if not self._load(job_id)[4]:
                raise Conflict('legacy v1은 일괄 승인을 저장할 수 없습니다.')
            # Not removed: deleting a lock file would allow locking different inodes.
            with (self.store.job_dir(job_id) / '.bulk-approval.lock').open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise Conflict('같은 job의 일괄 승인 실행이 진행 중입니다.')
                return self._act(job_id, payload)

    def _act(self, job_id, p):
        _, _, sample, basis, _, state = self._load(job_id)
        if p.get('revision') != state['revision']:
            raise Conflict('일괄 승인 기록이 바뀌었습니다. 다시 불러와 확인하세요.')
        action = p.get('action')
        if action == 'plan':
            if state['active_execution_id']:
                raise Conflict('진행 중인 실행을 복구하거나 종료한 뒤 새 계획을 만드세요.')
            s = self._session(sample, basis)
            targets = p.get('targets')
            if (not isinstance(targets, list) or not targets or any(not isinstance(x, str) for x in targets)
                    or len(set(targets)) != len(targets) or not set(targets).issubset(basis['candidates'])):
                raise ValueError('중복 없이 현재 후보 안에서 승인 대상을 1건 이상 지정하세요.')
            if p.get('basis_token') != digest(basis):
                raise Conflict('후보 근거가 바뀌었습니다. 다시 확인하세요.')
            plan = {'id': uuid.uuid4().hex, 'created_at': now(), 'policy': POLICY_VERSION,
                    'session_id': s['id'], 'session_digest': digest(s), 'targets': sorted(targets), 'basis': basis}
            self._validate(job_id, plan)
            plan['confirmation_token'] = digest(plan)
            state['plans'].append(plan); self._save(job_id, state)
        elif action in ('execute', 'retry', 'recover', 'close'):
            run = next((r for r in state['executions'] if r['id'] == p.get('execution_id')), None)
            plan = next((x for x in state['plans'] if x['id'] == (run['plan_id'] if run else p.get('plan_id'))), None)
            if not plan:
                raise Conflict('저장된 승인 계획을 찾을 수 없습니다.')
            if action in ('execute', 'retry') and (p.get('confirmed') is not True
                    or p.get('targets') != plan['targets'] or p.get('confirmation_token') != plan['confirmation_token']):
                raise Conflict('정확한 대상 목록·건수를 다시 확인하고 별도로 실행하세요.')
            if action == 'execute':
                if state['active_execution_id'] or any(r['plan_id'] == plan['id'] for r in state['executions']):
                    raise Conflict('이미 실행했거나 다른 실행이 진행 중입니다. 기존 실행을 조회하세요.')
                run = {'id': uuid.uuid4().hex, 'plan_id': plan['id'], 'status': 'pending', 'error': '',
                       'confirmed_at': now(), 'explicit_action': True, 'actor_identity': None,
                       'confirmed_targets': plan['targets'], 'items': {rid: {'status': 'pending', 'attempts': [], 'error': ''} for rid in plan['targets']}, 'events': []}
                state['executions'].append(run); state['active_execution_id'] = run['id']
                event(run, 'explicit_execute', targets=plan['targets'])
                self._execute(job_id, state, plan, run)
            else:
                if not run or state['active_execution_id'] != run['id']:
                    raise Conflict('현재 미완료 실행이 아닙니다. 이력은 보존됩니다.')
                if action == 'retry':
                    if run['status'] == 'running' or any(i['status'] in ('prepared', 'uncertain') for i in run['items'].values()):
                        raise Conflict('복구 확인이 먼저 필요합니다. 결과 불명확 항목은 재승인하지 않습니다.')
                    event(run, 'explicit_retry', targets=plan['targets']); self._execute(job_id, state, plan, run)
                elif action == 'recover':
                    for rid, item in run['items'].items():
                        if item['status'] in ('prepared', 'uncertain'):
                            proof = self._checkpoint_proof(job_id, rid, item)
                            event(run, 'reconciled', item_id=rid, outcome=proof)
                    try:
                        _, _, remaining = self._validate(job_id, plan, run)
                        run['status'] = 'partial' if remaining else 'completed'; run['error'] = ''
                        if not remaining:
                            state['active_execution_id'] = None
                    except (ValueError, OSError) as exc:
                        run['status'] = 'blocked'; run['error'] = str(exc)
                    event(run, 'explicit_recovery', status=run['status']); self._save(job_id, state)
                else:
                    if run['status'] == 'running' or any(i['status'] in ('prepared', 'uncertain') for i in run['items'].values()):
                        raise Conflict('불명확한 결과는 복구 확인 전 종료할 수 없습니다.')
                    run['status'] = 'closed'; event(run, 'explicit_close', reason='추가 승인 없이 종료 · 기록 보존')
                    state['active_execution_id'] = None; self._save(job_id, state)
        else:
            raise ValueError('지원하지 않는 일괄 승인 동작입니다.')
        return self.get(job_id)
