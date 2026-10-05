"""Deterministic workbook metadata and presentation; raw text remains evidence."""
from __future__ import annotations

import copy
import re
from dataclasses import asdict

from .workbook_content import workbook_presentation

# Only a whole printed answer line is metadata. Never infer an answer from math.
_ANSWER = re.compile(
    r'^\s*(?:<span\s+class=[\"\']source-inline-box[\"\']>\s*정답\s*</span>\s*'
    r'|\*\*정답\s*:\s*)(?P<box><span\s+class=[\"\']source-inline-box[\"\']>)?'
    r'(?P<value>[①②③④⑤]|\d+)(?(box)</span>)(?:\*\*)?\s*$', re.M)
_STANDARD_ANSWER = re.compile(r'^\s*\*\*정답\s*:\s*(?P<value>[①②③④⑤]|\d+)\*\*\s*$', re.M)
_ANSWER_LABEL = re.compile(r'<span\s+class=[\"\']source-inline-box[\"\']>\s*정답\s*</span>')


def workbook_content(record):
    """Return raw-preserving metadata/presentation with explicit conflict reasons."""
    view = {'body': record.body, 'solution': record.solution, 'metadata': {}, 'warnings': []}
    if record.question.content_kind not in ('question', 'example'):
        return view
    is_workbook = record.source.format_id == 'hanwangi_2026_probability'
    if is_workbook:
        view = workbook_presentation(record)
    if view['warnings']:
        return view
    meta = view['metadata']
    warnings = []
    printed = meta.get('origin')
    if printed and record.origin:
        stored = asdict(record.origin)
        for key, value in printed.items():
            if key == 'raw_label' or not value or not stored.get(key):
                continue
            if stored[key] != value:
                warnings.append('표준 출처와 인쇄 출처가 다릅니다: ' + key)
    answer_pattern = _ANSWER if is_workbook else _STANDARD_ANSWER
    matches = list(answer_pattern.finditer(view['solution']))
    values = {m['value'] for m in matches}
    residue = answer_pattern.sub('', view['solution'])
    if is_workbook and _ANSWER_LABEL.search(residue):
        warnings.append('인쇄 정답 박스의 값을 확정할 수 없습니다.')
    if len(values) > 1:
        warnings.append('인쇄 정답이 서로 다릅니다.')
    if len(values) == 1:
        answer = next(iter(values))
        if record.question.answer and str(record.question.answer).strip() != answer:
            warnings.append('표준 정답과 인쇄 정답이 다릅니다.')
        else:
            meta['answer'] = answer
    if warnings:
        return {'body': record.body, 'solution': record.solution, 'metadata': meta, 'warnings': warnings}
    if matches:
        view['solution'] = residue.strip()
    return view


def populate_standard_metadata(record):
    """Fill missing canonical metadata at explicit ingestion/content-save boundaries.

    Raw body/solution retain printed labels as auditable evidence. Never overwrite
    explicit fields on conflict; projection reports the conflict on every read.
    """
    from _scripts_2.apps.exam.exam_core.domain.canonical.schema import OriginDomain
    view = workbook_content(record)
    if view['warnings']:
        return view['warnings']
    meta = view['metadata']
    if not meta:
        return []
    if meta.get('origin') and record.origin is None:
        record.origin = OriginDomain(**meta['origin'])
    if meta.get('answer') and not record.question.answer:
        record.question.answer = meta['answer']
    if meta.get('item_code'):
        record.question.display_code = meta['item_code']
    if meta.get('item_code') and not record.source.item_code:
        record.source.item_code = meta['item_code']
    if meta.get('correct_rate') is not None and record.question.correct_rate is None:
        record.question.correct_rate = meta['correct_rate']
    if meta.get('pattern') and not record.source.section_title:
        record.source.section_title = meta['pattern']
    record.source.print_labels = list(dict.fromkeys(record.source.print_labels + meta.get('print_labels', [])))
    return []


def standard_record(record):
    """Detached canonical projection for exporters and previews; never disk writes."""
    result = copy.deepcopy(record)
    warnings = populate_standard_metadata(result)
    if not warnings:
        view = workbook_content(result)
        result.body, result.solution = view['body'], view['solution']
    return result, warnings
