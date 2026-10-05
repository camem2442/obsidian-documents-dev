"""Lossless span adapters. These recognize two explicit grammars, not arbitrary Markdown."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ProblemSpan:
    key: str
    body: str
    solution: str
    start: int  # Unicode character offsets, not bytes
    end: int


def parse_study_markdown(text: str, adapter: str) -> list[ProblemSpan]:
    if '![' in text or '![[' in text or '```' in text or '~~~' in text:
        raise ValueError('Embedded assets and fenced code require another adapter')
    rows = text.splitlines(keepends=True)
    spans = []
    offset = 0
    if adapter == 'numbered-qa-v1':
        current = None
        answers = []
        for line in rows:
            match = re.fullmatch(r'\t(\d+)\. (.+?)(?:\r?\n)?', line)
            if match:
                if current:
                    if not answers:
                        raise ValueError('Missing explicit answer')
                    spans.append(ProblemSpan(*current[:2], ''.join(answers), current[2], offset))
                current = (match[1], match[2], offset)
                answers = []
            elif line.startswith('\t\t○ ') and current:
                answers.append(line[len('\t\t○ '):])
            elif line.strip():
                raise ValueError('Unsupported numbered QA line')
            offset += len(line)
        if current and answers:
            spans.append(ProblemSpan(*current[:2], ''.join(answers), current[2], offset))
        else:
            raise ValueError('Missing explicit question/answer')
    elif adapter == 'numbered-table-v1':
        header_seen = False
        separator_seen = False
        for line in rows:
            raw = line.rstrip('\r\n')
            if not raw.strip():
                offset += len(line)
                continue
            cells = raw.split('|')
            if len(cells) != 5 or cells[0] or cells[-1] or '\\|' in raw:
                raise ValueError('Expected exactly three table cells without escaped pipes')
            key, body, solution = cells[1:4]
            if not header_seen and all(not cell.strip() for cell in cells[1:4]):
                header_seen = True
            elif header_seen and not separator_seen and all(re.fullmatch(r':?-{3,}:?', cell.strip()) for cell in cells[1:4]):
                separator_seen = True
            elif separator_seen and key.isdigit() and body.strip() and solution.strip():
                spans.append(ProblemSpan(key, body, solution, offset, offset + len(line)))
            else:
                raise ValueError('Unsupported table row')
            offset += len(line)
    else:
        raise ValueError(f'Unknown adapter: {adapter}')
    keys = [span.key for span in spans]
    if not keys or len(keys) != len(set(keys)):
        raise ValueError('Empty input or duplicate question keys')
    return spans
