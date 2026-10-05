"""R6 explicit labels and bounded observed intervals. Runtime evidence is an
immutable evaluation input copy, never a content, review or routing authority.
Only cooperative R6 writers share flock; ordinary writers use Store.lock.
"""
import base64
import copy
import fcntl
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone

from ..domain.focused_review import issue_token
from .sample_review import SampleReview, digest
from .store import Conflict

POLICY = 'human-evaluation/1'
SCHEMA = 'evaluation-dataset/1'
SCOPES = ('body', 'answer', 'solution', 'dependencies', 'source')
LEASE_SECONDS = 15


def now():
    return datetime.now(timezone.utc).isoformat()


def empty_state():
    return {'revision': 'empty', 'labels': [], 'evidence': {}, 'actions': [],
            'timers': [], 'datasets': [], 'requests': {}}


def record_focused_action(store, folder, item_id, payload, before_revision, after_revision):
    """Called inside R3 rollback boundary; action provenance never creates a label."""
    runtime = store.runtime.load(folder)
    raw = json.loads((folder / 'runtime.json').read_text()) if (folder / 'runtime.json').exists() else {}
    roundtrip = runtime.to_dict()
    if any(k not in roundtrip or roundtrip[k] != v for k, v in raw.items()):
        raise ValueError('runtime의 알 수 없는 필드를 보존하기 위해 행동 저장을 차단합니다.')
    state = copy.deepcopy(runtime.evaluation) or empty_state()
    state['actions'].append({'id': uuid.uuid4().hex, 'source': 'R3', 'at': now(),
                            'item_id': item_id, 'action': payload['action'],
                            'issue_token': payload['token'], 'before_revision': before_revision,
                            'after_revision': after_revision, 'actor': None})
    state['revision'] = uuid.uuid4().hex
    runtime.evaluation = state
    store.runtime.save(folder, runtime)


class Evaluation:
    def __init__(self, store):
        self.store = store
        self.sample = SampleReview(store)
        with store.lock:
            if not hasattr(store, '_evaluation_epoch'):
                store._evaluation_epoch = uuid.uuid4().hex
        self.epoch = store._evaluation_epoch

    def _load(self, job_id):
        job, runtime, _, basis, available = self.sample._load(job_id)
        state = copy.deepcopy(runtime.evaluation) if available else {}
        state = state or empty_state()
        if (set(state) != set(empty_state()) or not isinstance(state['revision'], str)
                or any(not isinstance(state[k], list) for k in ('labels', 'actions', 'timers', 'datasets'))
                or any(not isinstance(state[k], dict) for k in ('evidence', 'requests'))):
            raise ValueError('평가 기록 형식 오류: 기존 runtime을 보존하세요.')
        folder = self.store.job_dir(job_id)
        if available and (folder / 'runtime.json').exists():
            raw_runtime = json.loads((folder / 'runtime.json').read_text())
            roundtrip = runtime.to_dict()
            if any(k not in roundtrip or roundtrip[k] != v for k, v in raw_runtime.items()):
                raise ValueError('알 수 없는 runtime 필드를 손실 없이 보존할 수 없습니다. 저장을 차단합니다.')
        # A fixture marker is set by test construction, never accepted from a label request.
        origin = 'synthetic_fixture' if (folder / 'evaluation-fixture.json').exists() else 'human_explicit'
        basis = {**basis, 'evaluation_policy': POLICY, 'origin': origin}
        files = {}
        if available:
            for item in job['items']:
                paths = ['regions/' + r.get('image', '') for r in item.get('regions', []) + item.get('solution_regions', [])]
                paths += [a.get('path', '') for a in item.get('assets', [])]
                for name in paths:
                    path = (folder / name).resolve()
                    files[name] = hashlib.sha256(path.read_bytes()).hexdigest() if name and path.is_relative_to(folder.resolve()) and path.is_file() else None
        basis['input_files'] = files
        return job, runtime, state, basis, available

    def _capture(self, job, basis):
        folder = self.store.job_dir(job['id'])
        records, reviews, blobs = {}, {}, {}
        for rid in basis['records']:
            records[rid] = json.loads((folder / 'records' / (rid + '.json')).read_text())
            path = folder / 'review' / (rid + '.json')
            reviews[rid] = json.loads(path.read_text()) if path.exists() else None
        for name, sha in basis['input_files'].items():
            if sha:
                data = (folder / name).read_bytes()
                if hashlib.sha256(data).hexdigest() != sha:
                    raise Conflict('원본 근거가 변경되었습니다. 다시 확인하세요.')
                blobs[name] = base64.b64encode(data).decode('ascii')
        # Hashes tie captured bytes to exactly the basis shown before submit.
        for rid, record in records.items():
            if digest(record) != basis['records'][rid]['digest'] or digest(reviews[rid]) != basis['reviews'][rid]['digest']:
                raise Conflict('내용 또는 검수 근거가 변경되었습니다.')
        context = json.loads((folder / 'job.json').read_text())
        return {'basis': copy.deepcopy(basis), 'records': records, 'reviews': reviews,
                'source_blobs_base64': blobs, 'manifest': context,
                'input_contract': 'region-crops-and-canonical-snapshots/1; no complete original PDF claim',
                'file_roles': {name: 'original_region_crop' if name.startswith('regions/') else 'extracted_asset' for name in basis['input_files']},
                'roles': {'records': 'canonical_transcription_snapshot', 'reviews': 'AI_and_workflow_evidence_not_ground_truth'},
                'materials': self.sample._materials(job, basis, [i['id'] for i in job['items'] if i.get('kind') == 'question'])}

    @staticmethod
    def _reproducible(evidence, item_id):
        if not evidence:
            return ['missing_evidence']
        reasons = []
        basis = evidence['basis']
        for rid, expected in basis['records'].items():
            if digest(evidence['records'].get(rid)) != expected['digest']:
                reasons.append('record_snapshot_mismatch')
            if digest(evidence['reviews'].get(rid)) != basis['reviews'][rid]['digest']:
                reasons.append('review_snapshot_mismatch')
        for name, sha in basis['input_files'].items():
            try:
                data = base64.b64decode(evidence['source_blobs_base64'].get(name, ''), validate=True)
                if not sha or hashlib.sha256(data).hexdigest() != sha:
                    reasons.append('missing_source_bytes')
            except (ValueError, TypeError):
                reasons.append('invalid_source_bytes')
        material = evidence['materials'].get(item_id)
        # v1 image-region evidence only. Extracted text is not silently called original.
        if not material or not material['can_pass'] or any(
                not section['images'] for part in material['parts'] for section in part['sections']):
            reasons.append('source_not_reproducible')
        return sorted(set(reasons))

    def _actions(self, job, runtime, state):
        actions = copy.deepcopy(state['actions'])
        for item in job['items']:
            for idx, history in enumerate(item.get('history', [])):
                actions.append({'id': f"revision:{item['id']}:{history.get('revision', idx)}", 'source': 'review.history',
                                'item_id': item['id'], 'action': 'content_revision', 'at': None,
                                'evidence': history, 'actor': None})
            for idx, issue in enumerate((item.get('audit') or {}).get('issues', [])):
                if issue.get('skipped') or issue.get('applied'):
                    actions.append({'id': 'audit:' + issue_token(item['audit'], idx), 'source': 'review.audit',
                                    'item_id': item['id'], 'action': 'skip' if issue.get('skipped') else 'apply',
                                    'at': None, 'evidence': issue, 'actor': None})
        if runtime:
            for session in runtime.sample_review.get('sessions', []):
                for rid, result in session['results'].items():
                    if result['verdict'] != 'unreviewed':
                        actions.append({'id': f"R4:{session['id']}:{rid}", 'source': 'R4', 'item_id': rid,
                                        'action': result['verdict'], 'at': result.get('at'), 'evidence': result,
                                        'session_id': session['id'], 'actor': None})
            for run in runtime.bulk_approval.get('executions', []):
                for rid, result in run['items'].items():
                    actions.append({'id': f"R5:{run['id']}:{rid}", 'source': 'R5', 'item_id': rid,
                                    'action': result['status'], 'at': None, 'evidence': result,
                                    'execution_id': run['id'], 'plan_id': run['plan_id'], 'actor': None})
        return actions

    def _labels(self, state, basis):
        replaced = {x['supersedes'] for x in state['labels'] if x['supersedes']}
        result = []
        for label in state['labels']:
            reasons = []
            if label['id'] in replaced:
                reasons.append('superseded')
            evidence = state['evidence'].get(label['basis_hash'])
            if not evidence or evidence.get('basis') != basis:
                reasons.append('basis_changed')
            if label['policy'] != POLICY:
                reasons.append('policy_changed')
            if evidence and digest(evidence) != label['evidence_hash']:
                reasons.append('evidence_integrity')
            details = self.sample._reasons(evidence['basis'], basis) if evidence else ['근거 없음']
            details += [key + ' 변경' for key in ('evaluation_policy', 'origin', 'input_files') if evidence and evidence['basis'].get(key) != basis.get(key)]
            reasons += self._reproducible(evidence, label['item_id'])
            if label['verdict'] == 'deferred':
                reasons.append('deferred')
            result.append({**label, 'current': not any(x in reasons for x in ('superseded', 'basis_changed', 'policy_changed', 'evidence_integrity')),
                           'invalidation_reasons': details, 'exclusions': sorted(set(reasons))})
        return result

    def _timer_view(self, timer):
        value = copy.deepcopy(timer)
        value.setdefault('gaps', [])
        if value['status'] == 'running' and (value['epoch'] != self.epoch or not 0 <= time.monotonic() - value['last_tick'] <= LEASE_SECONDS):
            value['status'] = 'interrupted'
            value['unmeasured_tail'] = True
            value['gaps'].append({'from': value['last_at'], 'until': None, 'reason': 'server_restart' if value['epoch'] != self.epoch else 'checkpoint_gap', 'seconds': None})
        return value

    @staticmethod
    def _metrics(labels, origin):
        population = [x for x in labels if x['origin'] == origin]
        issues = [x for x in population if x['target'] == 'issue' and not x['exclusions'] and x['tier'] in ('GREEN', 'YELLOW', 'RED')]
        questions = [x for x in population if x['target'] == 'question' and not x['exclusions'] and x['scope'] == list(SCOPES)
                     and x['tier'] in ('GREEN', 'YELLOW', 'RED')]
        errors = [x for x in questions if x['verdict'] == 'error_found']
        return {'origin': origin,
                'issue_false_positive': {'numerator': sum(x['verdict'] == 'false_positive' for x in issues), 'denominator': len(issues),
                                         'meaning': '명시 판정된 비교 가능 AI 지적 중 오탐 비율; 전체 FPR 아님'},
                'question_green_miss': {'numerator': sum(x['tier'] == 'GREEN' for x in errors), 'denominator': len(errors),
                                        'meaning': '전체 범위를 명시 확인한 오류 문항 중 GREEN 비율; 선택 표본 한정'},
                'issue_excluded': [{'id': x['id'], 'reasons': x['exclusions'] or ['classification_not_comparable']} for x in population if x['target'] == 'issue' and x not in issues],
                'question_excluded': [{'id': x['id'], 'reasons': x['exclusions'] or (['partial_scope'] if x['scope'] != list(SCOPES) else ['classification_not_comparable'])}
                                      for x in population if x['target'] == 'question' and x not in questions],
                'question_comparable': len(questions)}

    def _view(self, job, runtime, state, basis, available):
        labels = self._labels(state, basis)
        timers = [self._timer_view(t) for t in state['timers']]
        measured = [t['measured_seconds'] for t in timers if t['measured_seconds'] is not None]
        current = [x for x in labels if x['current']]
        questions = [i for i in job['items'] if i.get('kind') == 'question']
        actions = self._actions(job, runtime, state)
        return {'available': available, 'reason': '' if available else 'legacy v1: 기록 조회만 지원. 자동 migration 및 평가 저장 없음.',
                'revision': state['revision'], 'basis_token': digest(basis), 'policy': POLICY, 'schema': SCHEMA, 'origin': basis['origin'],
                'materials': self.sample._materials(job, basis, [i['id'] for i in questions]) if available else {},
                'labels': labels, 'actions': actions, 'timers': timers,
                'datasets': [{k: v for k, v in d.items() if k not in ('evidence', 'labels')} for d in state['datasets']],
                'summary': {'recorded': len(labels), 'current': len(current),
                            'unjudged_questions': sum(not any(x['target'] == 'question' and x['item_id'] == i['id'] for x in current) for i in questions),
                            'unjudged_issues': sum(not any(x['target'] == 'issue' and x['issue_token'] == issue_token(i['audit'], n) and x['item_id'] == i['id'] for x in current)
                                                  for i in questions for n, _ in enumerate((i.get('audit') or {}).get('issues', []))),
                            'deferred': sum(x['verdict'] == 'deferred' for x in current),
                            'invalidated': sum(not x['current'] and 'superseded' not in x['exclusions'] for x in labels),
                            'excluded': sum(bool(x['exclusions']) for x in labels),
                            'measured_seconds': round(sum(measured), 3) if measured else None,
                            'unmeasured_tails': sum(len(t['gaps']) for t in timers),
                            'time_coverage': '명시적으로 시작한 구간의 서버 확인 간격만 합산. 미측정 구간·과거 시간은 미확인. 집중 시간 아님.'},
                'metrics': [self._metrics(labels, x) for x in ('human_explicit', 'synthetic_fixture')],
                'green_errors': [x for x in current if x['target'] == 'question' and x['verdict'] == 'error_found' and x['tier'] == 'GREEN'],
                'items': [{'id': i['id'], 'number': i.get('number'), 'revision': i.get('revision'), 'tier': (i.get('review_decision') or {}).get('tier'),
                           'issues': [{'token': issue_token(i['audit'], n), 'index': n, 'evidence': issue} for n, issue in enumerate((i.get('audit') or {}).get('issues', []))]}
                          for i in questions]}

    def get(self, job_id):
        with self.store.lock:
            job, runtime, state, basis, available = self._load(job_id)
            return self._view(job, runtime, state, basis, available)

    def dataset(self, job_id, dataset_id):
        with self.store.lock:
            _, _, state, _, _ = self._load(job_id)
            result = next((d for d in state['datasets'] if d['id'] == dataset_id), None)
            if result is None:
                raise ValueError('데이터셋을 찾을 수 없습니다.')
            content = {k: v for k, v in result.items() if k not in ('id', 'created_at', 'composition_hash')}
            if digest(content) != result['composition_hash']:
                raise ValueError('데이터셋 무결성 오류: 이전 산출물을 덮어쓰지 마세요.')
            return copy.deepcopy(result)

    def act(self, job_id, payload):
        with self.store.lock:
            folder = self.store.job_dir(job_id)
            # Existing file: GET and invalid legacy requests never create sidecars.
            with (folder / 'job.json').open('rb') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                return self._act(job_id, payload)

    def _act(self, job_id, payload):
        job, runtime, state, basis, available = self._load(job_id)
        if not available:
            raise Conflict('legacy v1은 조회 전용입니다. 자동 migration하지 않습니다.')
        request_id = payload.get('request_id')
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 128:
            raise ValueError('중복 방지 요청 ID가 필요합니다.')
        old = state['requests'].get(request_id)
        if old:
            if old != digest(payload):
                raise Conflict('같은 요청 ID의 입력이 다릅니다.')
            return self._view(job, runtime, state, basis, available)
        if payload.get('revision') != state['revision']:
            raise Conflict('다른 기록이 저장되었습니다. 입력을 보존하고 재조회 후 확인하세요.')
        action = payload.get('action')
        if action in ('label', 'dataset') and payload.get('basis_token') != digest(basis):
            raise Conflict('판정 대상 근거가 변경되었습니다. 재조회 후 다시 확인하세요.')
        if action == 'label':
            self._label(job, runtime, state, basis, payload)
        elif action == 'dataset':
            self._dataset(job, state, basis, payload)
        elif action in ('start', 'pause', 'resume', 'end', 'tick'):
            self._timer(state, payload)
        else:
            raise ValueError('지원하지 않는 평가 동작입니다.')
        state['revision'] = uuid.uuid4().hex
        state['requests'][request_id] = digest(payload)
        runtime.evaluation = state
        response = self._view(job, runtime, state, basis, available)
        self.store.runtime.save(self.store.job_dir(job_id), runtime)
        return response

    def _label(self, job, runtime, state, basis, p):
        rid, target, verdict = p.get('item_id'), p.get('target'), p.get('verdict')
        item = next((i for i in job['items'] if i['id'] == rid and i.get('kind') == 'question'), None)
        if not item or target not in ('question', 'issue'):
            raise ValueError('문항 또는 개별 지적을 선택하세요.')
        allowed = ('error_found', 'no_error_in_scope', 'deferred') if target == 'question' else ('actual_error', 'false_positive', 'deferred')
        if verdict not in allowed:
            raise ValueError('대상 단위에 맞는 판정을 선택하세요.')
        note, scope = p.get('note'), p.get('scope')
        if not isinstance(note, str) or not note.strip() or len(note) > 2000:
            raise ValueError('판단 근거를 1~2000자로 남기세요.')
        if not isinstance(scope, list) or not scope or any(x not in SCOPES for x in scope) or len(scope) != len(set(scope)):
            raise ValueError('실제로 확인한 범위를 선택하세요.')
        token = p.get('issue_token') if target == 'issue' else None
        audit = item.get('audit') or {}
        issue = next((x for n, x in enumerate(audit.get('issues', [])) if issue_token(audit, n) == token), None) if token else None
        if target == 'issue' and (not issue or audit.get('revision') != item.get('revision') or audit.get('status') != 'completed'):
            raise Conflict('현재 revision의 AI 지적을 선택하세요. 과거 지적에 현재 판정을 소급하지 않습니다.')
        prior = next((x for x in reversed(state['labels']) if x['item_id'] == rid and x['target'] == target and x['issue_token'] == token), None)
        if p.get('supersedes') != (prior['id'] if prior else None):
            raise Conflict('이미 판정이 있습니다. 기존 기록을 확인하고 명시적으로 정정하세요.')
        reason = p.get('correction_reason', '')
        if not isinstance(reason, str) or len(reason) > 2000 or (prior and not reason.strip()):
            raise ValueError('정정 사유를 남기세요.')
        refs = p.get('action_ids', [])
        actions = {a['id']: a for a in self._actions(job, runtime, state) if a['item_id'] == rid}
        if not isinstance(refs, list) or any(not isinstance(x, str) or x not in actions for x in refs):
            raise ValueError('같은 문항의 존재하는 행동 기록만 연결하세요.')
        key = digest(basis)
        evidence = state['evidence'].get(key) or self._capture(job, basis)
        state['evidence'][key] = evidence
        state['labels'].append({'id': uuid.uuid4().hex, 'at': now(), 'policy': POLICY, 'actor': None,
                                'origin': basis['origin'], 'item_id': rid, 'target': target, 'issue_token': token,
                                'issue_evidence': copy.deepcopy(issue), 'revision_id': item.get('revision'),
                                'tier': (item.get('review_decision') or {}).get('tier'), 'basis_hash': key,
                                'evidence_hash': digest(evidence), 'verdict': verdict, 'scope': [x for x in SCOPES if x in scope],
                                'note': note.strip(), 'supersedes': prior['id'] if prior else None, 'correction_reason': reason.strip(),
                                'action_links': [copy.deepcopy(actions[x]) for x in refs]})

    def _dataset(self, job, state, basis, p):
        selection = p.get('selection')
        if not isinstance(selection, dict) or set(selection) != {'origin', 'tiers', 'targets', 'full_scope_only'}:
            raise ValueError('데이터셋 선정 조건을 명시하세요.')
        if (selection['origin'] not in ('human_explicit', 'synthetic_fixture')
                or type(selection['full_scope_only']) is not bool
                or not isinstance(selection['tiers'], list) or not selection['tiers'] or any(t not in ('GREEN', 'YELLOW', 'RED', 'NOT_READY') for t in selection['tiers'])
                or not isinstance(selection['targets'], list) or not selection['targets'] or any(t not in ('question', 'issue') for t in selection['targets'])):
            raise ValueError('잘못된 선정 조건입니다.')
        selection = {**selection, 'tiers': sorted(set(selection['tiers'])), 'targets': sorted(set(selection['targets']))}
        included, excluded = [], []
        for label in self._labels(state, basis):
            reasons = list(label['exclusions'])
            if label['origin'] != selection['origin']: reasons.append('origin_filter')
            if label['tier'] not in selection['tiers']: reasons.append('tier_filter')
            if label['target'] not in selection['targets']: reasons.append('target_filter')
            if selection['full_scope_only'] and label['scope'] != list(SCOPES): reasons.append('partial_scope')
            if reasons:
                excluded.append({'id': label['id'], 'reasons': sorted(set(reasons))})
            else:
                included.append(label)
        # Enumerate unlabeled current targets; absence is never a negative label.
        labels = self._labels(state, basis)
        for item in job['items']:
            if item.get('kind') != 'question':
                continue
            targets = [('question', None)] + [('issue', issue_token(item['audit'], n)) for n, _ in enumerate((item.get('audit') or {}).get('issues', []))]
            for target, token in targets:
                if target in selection['targets'] and not any(x['current'] and x['item_id'] == item['id'] and x['target'] == target and x['issue_token'] == token for x in labels):
                    excluded.append({'id': f"unjudged:{item['id']}:{token or 'question'}", 'reasons': ['no_explicit_judgment']})
        included.sort(key=lambda x: x['id']); excluded.sort(key=lambda x: x['id'])
        evidence = {x['basis_hash']: copy.deepcopy(state['evidence'][x['basis_hash']]) for x in included}
        content = {'schema': SCHEMA, 'policy': POLICY, 'selection': selection, 'basis_hash': digest(basis),
                   'included': [x['id'] for x in included], 'excluded': excluded, 'labels': included, 'evidence': evidence}
        state['datasets'].append({'id': uuid.uuid4().hex, 'created_at': now(), 'composition_hash': digest(content), **content})

    def _timer(self, state, p):
        action, owner = p['action'], p.get('owner')
        if not isinstance(owner, str) or not 8 <= len(owner) <= 128:
            raise ValueError('창별 측정 ID가 필요합니다. 사용자 신원은 미확인입니다.')
        active = next((t for t in reversed(state['timers']) if t['status'] != 'ended'), None)
        if action == 'start':
            if active:
                raise Conflict('기존 구간을 종료하거나 중단 상태에서 명시적으로 재개하세요.')
            active = {'id': uuid.uuid4().hex, 'status': 'paused', 'owner': owner, 'actor': None,
                      'created_at': now(), 'measured_seconds': None, 'unmeasured_tail': False, 'gaps': [], 'intervals': [], 'events': []}
            state['timers'].append(active)
        elif not active or active['id'] != p.get('timer_id'):
            raise Conflict('현재 측정 구간을 다시 불러오세요.')
        active.setdefault('gaps', [])
        status = self._timer_view(active)['status']
        if status == 'interrupted':
            active['gaps'] = self._timer_view(active)['gaps']
            active['unmeasured_tail'] = True
            active['status'] = 'paused'
        elif active['owner'] != owner and active['status'] == 'running':
            raise Conflict('다른 창에서 측정 중입니다. 중복 측정할 수 없습니다.')
        if action in ('start', 'resume'):
            if active['status'] != 'paused':
                raise Conflict('이미 측정 중입니다.')
            active.update(status='running', owner=owner, epoch=self.epoch, last_tick=time.monotonic(), last_at=now())
        else:
            if action == 'tick' and active['status'] != 'running':
                raise Conflict('측정이 중단되었습니다. 재조회 후 명시적으로 재개하세요.')
            if active['status'] == 'running':
                delta = time.monotonic() - active['last_tick']
                if delta < 0 or delta > LEASE_SECONDS:
                    raise Conflict('측정 시계가 변경되었습니다.')
                at = now()
                active['intervals'].append({'from': active['last_at'], 'to': at, 'seconds': delta, 'epoch': self.epoch})
                active['measured_seconds'] = (active['measured_seconds'] or 0) + delta
                active.update(last_tick=time.monotonic(), last_at=at)
            if action in ('pause', 'end'):
                active['status'] = 'ended' if action == 'end' else 'paused'
        active['events'].append({'action': action, 'at': now(), 'reason': p.get('reason', ''), 'owner': owner})
