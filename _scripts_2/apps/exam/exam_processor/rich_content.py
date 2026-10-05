"""Contracts for source images accompanied by editable, readable Markdown."""
import re
from html.parser import HTMLParser
from pathlib import PurePosixPath

from .domain.rich_content import (
    image_description_callout, image_description_from_markdown,
    without_image_description_callout,
)


class UnderlineParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        if tag == "u":
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "u":
            self.depth -= 1
            if self.depth < 0:
                self.invalid = True

    def handle_startendtag(self, tag, attrs):
        if tag == "u":
            self.invalid = True


def markup_errors(text):
    parser = UnderlineParser()
    parser.feed(text)
    errors = []
    if parser.depth or parser.invalid:
        errors.append("밑줄 <u> 태그의 시작과 끝을 확인하세요.")
    if re.search(r"&lt;/?u&gt;", text, re.I):
        errors.append("밑줄 태그가 문자로 저장되었습니다. 실제 <u> 태그로 수정하세요.")
    return errors


def material_errors(text, assets):
    errors = markup_errors(text)
    remaining = text
    for asset in assets:
        link = f"![[{asset['path']}]]"
        if link not in text:
            continue
        name = asset.get("id", PurePosixPath(asset["path"]).stem)
        pattern = (r"<!-- MATERIAL:" + re.escape(name) + r" START -->\s*"
                   + re.escape(link) + r"\s*(.*?)\s*<!-- MATERIAL:" + re.escape(name) + r" END -->")
        blocks = list(re.finditer(pattern, text, re.S))
        if len(blocks) != 1 or not blocks[0][1].strip():
            errors.append("자료 이미지 아래의 텍스트 전사가 없거나 연결이 잘못되었습니다. AI 재변환 또는 편집으로 보완하세요.")
        else:
            remaining = remaining.replace(blocks[0][0], "")
    # Descriptions are legitimate inside an image-linked transcription, not standalone placeholders.
    if re.search(r"원본.*확인 필요|이미지 원본 확인|\(이미지:", remaining):
        errors.append("원본 이미지 자리표시자가 남아 있습니다.")
    return errors
