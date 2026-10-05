"""EX-N01: explicit whole-job export plans and conservative artifact recovery.

Never approves a record. Never resumes or retries an uncertain export. Existing
job exporters retain their own validation and output format ownership.
"""
import copy
import json
import re
import uuid
from pathlib import Path

from .batch_jobs import execution_lock, ensure_job_unclaimed, ensure_no_job_worker
from .export_evidence import file_hash, payload_files
from .json_io import atomic_json_save
from .sample_review import digest
from .store import Conflict

POLICY = 'approved-job-export/1'
DONE = ('completed', 'recovered')


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
        raise ValueError('잘못된 출력 계획 ID입니다.')
    return value


def destination_path(value):
    path = Path(value).expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink 목적지는 지원하지 않습니다.')
    if path.exists() and not path.is_dir():
        raise ValueError('출력 목적지가 디렉터리가 아닙니다.')
    return path.resolve()


class ExportJobs:
    def __init__(self, store):
        self.store = store

    def _path(self, plan_id):
        return self.store.root / 'export-plans' / (identifier(plan_id) + '.json')

    def _read(self, plan_id):
        path = self._path(plan_id)
        if not path.is_file():
            raise ValueError('출력 계획을 찾을 수 없습니다.')
        value = json.loads(path.read_text())
        if value.get('integrity') != digest({k: v for k, v in value.items() if k != 'integrity'}):
            raise ValueError('출력 기록 무결성 오류. 기존 파일을 보존하세요.')
        if value.get('policy') != POLICY:
            raise ValueError('지원하지 않는 출력 기록입니다.')
        return value

    def _save(self, value):
        value['revision'] = uuid.uuid4().hex
        value['integrity'] = digest({k: v for k, v in value.items() if k != 'integrity'})
        atomic_json_save(self._path(value['id']), value)

    def get(self, plan_id):
        return copy.deepcopy(self._read(plan_id))

    def list(self):
        return [self.get(p.stem) for p in sorted((self.store.root / 'export-plans').glob('*.json'))]

    def _entry(self, jid, destination):
        folder = self.store.job_dir(jid)
        raw = json.loads((folder / 'job.json').read_text())
        if raw.get('storage_version', 1) < 2:
            raise ValueError('legacy 작업은 기존 검수 경로에서 확인하세요.')
        ensure_job_unclaimed(self.store, jid)
        ensure_no_job_worker(self.store, jid)
        job = self.store.get(jid)
        if any((job.get(k) or {}).get('status') in ('running', 'paused', 'uncertain') for k in ('task', 'queue')):
            raise ValueError('작업 처리/복구를 먼저 마치세요.')
        manifest = self.store.jobs.load_manifest(folder)
        if manifest.warnings:
            raise ValueError('작업 범위 경고를 먼저 해결하세요.')
        records = self.store.records.get_many(folder, manifest.record_ids)
        reviews = self.store.reviews.get_many(folder, manifest.record_ids)
        if not records or len(records) != len(manifest.record_ids):
            raise ValueError('문항 근거가 없습니다.')
        from _scripts_2.apps.exam.exam_core.domain.canonical import canonical_relation_errors
        errors = canonical_relation_errors(list(records.values()))
        if errors:
            raise ValueError('정규 관계 오류: ' + '; '.join(errors))
        segments = [manifest.track, manifest.source_id]
        for record in records.values():
            segments.extend([record.question.question_id, record.question.question_number,
                             record.question.question_set_id, record.source.source_set_id, record.source.section_code])
        if any(v and (v in ('.', '..') or any(c in str(v) for c in ('/', '\\', '\x00'))) for v in segments):
            raise ValueError('출력 경로 식별자가 안전한 단일 이름이 아닙니다.')
        review_basis = {}
        for rid, record in records.items():
            if record.review.human_approval != 'approved':
                raise ValueError('일괄 출력은 작업 전체의 현재 승인본만 지원합니다. 검수·승인을 마치세요.')
            review = reviews.get(rid)
            if not review:
                raise ValueError('현재 검수 근거가 없습니다.')
            waiver, audit = review.audit_waiver or {}, review.audit or {}
            rev = record.provenance.revision_id
            if not ((waiver.get('revision') == rev and waiver.get('status') == 'waived') or
                    (audit.get('revision') == rev and audit.get('status') == 'completed')):
                raise ValueError('현재 버전의 대조와 승인이 필요합니다.')
            rb = review.to_dict()
            rb.pop('published_revision_id', None)
            rb.pop('state_revision_id', None)
            review_basis[rid] = rb
        # Include original files used by any existing exporter, not only Markdown.
        files = {}
        for path in sorted(folder.rglob('*')):
            if path.is_symlink():
                raise ValueError('작업 내부 symlink는 지원하지 않습니다.')
            if path.is_file() and path.suffix.lower() != '.json':
                files[path.relative_to(folder).as_posix()] = file_hash(path)
        for record in records.values():
            for asset in record.assets:
                name = asset.source_path or asset.path
                path = folder / name
                if not path.resolve().is_relative_to(folder.resolve()) or not path.is_file():
                    raise ValueError('에셋 원본이 없거나 작업 폴더 밖입니다.')
                files[name] = file_hash(path)
        from ..domain.job_manifest import RUNTIME_KEYS
        mb = {k: v for k, v in raw.items() if k not in RUNTIME_KEYS and k != 'last_export'}
        basis = digest(dict(manifest=mb, records={k: v.to_dict() for k, v in records.items()},
                            reviews=review_basis, files=files))
        return dict(job_id=jid, source=job.get('source', ''), source_code=manifest.source_id, basis=basis,
                    items=[dict(id=rid, revision=records[rid].provenance.revision_id) for rid in manifest.record_ids],
                    sets=sorted({r.question.question_set_id or r.source.source_set_id or r.source.section_code or 'general'
                                 for r in records.values()}), destination=str(destination))

    def dashboard(self):
        rows = []
        with self.store.lock:
            for summary in self.store.list():
                job = self.store.get(summary['id'])
                items = job.get('items', [])
                rows.append(dict(summary,
                    input_ready=bool(items),
                    transcription_pending=sum(bool(i.get('transcription_pending')) for i in items),
                    audit_current=sum((i.get('audit') or {}).get('revision') == i.get('revision')
                                      and (i.get('audit') or {}).get('status') == 'completed' for i in items),
                    review_pending=sum(i.get('review') == 'pending' for i in items),
                    last_export=job.get('last_export') or ''))
        return rows

    def preview(self, job_ids, destination=None):
        if (not isinstance(job_ids, list) or not 1 <= len(job_ids) <= 50
                or any(not isinstance(jid, str) for jid in job_ids) or len(set(job_ids)) != len(job_ids)):
            raise ValueError('중복 없는 1~50개 작업을 선택하세요.')
        target = destination_path(destination or self.store.output)
        entries, excluded = [], []
        existing = self.list()
        with self.store.lock:
            for jid in job_ids:
                self.store.job_dir(jid)
                try:
                    folder = self.store.job_dir(jid).resolve()
                    if target.is_relative_to(folder) or folder.is_relative_to(target):
                        raise ValueError('작업 원본과 겹치는 목적지는 지원하지 않습니다.')
                    entry = self._entry(jid, target)
                    duplicate = any(e['job_id'] == jid and e['basis'] == entry['basis'] and
                                    p['plan']['destination'] == str(target) and
                                    e['status'] in (*DONE, 'running', 'uncertain')
                                    for p in existing for e in p['entries'])
                    if duplicate:
                        raise ValueError('동일 근거·목적지의 출력 완료 또는 결과 불명확 기록이 있습니다. 기록을 확인하세요.')
                    entries.append(entry)
                except (ValueError, OSError) as exc:
                    excluded.append(dict(job_id=jid, reason=str(exc)))
        plan = dict(policy=POLICY, job_ids=job_ids, destination=str(target), entries=entries, excluded=excluded)
        return dict(plan=plan, plan_hash=digest(plan))

    def plan(self, job_ids, preview_hash, request_id, destination=None):
        identifier(request_id)
        request = dict(job_ids=job_ids, preview_hash=preview_hash, destination=str(destination or self.store.output))
        with execution_lock(self.store):
            if self._path(request_id).exists():
                old = self._read(request_id)
                if old['request'] != request:
                    raise Conflict('같은 요청 ID의 내용이 다릅니다.')
                return old
            preview = self.preview(job_ids, destination)
            if preview['plan_hash'] != preview_hash:
                raise Conflict('출력 근거가 바뀌었습니다. 다시 계획하세요.')
            if not preview['plan']['entries']:
                raise ValueError('출력 가능한 승인 작업이 없습니다.')
            value = dict(id=request_id, policy=POLICY, request=request, status='planned', **preview)
            value['entries'] = [dict(e, status='pending', output=None, error='') for e in value['plan']['entries']]
            self._save(value)
            return value

    def execute(self, plan_id, revision, confirmed_plan_hash):
        with execution_lock(self.store):
            value = self._read(plan_id)
            if confirmed_plan_hash != value['plan_hash']:
                raise Conflict('확인한 출력 계획이 다릅니다.')
            # A replay observes the original operation; it never reruns work.
            if value['status'] != 'planned':
                return value
            if revision != value['revision']:
                raise Conflict('출력 계획 버전이 바뀌었습니다.')
            current = self.preview(value['plan']['job_ids'], value['plan']['destination'])
            if current['plan_hash'] != value['plan_hash']:
                raise Conflict('현재 승인·파일 근거가 바뀌었습니다. 새 계획이 필요합니다.')
            target = destination_path(value['plan']['destination']) / ('exam-export-' + value['id'])
            if target.exists():
                raise Conflict('목적지에 기존 파일이 있습니다. 덮어쓰지 않습니다.')
            value['status'] = 'running'
            self._save(value)  # durable intent precedes all output effects
            for entry in value['entries']:
                entry['status'] = 'running'
                entry['target'] = str(target / entry['job_id'])
                self._save(value)
                try:
                    if self._entry(entry['job_id'], value['plan']['destination'])['basis'] != entry['basis']:
                        raise Conflict('출력 직전 근거가 바뀌었습니다.')
                    destination_path(target).mkdir(parents=True, exist_ok=True)
                    Path(entry['target']).mkdir(exist_ok=False)
                    receipt = dict(policy=POLICY, plan_id=plan_id, plan_hash=value['plan_hash'], basis=entry['basis'])
                    result = self.store.export(entry['job_id'], entry['target'], source_code=entry['source_code'], export_receipt=receipt)
                    entry['output'] = result['last_export']
                    self._verify_output(value, entry)
                    entry['status'] = 'completed'
                except (OSError, ValueError) as exc:
                    entry['error'] = str(exc)
                    entry['status'] = 'uncertain' if any(Path(entry['target']).rglob('*')) else 'failed'
                self._save(value)
            value['status'] = 'finished'
            self._save(value)
            return value

    def _verify_output(self, value, entry):
        target = Path(entry['target'])
        destination_path(target)
        candidates = [p for p in target.rglob('manifest.json') if not any(part.startswith('.export-') for part in p.relative_to(target).parts)]
        matches = []
        for path in candidates:
            manifest = json.loads(path.read_text())
            expected = dict(policy=POLICY, plan_id=value['id'], plan_hash=value['plan_hash'], basis=entry['basis'])
            if manifest.get('export_receipt') != expected:
                continue
            if (manifest.get('job_id') != entry['job_id'] or manifest.get('items') != entry['items']
                    or manifest.get('source_code') != entry['source_code']):
                raise ValueError('출력 항목·revision 근거가 다릅니다.')
            if not manifest.get('files') or payload_files(path.parent) != manifest['files']:
                raise ValueError('출력 파일이 변경되거나 누락되었습니다. 사용자 편집본을 보존하세요.')
            matches.append(str(path.parent))
        if len(matches) != 1:
            raise ValueError('유일한 출력 근거를 확인할 수 없습니다. 자동 재출력하지 않습니다.')
        return matches[0]

    def recover(self, plan_id):
        with execution_lock(self.store):
            value = self._read(plan_id)
            if value['status'] == 'planned':
                return value
            for entry in value['entries']:
                if entry['status'] in ('pending', 'failed'):
                    continue
                try:
                    entry['output'] = self._verify_output(value, entry)
                    entry['status'] = 'completed' if entry['status'] == 'completed' else 'recovered'
                    entry['error'] = ''
                except (OSError, ValueError, KeyError) as exc:
                    entry['status'], entry['error'] = 'uncertain', str(exc)
            value['status'] = 'finished'
            self._save(value)
            return value
