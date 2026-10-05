"""Read-only input contracts; parsing and publication remain in the existing services."""
import hashlib
import json
import re
from pathlib import Path

from .formats import FORMATS, FormatRequest
from .study_markdown import parse_study_markdown
from ..storage.sample_review import digest

POLICY = 'multi-input/1'
STUDY = ('numbered-qa-v1', 'numbered-table-v1')
FILES = ('path', 'solution', 'script', 'answer', 'structure', 'layout')
FIELDS = (*FILES, 'format', 'source', 'track', 'pages', 'subject', 'question_type', 'unit',
          'repo_root', 'relationship_confirmed', 'included', 'exclude_reason', 'id')


def catalog():
    return [dict(id=k, label=v.label, tracks=sorted(v.supported_tracks), extensions=sorted(v.extensions))
            for k, v in FORMATS.items()] + [dict(id=k, label='공부 Markdown · '+k, tracks=[], extensions=['.md']) for k in STUDY]


def normalize(raw):
    if not isinstance(raw, dict) or set(raw) - set(FIELDS):
        raise ValueError('알 수 없는 입력 필드입니다.')
    value = {k: raw.get(k, '') for k in FIELDS}
    if not isinstance(value['id'], str) or not re.fullmatch('[a-zA-Z0-9_-]{1,80}', value['id']):
        raise ValueError('입력 단위 ID가 필요합니다.')
    for k in FIELDS:
        if k not in ('included', 'relationship_confirmed'):
            if not isinstance(value[k], str): raise ValueError('입력 조건은 문자열이어야 합니다: '+k)
            value[k] = value[k].strip()
    for k in ('included', 'relationship_confirmed'):
        value[k] = raw.get(k, k == 'included')
        if not isinstance(value[k], bool): raise ValueError('선택·관계 확인 값이 잘못되었습니다.')
    for k in (*FILES, 'repo_root'):
        if value[k]: value[k] = str(Path(value[k]).expanduser().resolve())
    return value


def inspect(raw):
    v = normalize(raw)
    errors, evidence = [], {}
    fmt = v['format']
    state = 'ready'
    if fmt not in (*FORMATS, *STUDY):
        state = 'unsupported' if fmt else 'ambiguous'
        errors.append('형식을 명시적으로 선택하세요. 확장자·파일명만으로 형식을 확정하지 않습니다.')
    for role in FILES:
        if not v[role]: continue
        p = Path(v[role])
        try:
            if not p.is_file(): raise ValueError('일반 파일이 아닙니다.')
            raw_bytes = p.read_bytes()
            evidence[role] = {'path': str(p), 'sha256': hashlib.sha256(raw_bytes).hexdigest(),
                              'size': len(raw_bytes), 'suffix': p.suffix.lower()}
        except (OSError, ValueError) as exc: errors.append(role+': 파일을 읽을 수 없습니다. '+str(exc))
    try:
        if 'path' not in evidence: raise ValueError('원본 파일을 지정하세요.')
        if any(v[k] and k not in evidence for k in FILES): raise ValueError('관련 파일을 확인하세요.')
        if any(v[k] for k in FILES[1:]) and not v['relationship_confirmed']:
            raise ValueError('관련 파일의 역할과 같은 입력 단위에 속하는 관계를 명시적으로 확인하세요.')
        if len({v[k] for k in FILES if v[k]}) != sum(bool(v[k]) for k in FILES):
            raise ValueError('같은 파일을 서로 다른 역할로 중복 지정할 수 없습니다.')
        if fmt in STUDY:
            if any(v[k] for k in (*FILES[1:], 'pages', 'source', 'track')):
                raise ValueError('공부 어댑터는 단일 Markdown·과목·유형·단원을 사용합니다. PDF/관련 파일 조건은 비우세요.')
            if not v['repo_root'] or not v['subject'] or not v['question_type']:
                raise ValueError('공부 자료의 원본 루트·과목·문항 유형이 필요합니다.')
            Path(v['path']).relative_to(Path(v['repo_root']))
            if Path(v['path']).suffix != '.md': raise ValueError('Markdown 파일이 필요합니다.')
            parse_study_markdown(Path(v['path']).read_text(), fmt)
        elif fmt in FORMATS:
            if any(v[k] for k in ('repo_root','subject','question_type','unit')):
                raise ValueError('이 형식은 공부 어댑터 전용 옵션을 사용하지 않습니다.')
            if not v['source'] or len(v['source']) > 180: raise ValueError('출처명을 180자 이내로 지정하세요.')
            module = FORMATS[fmt]
            request = FormatRequest(Path(v['path']), Path(v['solution']) if v['solution'] else None,
                                    'preview', v['track'], v['pages'], None, source=v['source'])
            module.validate(request)
            if (v['script'] or v['answer']) and fmt != 'kice_english':
                raise ValueError('대본·정답 연결은 평가원 영어만 지원합니다.')
            if fmt != 'hanwangi_2026_probability' and (v['structure'] or v['layout']):
                raise ValueError('구조·영역 기록은 한완기 형식에만 지정하세요.')
            if v['solution'] and Path(v['solution']).suffix.lower() not in ('.md','.pdf'):
                raise ValueError('해설 파일은 Markdown 또는 PDF여야 합니다.')
            if v['script'] and Path(v['script']).suffix.lower() != '.pdf': raise ValueError('대본 PDF가 필요합니다.')
            if v['answer'] and Path(v['answer']).suffix.lower() not in ('.pdf','.png'): raise ValueError('정답 PDF/PNG가 필요합니다.')
            if fmt == 'korean_notes':
                if v['pages']: raise ValueError('Markdown에는 페이지 범위를 적용하지 않습니다.')
                module.parse(request)  # canonical in memory; this adapter performs no writes
            else:
                import pdfplumber
                from .formats.korean.kice_korean import page_range
                with pdfplumber.open(v['path']) as pdf:
                    selected = page_range(v['pages'], len(pdf.pages))
                    if selected != list(range(selected[0], selected[-1]+1)):
                        raise ValueError('지문 연결을 위해 연속 페이지를 지정하세요.')
                    header = (pdf.pages[0].extract_text() or '')[:300]
                if fmt == 'kice_english':
                    match = re.search(r'(\d{4})학년도\s*(?:(\d{1,2})월|(?:대학수학능력시험|수능))', v['source'])
                    if not match: raise ValueError('영어 출처의 학년도·월/수능을 지정하세요.')
                    event = f'{int(match[2])}월' if match[2] else '대학수학능력시험'
                    if match[1]+'학년도' not in header or event not in header:
                        raise ValueError('영어 출처와 첫 쪽 표제가 다릅니다.')
                if fmt == 'hanwangi_2026_probability':
                    from ..pipeline.workbook import read_structure, structure_errors
                    from .formats.math.workbook_layout import validate_layout
                    if not all(v[k] for k in ('solution','structure','layout')): raise ValueError('한완기는 해설·구조·확인된 영역 기록이 필요합니다.')
                    if v['pages']: raise ValueError('한완기 범위는 영역 기록으로 지정합니다. 페이지는 비우세요.')
                    structure = read_structure(v['structure'])
                    if structure.get('profile') != fmt or structure_errors(structure): raise ValueError('지원 판본의 구조 기록이 아닙니다.')
                    for role, key in (('problem','path'),('solution','solution')):
                        if structure['documents'][role]['sha256'] != evidence[key]['sha256']: raise ValueError('구조 기록과 원본 해시가 다릅니다: '+role)
                    validate_layout(json.loads(Path(v['layout']).read_text()), structure)
    except Exception as exc:
        errors.append(str(exc))
    if errors and state == 'ready': state = 'missing_conditions'
    if not v['included'] and not v['exclude_reason']: errors.append('제외 사유를 입력하세요.')
    # Content identity excludes paths and selection; role, suffix, bytes and every applied option remain.
    identity = digest({'policy': POLICY, 'files': {k:{x:y for x,y in e.items() if x!='path'} for k,e in evidence.items()},
                       'options':{k:v[k] for k in ('format','source','track','pages','subject','question_type','unit')},
                       'parser': FORMATS[fmt].parser_version if fmt in FORMATS else fmt})
    return {'id':v['id'],'input':v,'evidence':evidence,'identity':identity,'state':state,
            'reasons':errors,'included':v['included'], 'existing_jobs':[]}
