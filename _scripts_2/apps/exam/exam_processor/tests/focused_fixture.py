"""Explicit synthetic R3 cases. No real job, tier override, provider, or approval."""
from pathlib import Path
from PIL import Image, ImageDraw

JOB_ID = 'f3' * 16


def create_fixture(root):
    from _scripts_2.apps.exam.exam_processor.storage.store import Store
    store = Store(Path(root), Path(root) / 'exports')
    folder = store.job_dir(JOB_ID)
    (folder / 'regions').mkdir(parents=True, exist_ok=True)
    image = Image.new('RGB', (700, 240), 'white')
    draw = ImageDraw.Draw(image)
    draw.text((25, 25), 'SYNTHETIC R3 FIXTURE - not an exam source', fill='black')
    draw.text((25, 90), 'Correct source: alpha + beta = gamma.', fill='black')
    image.save(folder / 'regions' / 'fixture.png')
    items = []
    for number, name in enumerate(('apply', 'skip', 'edit', 'ambiguous', 'warning', 'waiver', 'stale', 'multi'), 1):
        rev = 'revision-' + name
        issue = {'message': '명시적 격리 fixture: alphx 오탈자 확인', 'location': '본문 첫 문장',
                 'original': 'alpha', 'extracted': 'alphx', 'suggestion': 'alpha'}
        item = {'id': name, 'kind': 'question', 'number': str(number), 'revision': rev,
                'body': 'alphx + beta = gamma.', 'solution': '', 'answer': '', 'points': 3,
                'review': 'pending', 'note': '격리 fixture의 지적 확인용 메모',
                'regions': [{'id': 'source', 'document': 'problem', 'page': 1, 'bbox': [0, 0, 700, 240], 'width': 700, 'height': 240, 'image': 'fixture.png'}],
                'solution_regions': [], 'assets': [], 'warnings': [], 'history': [],
                'audit': {'revision': rev, 'status': 'completed', 'result': 'suspected_difference', 'issues': [issue]}}
        if name == 'ambiguous':
            item['body'] += ' alphx'
        if name in ('warning', 'waiver'):
            item['body'] = 'alpha + beta = gamma.'
            item['audit']['issues'] = []
            item['audit']['result'] = 'no_difference'
        if name == 'warning':
            item['warnings'] = ['격리 fixture: 원문 표기 확인 필요']
        if name in ('waiver', 'stale'):
            item['audit_waiver'] = {'revision': rev, 'status': 'waived', 'reason': '격리 fixture의 원본 직접 확인 기록'}
        if name == 'waiver':
            item['audit'] = None
        if name == 'stale':
            item['audit']['revision'] = 'old-revision'
        if name == 'multi':
            item['audit']['issues'].append({'message': '둘째 지적 fixture', 'original': 'beta', 'extracted': 'beta', 'suggestion': 'BETA'})
        items.append(item)
    store.save({'id': JOB_ID, 'source': 'R3 명시적 격리 fixture', 'subject': '영어', 'format': 'kice_english',
                'source_id': 'r3-fixture', 'track': '영어', 'warnings': [], 'documents': {}, 'items': items})
    return store
