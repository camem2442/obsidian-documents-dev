"""Read-only R3 cards derived from the existing review decision and audit evidence."""
from __future__ import annotations

import hashlib
import json


def issue_token(audit, index):
    return hashlib.sha256(json.dumps([audit, index], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def issue_target(item, issue):
    """Never infer a section or crop from free-form location text."""
    extracted = issue.get('extracted')
    if not isinstance(extracted, str) or not extracted:
        return None, None
    matches = []
    for key in ('body', 'solution'):
        text = item.get(key, '')
        start = text.find(extracted)
        while start >= 0:
            matches.append(key)
            if len(matches) > 1:
                return None, None
            # Overlapping matches are ambiguous too (e.g. aa in aaa).
            start = text.find(extracted, start + 1)
    if len(matches) != 1:
        return None, None
    section = matches[0]
    if issue.get('section') and issue['section'] != section:
        return None, None
    regions = item.get('regions' if section == 'body' else 'solution_regions', [])
    if issue.get('region_id'):
        regions = [r for r in regions if r.get('id') == issue['region_id']]
    region = regions[0] if len(regions) == 1 and regions[0].get('image') else None
    return section, region


def focused_review(item, *, writable=True, image_exists=lambda region: True):
    decision = item.get('review_decision') or {}
    audit = item.get('audit') or {}
    current = audit.get('revision') == item.get('revision') and audit.get('status') == 'completed'
    eligible = decision.get('tier') == 'YELLOW' and item.get('kind') == 'question' and item.get('review') == 'pending'
    cards = []
    for index, issue in enumerate(audit.get('issues') or []):
        section, region = issue_target(item, issue)
        if region and not image_exists(region):
            region = None
        blocked = []
        if not current:
            blocked.append('이전 버전 또는 미완료 지적입니다. 현재 내용 수정에 사용할 수 없습니다. 상세 검수에서 재대조 필요 상태를 확인하세요.')
        if not writable:
            blocked.append('이전 저장 형식은 읽기 전용입니다. 상세 검수에서 저장 경계를 확인하세요.')
        if not section or not region:
            blocked.append('원본 영역 또는 수정 위치가 불명확합니다. 상세 검수에서 확인하세요.')
        if issue.get('applied') or issue.get('skipped'):
            blocked.append('이미 적용하거나 사유를 남겨 스킵한 지적입니다.')
        actionable = eligible and current and writable and not issue.get('applied') and not issue.get('skipped')
        suggestion = issue.get('suggestion')
        cards.append({
            'kind': 'issue', 'index': index, 'token': issue_token(audit, index),
            'message': issue.get('message') or 'AI 지적을 원본과 확인하세요.',
            'location': issue.get('location', ''), 'original': issue.get('original', ''),
            'extracted': issue.get('extracted', ''), 'suggestion': suggestion or '',
            'section': section, 'region': region, 'blocked': blocked,
            'status': 'applied' if issue.get('applied') else 'skipped' if issue.get('skipped') else 'open',
            'skip_note': issue.get('skip_note', ''),
            'can_apply': bool(actionable and section and region and isinstance(suggestion, str)
                              and suggestion and suggestion != issue.get('extracted')),
            'can_edit': bool(actionable and section and region),
            'can_skip': bool(actionable),
        })
    waiver = item.get('audit_waiver') or {}
    if waiver.get('status') == 'waived' and waiver.get('revision') == item.get('revision'):
        cards.append({'kind': 'waiver', 'message': 'AI 원본 대조 생략: ' + waiver.get('reason', ''),
                      'blocked': ['원본과 표준 본문을 직접 확인하고 필요한 수정은 상세 검수에서 진행하세요. 적용할 AI 제안은 없습니다.']})
    for warning in item.get('warnings') or []:
        cards.append({'kind': 'warning', 'message': str(warning),
                      'blocked': ['경고의 근거와 가능한 수정은 상세 검수에서 확인하세요. 적용할 AI 제안은 없습니다.']})
    if not cards:
        cards.append({'kind': 'attention', 'message': '차이 의심 판정 · 개별 AI 지적 없음',
                      'blocked': ['전체 원본과 표준 본문을 상세 검수에서 확인하세요. 적용할 제안은 없습니다.']})
    return {'revision': item.get('revision'),
            'review_revision': decision.get('basis_review_state_revision_id'),
            'eligible': eligible, 'tier': decision.get('tier'), 'cards': cards,
            'preview': item.get('standard_preview') or {}, 'approval_gate': item.get('approval_gate') or {}}
