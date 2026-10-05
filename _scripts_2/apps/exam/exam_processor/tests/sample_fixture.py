"""Explicit synthetic GREEN evidence for R4; no real jobs or forced tiers."""
from pathlib import Path
from PIL import Image, ImageDraw

JOB_ID = 'f4' * 16


def create_fixture(root):
    from _scripts_2.apps.exam.exam_processor.storage.store import Store
    store = Store(Path(root), Path(root) / 'exports')
    folder = store.job_dir(JOB_ID)
    (folder / 'regions').mkdir(parents=True, exist_ok=True)
    image = Image.new('RGB', (720, 220), 'white')
    draw = ImageDraw.Draw(image)
    draw.text((25, 25), 'SYNTHETIC R4 FIXTURE - NOT AN EXAM SOURCE', fill='black')
    draw.text((25, 90), 'alpha + beta = gamma. Sample review does not approve questions.', fill='black')
    image.save(folder / 'regions' / 'fixture.png')
    items = []
    for number in range(1, 5):
        rid = 'q' + str(number)
        items.append({'id': rid, 'kind': 'question', 'number': str(number), 'revision': 'revision-' + rid,
                      'body': 'alpha + beta = gamma. $x^2$', 'solution': '', 'answer': '', 'points': 3,
                      'review': 'pending', 'regions': [{'id': 'source', 'document': 'problem', 'page': 1,
                      'bbox': [0, 0, 720, 220], 'width': 720, 'height': 220, 'image': 'fixture.png'}],
                      'solution_regions': [], 'assets': [], 'warnings': [], 'history': [],
                      'audit': {'revision': 'revision-' + rid, 'status': 'completed', 'result': 'no_difference', 'issues': []}})
    passage = dict(items[0], id='p1', kind='passage', number='shared', revision='revision-p1', review='approved',
                   body='alpha + beta = gamma.', audit={'revision':'revision-p1','status':'completed','result':'no_difference','issues':[]})
    items[0]['passage_id'] = 'p1'
    items.append(passage)
    store.save({'id': JOB_ID, 'source': 'R4 명시적 격리 fixture', 'subject': '영어', 'format': 'kice_english',
                'source_id': 'r4-fixture', 'track': '영어', 'warnings': [], 'documents': {}, 'items': items,
                'ai_log': [{'message': 'preserve fixture runtime'}]})
    return store
