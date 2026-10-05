"""R7 offline replay only. No network, approval, classification or label writes.

R6 owns frozen datasets. This owner stores immutable plans and append-only response
attempts in evaluation-runs, never a second editable content authority.
"""
import copy
import fcntl
import json
import re
import uuid

from .evaluation import Evaluation, POLICY as LABEL_POLICY, SCHEMA as DATASET_SCHEMA, now
from .json_io import atomic_json_save
from .sample_review import digest
from .store import Conflict
from ..domain.focused_review import issue_token

POLICY = 'offline-issue-comparison/1'
SCHEMA = 'offline-evaluation-run/1'
INPUT_SCHEMA = 'offline-issue-input/1'
RESPONSE_SCHEMA = 'offline-responses/1'
TASK = 'audit_issue_is_error'
QUESTION = '이 개별 AI 지적은 보존된 원본과 전사 사이의 실제 오류인가? 다른 미발견 오류나 중요도·승인·검수 경로는 판단하지 않는다.'
OUTCOMES = ('actual_error', 'false_positive', 'abstain', 'failed')


def checked_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('잘못된 평가 실행 ID입니다.')
    return value


def request_id(p):
    value = p.get('request_id')
    if not isinstance(value, str) or not 8 <= len(value) <= 128:
        raise ValueError('중복 방지 요청 ID가 필요합니다.')
    # Strict JSON also rejects non-finite numbers before a mutation.
    json.dumps(p, allow_nan=False)
    return value


def make_input(label, evidence):
    """Allowlist content and original crops; never project review/workflow fields."""
    issue = label.get('issue_evidence') or {}
    section = issue.get('section')
    if section not in ('body', 'solution') or section not in label['scope']:
        return None, ['unsupported_or_unchecked_issue_scope']
    parts = evidence['materials'][label['item_id']]['parts']
    if len(parts) > 1 and 'dependencies' not in label['scope']:
        return None, ['unchecked_dependencies']
    if section == 'solution' and not any(s['label'] == '해설' and s['images'] for s in parts[0]['sections']):
        return None, ['missing_solution_source']
    contents, sources = [], {}
    for part in parts:
        record = evidence['records'][part['id']]
        contents.append({'id': part['id'], 'body': record.get('body', ''),
                         'solution': record.get('solution', ''),
                         'answer': record.get('question', {}).get('answer'),
                         'subject': record.get('question', {}).get('subject')})
        for sec in part['sections']:
            for region in sec['images']:
                name = 'regions/' + region['image']
                sources[name] = {'role': 'original_region_crop', 'sha256': evidence['basis']['input_files'][name],
                                 'base64': evidence['source_blobs_base64'][name]}
        # Extracted assets are not original source images.
        for asset in record.get('assets', []):
            name = asset.get('path')
            if name in evidence['source_blobs_base64']:
                sources[name] = {'role': 'extracted_asset', 'sha256': evidence['basis']['input_files'][name],
                                 'base64': evidence['source_blobs_base64'][name]}
    if not sources or not any(isinstance(issue.get(k), str) and issue[k].strip() for k in ('message', 'original', 'extracted')):
        return None, ['insufficient_evidence']
    # Do not forward suggestion, skipped/applied, reasons, tier or human notes.
    return {'schema': INPUT_SCHEMA, 'task': TASK, 'question': QUESTION,
            'issue': {k: issue[k] for k in ('type', 'section', 'message', 'original', 'extracted') if isinstance(issue.get(k), str)},
            'content': contents, 'files': sources,
            'boundary': 'Frozen region crops and transcription; not a complete PDF or whole-question error search.'}, []


def build_plan(dataset):
    if dataset['schema'] != DATASET_SCHEMA or dataset['policy'] != LABEL_POLICY:
        raise ValueError('지원하지 않는 R6 데이터셋 정책/스키마입니다.')
    origin = dataset['selection']['origin']
    if origin not in ('human_explicit', 'synthetic_fixture'):
        raise ValueError('데이터셋 판정 출처를 확인할 수 없습니다.')
    cases, excluded = [], [{'stage': 'R6', **copy.deepcopy(x)} for x in dataset['excluded']]
    if dataset['included'] != [x['id'] for x in dataset['labels']]:
        raise ValueError('데이터셋 포함 목록과 레이블이 일치하지 않습니다.')
    for label in dataset['labels']:
        reasons = list(label.get('exclusions', []))
        if label['origin'] != origin:
            raise ValueError('서로 다른 판정 출처가 섞인 데이터셋입니다.')
        if label['target'] != 'issue': reasons.append('unsupported_target_question')
        if label['verdict'] not in ('actual_error', 'false_positive'): reasons.append('no_binary_ground_truth')
        if label['policy'] != LABEL_POLICY: reasons.append('unsupported_label_policy')
        evidence = dataset['evidence'].get(label['basis_hash'])
        if not evidence or digest(evidence) != label['evidence_hash']:
            reasons.append('evidence_integrity')
        else:
            reasons += Evaluation._reproducible(evidence, label['item_id'])
            basis = evidence['basis']
            if (digest(basis) != label['basis_hash'] or basis.get('origin') != origin
                    or basis.get('evaluation_policy') != LABEL_POLICY
                    or basis['records'][label['item_id']]['revision'] != label['revision_id']):
                reasons.append('label_basis_mismatch')
            if label['target'] == 'issue':
                audit = (evidence['reviews'].get(label['item_id']) or {}).get('audit') or {}
                found = next((x for n, x in enumerate(audit.get('issues', [])) if issue_token(audit, n) == label['issue_token']), None)
                if found is None or found != label['issue_evidence']:
                    reasons.append('issue_snapshot_mismatch')
        payload = None
        if not reasons:
            payload, more = make_input(label, evidence)
            reasons += more
        if reasons:
            excluded.append({'stage': 'R7', 'id': label['id'], 'reasons': sorted(set(reasons))})
            continue
        cases.append({'id': digest([label['item_id'], label['issue_token'], label['evidence_hash']])[:32],
                      'input_hash': digest(payload), 'input': payload,
                      'ground_truth': copy.deepcopy(label),
                      'baseline': {'outcome': 'actual_error', 'meaning': '당시 AI가 이 지적을 제기함; tier를 오류 정답으로 변환하지 않음',
                                   'tier': label['tier'], 'classifier': evidence['basis']['classifier']}})
    cases.sort(key=lambda c: c['id'])
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('중복 평가 대상입니다.')
    manifest = {'schema': INPUT_SCHEMA, 'task': TASK, 'question': QUESTION,
                'cases': [{'case_id': c['id'], 'input_hash': c['input_hash']} for c in cases]}
    return {'policy': POLICY, 'task': TASK, 'dataset_id': dataset['id'], 'dataset_hash': dataset['composition_hash'],
            'label_origin': origin, 'input_manifest_hash': digest(manifest), 'manifest': manifest,
            'cases': cases, 'excluded': excluded}


def report(run):
    latest = {a['case_id']: a for a in run['attempts']}
    rows, matrix = [], {'tp': 0, 'fp': 0, 'tn': 0, 'fn': 0}
    baseline = dict(matrix)
    counts = {'pending': 0, 'failed': 0, 'invalid_response': 0, 'abstain': 0, 'scored': 0}
    for case in run['plan']['cases']:
        attempt = latest.get(case['id'])
        status = attempt['status'] if attempt else 'pending'
        counts[status] += 1
        truth = case['ground_truth']['verdict']
        outcome = attempt.get('outcome') if attempt else None
        if status == 'scored':
            positive, predicted = truth == 'actual_error', outcome == 'actual_error'
            matrix[('t' if positive == predicted else 'f') + ('p' if predicted else 'n')] += 1
            baseline['tp' if positive else 'fp'] += 1
        rows.append({'case_id': case['id'], 'item_id': case['ground_truth']['item_id'], 'status': status,
                     'truth': truth, 'outcome': outcome, 'attempt_id': attempt['id'] if attempt else None,
                     'reason': attempt.get('reason') if attempt else None})
    denominator = counts['scored']
    return {'counts': counts, 'eligible': len(rows), 'excluded': len(run['plan']['excluded']),
            'matrix': matrix, 'baseline_matrix_same_scored_cases': baseline,
            'agreement': {'numerator': matrix['tp'] + matrix['tn'], 'denominator': denominator,
                          'value': (matrix['tp'] + matrix['tn']) / denominator if denominator else None},
            'coverage': {'numerator': denominator, 'denominator': len(rows)}, 'rows': rows,
            'label_origin': run['plan']['label_origin'], 'response_source': run['response_source'],
            'boundary': '선정된 AI 지적의 명시 판정과 오프라인 응답 비교. 문항 전체 미탐률·실제 AI 성능·시간 개선이 아님.'}


class OfflineEvaluation:
    def __init__(self, store):
        self.store = store

    def folder(self, job_id):
        return self.store.job_dir(job_id) / 'evaluation-runs'

    def available(self, job_id):
        raw = json.loads((self.store.job_dir(job_id) / 'job.json').read_text())
        return raw.get('storage_version', 1) >= 2 and 'items' not in raw

    def _read(self, job_id, run_id):
        path = self.folder(job_id) / (checked_id(run_id) + '.json')
        if not path.is_file(): raise ValueError('평가 실행을 찾을 수 없습니다.')
        run = json.loads(path.read_text())
        if (run.get('schema') != SCHEMA or run.get('id') != run_id
                or digest({k: v for k, v in run.items() if k != 'integrity'}) != run.get('integrity')
                or digest(run['plan']) != run['plan_hash']):
            raise ValueError('평가 실행 무결성 오류. 기존 기록을 보존하세요.')
        return run

    def _save(self, job_id, run):
        run['integrity'] = digest({k: v for k, v in run.items() if k != 'integrity'})
        atomic_json_save(self.folder(job_id) / (run['id'] + '.json'), run)

    def get(self, job_id, run_id):
        with self.store.lock:
            run = self._read(job_id, run_id)
            return {**run, 'report': report(run)}

    def inputs(self, job_id, run_id):
        run = self.get(job_id, run_id)
        plan = run['plan']
        return {'schema': INPUT_SCHEMA, 'input_manifest_hash': plan['input_manifest_hash'],
                'task': TASK, 'cases': [{'case_id': c['id'], 'input_hash': c['input_hash'], 'input': c['input']} for c in plan['cases']]}

    def list(self, job_id):
        with self.store.lock:
            available = self.available(job_id)
            runs = []
            for path in sorted(self.folder(job_id).glob('*.json')):
                run = self._read(job_id, path.stem)
                runs.append({'id': run['id'], 'created_at': run['created_at'], 'dataset_id': run['plan']['dataset_id'],
                             'paused': run['paused'], 'report': report(run)})
            return {'available': available, 'reason': '' if available else 'legacy v1 조회 전용 · 자동 migration 없음', 'runs': runs}

    def act(self, job_id, p):
        key = request_id(p)
        with self.store.lock:
            if not self.available(job_id): raise Conflict('legacy v1은 평가 실행을 저장할 수 없습니다.')
            folder = self.folder(job_id)
            folder.mkdir(exist_ok=True)
            # Dedicated stable inode serializes cooperative R7 writers across Store instances.
            with (folder / '.lock').open('a+b') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                return self._act(job_id, p, key)

    def _act(self, job_id, p, key):
        if p.get('action') == 'create':
            run_id = digest(['offline-run', key])[:32]
            path = self.folder(job_id) / (run_id + '.json')
            if path.exists():
                run = self._read(job_id, run_id)
                if run['requests'].get(key) != digest(p): raise Conflict('같은 요청 ID의 입력이 다릅니다.')
                return self.get(job_id, run_id)
            dataset = Evaluation(self.store).dataset(job_id, p.get('dataset_id'))
            if p.get('dataset_hash') != dataset['composition_hash']: raise Conflict('데이터셋 해시가 다릅니다. 다시 확인하세요.')
            plan = build_plan(dataset)
            run = {'schema': SCHEMA, 'id': run_id, 'created_at': now(), 'revision': uuid.uuid4().hex,
                   'plan': plan, 'plan_hash': digest(plan), 'paused': False, 'attempts': [], 'events': [],
                   'response_source': None, 'requests': {key: digest(p)}}
        else:
            run = self._read(job_id, p.get('run_id'))
            if key in run['requests']:
                if run['requests'][key] != digest(p): raise Conflict('같은 요청 ID의 입력이 다릅니다.')
                return self.get(job_id, run['id'])
            if p.get('revision') != run['revision']: raise Conflict('다른 창에서 실행 기록이 변경되었습니다. 입력을 보존하고 재조회하세요.')
            if run['plan']['policy'] != POLICY: raise Conflict('과거 정책의 실행은 조회만 가능합니다.')
            action = p.get('action')
            if action == 'import':
                if run['paused']: raise Conflict('중단된 실행입니다. 명시적으로 재개하세요.')
                self._import(run, p)
            elif action in ('pause', 'resume'):
                if run['paused'] == (action == 'pause'): raise Conflict('이미 반영된 실행 상태입니다.')
                run['paused'] = action == 'pause'
            else: raise ValueError('지원하지 않는 오프라인 평가 동작입니다.')
            run['requests'][key] = digest(p)
            run['revision'] = uuid.uuid4().hex
        run['events'].append({'action': p['action'], 'at': now(), 'request_id': key})
        result = report(run)  # validate projection before committing
        self._save(job_id, run)
        return {**run, 'report': result}

    def _import(self, run, p):
        bundle = p.get('bundle')
        if not isinstance(bundle, dict) or len(json.dumps(bundle, ensure_ascii=False)) > 1_000_000:
            raise ValueError('응답 JSON은 직렬화 기준 100만자 이내의 객체여야 합니다.')
        if set(bundle) != {'schema', 'input_manifest_hash', 'response_origin', 'producer', 'responses'}:
            raise ValueError('응답 묶음 필드를 확인하세요.')
        if bundle['schema'] != RESPONSE_SCHEMA or bundle['input_manifest_hash'] != run['plan']['input_manifest_hash']:
            raise Conflict('응답이 현재 실행의 고정 입력과 일치하지 않습니다.')
        if bundle['response_origin'] not in ('synthetic_fixture', 'unverified_import'):
            raise ValueError('응답 출처는 합성 fixture 또는 미확인 가져오기만 지원합니다. 실제 AI 호출로 인증하지 않습니다.')
        if not isinstance(bundle['producer'], str) or not 1 <= len(bundle['producer'].strip()) <= 200:
            raise ValueError('응답 작성 출처를 1~200자로 명시하세요. 신원은 검증되지 않습니다.')
        source = {'origin': bundle['response_origin'], 'producer': bundle['producer']}
        if run['response_source'] is not None and run['response_source'] != source:
            raise Conflict('응답 출처를 섞지 않습니다. 다른 출처는 새 실행을 만드세요.')
        responses = bundle['responses']
        if not isinstance(responses, list) or not responses or len(responses) > 500:
            raise ValueError('1~500개 응답을 명시하세요.')
        cases = {c['id']: c for c in run['plan']['cases']}
        latest = {a['case_id']: a for a in run['attempts']}
        seen = set()
        for raw in responses:
            if not isinstance(raw, dict) or not isinstance(raw.get('case_id'), str) or raw['case_id'] not in cases:
                raise ValueError('선정되지 않은 응답 대상입니다.')
            cid = raw['case_id']
            if cid in seen: raise ValueError('한 묶음의 중복 대상입니다.')
            seen.add(cid)
            if raw.get('input_hash') != cases[cid]['input_hash']: raise Conflict('응답의 개별 입력 해시가 다릅니다.')
            prior = latest.get(cid)
            retry = p.get('retry_reason', '')
            if prior and prior['status'] in ('scored', 'abstain'):
                raise Conflict('완료된 응답은 덮어쓰지 않습니다. 재비교는 새 실행을 만드세요.')
            if prior and (not isinstance(retry, str) or not 1 <= len(retry.strip()) <= 2000):
                raise ValueError('실패 재시도에는 사유가 필요합니다.')
            reason, outcome = raw.get('reason'), raw.get('outcome')
            valid = (set(raw) == {'case_id', 'input_hash', 'outcome', 'reason'} and outcome in OUTCOMES
                     and isinstance(reason, str) and 1 <= len(reason.strip()) <= 2000)
            status = ('scored' if outcome in ('actual_error', 'false_positive') else outcome) if valid else 'invalid_response'
            run['attempts'].append({'id': uuid.uuid4().hex, 'at': now(), 'case_id': cid,
                                    'status': status, 'outcome': outcome if valid else None,
                                    'reason': reason if valid else '응답 형식/판정/사유 오류', 'raw': copy.deepcopy(raw),
                                    'bundle_hash': digest(bundle), 'source': source, 'retry_reason': retry if prior else None,
                                    'supersedes_attempt': prior['id'] if prior else None,
                                    'usage': None, 'cost': None, 'provider_model': None})
        run['response_source'] = source
