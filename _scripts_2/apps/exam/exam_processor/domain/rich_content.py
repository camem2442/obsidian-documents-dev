"""Contracts for source images accompanied by editable, readable Markdown."""
import re
from html.parser import HTMLParser
from pathlib import PurePosixPath


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


def image_description_callout(description):
    """Render an optional visual description as a collapsed Obsidian callout."""
    text = str(description or "").strip()
    if not text:
        return ""
    return "> [!info]- 이미지 설명\n" + "\n".join(
        ">" if not line.strip() else f"> {line.rstrip()}"
        for line in text.splitlines()
    )


def without_image_description_callout(text):
    """Remove a leading generated description callout before checking source transcription."""
    lines = str(text or "").splitlines()
    if not lines or lines[0].strip() != "> [!info]- 이미지 설명":
        return str(text or "").strip()
    index = 1
    while index < len(lines) and (lines[index].startswith(">") or not lines[index].strip()):
        index += 1
    return "\n".join(lines[index:]).strip()


def image_description_from_markdown(text, asset_path):
    """Read a generated description callout following one asset link."""
    link = f"![[{asset_path}]]"
    if link not in str(text or ""):
        return ""
    following = str(text).split(link, 1)[1].lstrip()
    lines = following.splitlines()
    if not lines or lines[0].strip() != "> [!info]- 이미지 설명":
        return ""
    description = []
    for line in lines[1:]:
        if not line.startswith(">"):
            break
        value = line[1:]
        description.append(value[1:] if value.startswith(" ") else value)
    return "\n".join(description).strip()


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
        if len(blocks) != 1 or not without_image_description_callout(blocks[0][1]):
            errors.append("자료 이미지 아래의 텍스트 전사가 없거나 연결이 잘못되었습니다. AI 재변환 또는 편집으로 보완하세요.")
        else:
            remaining = remaining.replace(blocks[0][0], "")
    # Descriptions are legitimate inside an image-linked transcription, not standalone placeholders.
    if re.search(r"원본.*확인 필요|이미지 원본 확인|\(이미지:", remaining):
        errors.append("원본 이미지 자리표시자가 남아 있습니다.")
    return errors
