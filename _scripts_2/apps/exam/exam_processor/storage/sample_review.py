"""R4 runtime-only sessions. No approval, content mutation, or provider calls.

v1 conservatively fingerprints the entire job's records/reviews/classifications,
manifest context and referenced region bytes. Runtime activity is excluded.
The Store lock serializes actions in one server; external writers are unsupported.
"""
import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone

from ..domain.job_manifest import RUNTIME_KEYS
from ..domain.review_decision import CLASSIFIER_VERSION
from .store import Conflict

POLICY_VERSION = 'green-sample/1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def select_sample(candidates, count, seed, policy=POLICY_VERSION):
    return sorted(set(candidates), key=lambda rid: (digest([policy, seed, rid]), rid))[:count]


def source_path(folder, image):
    path = (folder / 'regions' / image).resolve()
    return path if path.is_relative_to((folder / 'regions').resolve()) and path.is_file() else None


class SampleReview:
    def __init__(self, store):
        self.store = store

    def _load(self, job_id):
        folder = self.store.job_dir(job_id)
        raw = json.loads((folder / 'job.json').read_text())
        job = self.store.get(job_id)
        candidates = sorted(i['id'] for i in job['items'] if i.get('kind') == 'question'
                            and i.get('review') == 'pending'
                            and i.get('review_decision', {}).get('tier') == 'GREEN'
                            and i['review_decision'].get('eligible_for_sample_review'))
        available = raw.get('storage_version', 1) >= 2 and 'items' not in raw
        if not available:
            return job, None, {}, {'candidates': candidates}, False
        runtime = self.store.runtime.load(folder, job_id)
        if not (folder / 'runtime.json').exists():
            for key in ('task', 'queue', 'queue_history', 'ai_log', 'ai_progress'):
                if key in raw:
                    setattr(runtime, key, copy.deepcopy(raw[key]))
        state = runtime.sample_review or {'revision': 'empty', 'active_session_id': None, 'sessions': []}
        if not isinstance(state.get('sessions'), list) or not isinstance(state.get('revision'), str):
            raise ValueError('표본 세션 저장 형식을 읽을 수 없습니다. 기존 runtime 기록을 보존하세요.')
        records, reviews, decisions, sources = {}, {}, {}, {}
        for item in job['items']:
            rid = item['id']
            rec = self.store.records.get(folder, rid)
            review_file = folder / 'review' / (rid + '.json')
            records[rid] = {'revision': rec.provenance.revision_id, 'digest': digest(rec.to_dict()),
                            'dependencies': rec.relations.passage_ids + rec.relations.listening_script_ids}
            reviews[rid] = {'revision': item.get('review_decision', {}).get('basis_review_state_revision_id'),
                            'digest': digest(json.loads(review_file.read_text()) if review_file.exists() else None)}
            decisions[rid] = item.get('review_decision')
            for region in item.get('regions', []) + item.get('solution_regions', []):
                name = region.get('image', '')
                path = source_path(folder, name)
                sources[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path else None
        basis = {'policy': POLICY_VERSION, 'classifier': CLASSIFIER_VERSION, 'candidates': candidates,
                 'records': records, 'reviews': reviews, 'decisions': decisions, 'sources': sources,
                 'context': digest({k: v for k, v in raw.items() if k not in RUNTIME_KEYS and k != 'last_export'})}
        return job, runtime, copy.deepcopy(state), basis, True

    @staticmethod
    def _reasons(old, new):
        labels = {'policy': '표본 정책', 'classifier': '분류 정책', 'candidates': '후보 구성',
                  'records': '내용 revision 또는 의존성', 'reviews': '검수 상태 revision·근거',
                  'decisions': '분류 근거', 'sources': '원본 영역 파일', 'context': '작업 설정·출처·경고'}
        return [label + ' 변경' for key, label in labels.items() if old.get(key) != new.get(key)]

    def _session(self, session, basis):
        view = copy.deepcopy(session)
        reasons = self._reasons(session['basis'], basis)
        counts = {v: sum(x['verdict'] == v for x in session['results'].values()) for v in ('unreviewed', 'pass', 'problem')}
        # A recorded problem can never be overwritten into a passing session.
        outcome = 'problem' if counts['problem'] else 'passed' if not counts['unreviewed'] else 'incomplete'
        view.update(valid=not reasons, invalidation_reasons=reasons, counts=counts, outcome=outcome,
                    status='stale' if reasons else 'paused' if session['paused'] else outcome)
        return view

    def _materials(self, job, basis, selected):
        items = {i['id']: i for i in job['items']}
        result = {}
        folder = self.store.job_dir(job['id'])
        for rid in selected:
            pending, seen, parts = [rid], set(), []
            while pending:
                key = pending.pop(0)
                if key in seen:
                    continue
                seen.add(key)
                item = items.get(key)
                if not item:
                    parts.append({'id': key, 'label': key, 'source_available': False, 'preview': {}, 'sections': []})
                    continue
                pending.extend(basis.get('records', {}).get(key, {}).get('dependencies', []))
                sections = []
                for name, field, reference in (('본문', 'regions', 'reference_text'), ('해설', 'solution_regions', 'solution_reference')):
                    regions = item.get(field, [])
                    if field == 'solution_regions' and not (regions or item.get('solution') or item.get(reference)):
                        continue
                    images = [{'image': r.get('image', ''), 'page': r.get('page'),
                               'available': bool(source_path(folder, r.get('image', '')))} for r in regions]
                    text = item.get(reference, '') if not regions else ''
                    sections.append({'label': name, 'images': images, 'text': text,
                                     'available': all(r['available'] for r in images) if images else bool(text.strip())})
                preview = item.get('standard_preview', {})
                if item.get('kind') != 'question':
                    # R2 attaches question previews only. Reuse the same export
                    # projection for shared passages/scripts instead of omitting them.
                    from ..exporters.profiles.markdown import preview_sections
                    preview = preview_sections(self.store.records.get(folder, key))
                label = {'question': '문항', 'passage': '공통 지문', 'script': '듣기 대본'}.get(item.get('kind'), '연결 자료')
                parts.append({'id': key, 'label': f"{label} {item.get('number')}", 'sections': sections,
                              'preview': preview, 'source_available': bool(sections) and all(s['available'] for s in sections)
                              and preview.get('status') != 'unavailable' and bool(preview.get('body'))})
            result[rid] = {'parts': parts, 'can_pass': bool(parts) and all(p['source_available'] for p in parts)}
        return result

    def _view(self, job, state, basis, available):
        sessions = [self._session(s, basis) for s in state.get('sessions', [])]
        selected = list(dict.fromkeys(rid for s in sessions for rid in s['selected']))
        return {'available': available, 'unavailable_reason': '' if available else 'legacy v1: 후보 조회만 지원합니다. 세션 저장은 v2에서 지원하며 자동 전환하지 않습니다.',
                'revision': state.get('revision', 'empty'), 'basis_token': digest(basis), 'policy': POLICY_VERSION,
                'candidate_ids': basis['candidates'], 'candidate_count': len(basis['candidates']),
                'active_session_id': state.get('active_session_id'), 'sessions': sessions,
                'materials': self._materials(job, basis, selected) if available else {},
                'job': {**job, 'items': [{k: v for k, v in i.items() if k != 'history'} for i in job['items']]}}

    def get(self, job_id):
        with self.store.lock:
            job, _, state, basis, available = self._load(job_id)
            return self._view(job, state, basis, available)

    def act(self, job_id, payload):
        with self.store.lock:
            job, runtime, state, basis, available = self._load(job_id)
            if not available:
                raise Conflict('legacy v1은 표본 세션을 저장할 수 없습니다. 자동 migration하지 않습니다.')
            if payload.get('revision') != state['revision'] or payload.get('basis_token') != digest(basis):
                raise Conflict('세션 또는 검수 근거가 바뀌었습니다. 다시 불러와 확인하세요.')
            action = payload.get('action')
            now = datetime.now(timezone.utc).isoformat()
            if action == 'start':
                count, seed = payload.get('count'), payload.get('seed')
                if type(count) is not int or not 1 <= count <= len(basis['candidates']):
                    raise ValueError('표본 수를 현재 후보 수 이내의 1 이상 정수로 지정하세요.')
                if not isinstance(seed, str) or not seed.strip() or len(seed) > 128:
                    raise ValueError('재현용 seed를 1~128자로 지정하세요.')
                selected = select_sample(basis['candidates'], count, seed, POLICY_VERSION)
                session = {'id': uuid.uuid4().hex, 'created_at': now, 'policy': POLICY_VERSION, 'seed': seed,
                           'sample_count': count, 'basis': basis, 'selected': selected, 'paused': False,
                           'results': {rid: {'verdict': 'unreviewed', 'note': ''} for rid in selected}, 'events': []}
                state['sessions'].append(session)
                state['active_session_id'] = session['id']
            else:
                session = next((s for s in state['sessions'] if s['id'] == payload.get('session_id')), None)
                if not session or session['id'] != state['active_session_id']:
                    raise Conflict('현재 세션이 아닙니다. 이전 세션은 기록 조회만 지원합니다.')
                if self._reasons(session['basis'], basis):
                    raise Conflict('이전 세션의 근거가 무효화되었습니다. 기록을 보존하고 새 세션을 시작하세요.')
                if action == 'verdict':
                    rid, verdict, note = payload.get('item_id'), payload.get('verdict'), payload.get('note', '')
                    if session['paused'] or rid not in session['results']:
                        raise Conflict('세션을 재개하고 선정된 표본을 확인하세요.')
                    if session['results'][rid]['verdict'] != 'unreviewed':
                        raise Conflict('이미 판정된 표본입니다. 기존 판정은 보존됩니다.')
                    if verdict not in ('pass', 'problem') or not isinstance(note, str) or len(note) > 1000:
                        raise ValueError('통과 또는 문제 발견과 1000자 이내 메모를 지정하세요.')
                    if verdict == 'problem' and not note.strip():
                        raise ValueError('문제 발견에는 판단 사유가 필요합니다.')
                    if verdict == 'pass' and not self._materials(job, basis, [rid])[rid]['can_pass']:
                        raise Conflict('원본 또는 표준 본문을 확인할 수 없습니다. 상세 검수로 이동하세요.')
                    session['results'][rid] = {'verdict': verdict, 'note': note.strip(), 'at': now}
                elif action in ('pause', 'resume'):
                    if session['paused'] == (action == 'pause'):
                        raise Conflict('이미 반영된 세션 상태입니다.')
                    session['paused'] = action == 'pause'
                else:
                    raise ValueError('지원하지 않는 표본 세션 동작입니다.')
            session['events'].append({'at': now, 'action': action, 'item_id': payload.get('item_id'),
                                      'verdict': payload.get('verdict'), 'note': payload.get('note', '').strip() if action == 'verdict' else ''})
            state['revision'] = uuid.uuid4().hex
            runtime.sample_review = state
            # Build response before save: successful persistence has no fallible read after it.
            response = self._view(job, state, basis, available)
            self.store.runtime.save(self.store.job_dir(job_id), runtime)
            return response
