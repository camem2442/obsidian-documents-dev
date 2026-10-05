"""Reflow Korean prose without discarding explicit Markdown block boundaries."""
import re

LABEL = re.compile(r"^(?:\[[^\]\n]{1,40}\]|<보\s*기>|\([가-하]\))$")
CHOICE = re.compile(r"^[①②③④⑤]")
ENTRY = re.compile(r"^(?:[ㄱ-ㅎ]\.|[-+*◦•○])\s+")
SPEAKER = re.compile(r"^(?:\*\*)?[가-힣A-Za-z0-9 ]{1,24}\s*[:：]")


def join_prose(parts):
    text = parts[0] if parts else ""
    for part in parts[1:]:
        # A copula stranded by a physical wrap belongs to the preceding Korean word.
        last_korean = re.search(r"([가-힣]+)$", text)
        continuation = re.match(r"^([가-힣]+)", part)
        stranded_syllable = bool(
            last_korean and len(last_korean[1]) == 1 and continuation
            and re.match(r"^[가-힣](?:[은는이가을를에의와과도만]|으로|부터|까지|라고|이라고|[.,?!]|$)", continuation[1])
        )
        separator = "" if stranded_syllable or (
            re.search(r"[가-힣]$", text) and re.match(r"^이다(?:[.?!,]|$)", part)
        ) else " "
        text += separator + part
    return text


def normalize_markdown(text):
    rows = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks, pending = [], []
    kind = "prose"

    def flush():
        if pending:
            blocks.append((kind, join_prose(pending)))
            pending.clear()

    index = 0
    while index < len(rows):
        raw = rows[index]
        line = raw.strip()
        index += 1
        if not line:
            flush()
            continue
        fence = re.match(r"^(`{3,}|~{3,}|\$\$)", line)
        if fence:
            flush()
            chunk = [raw]
            delimiter = fence[1]
            if not (delimiter == "$$" and line.endswith("$$") and len(line) > 4):
                while index < len(rows):
                    chunk.append(rows[index])
                    index += 1
                    if re.fullmatch(re.escape(delimiter) + r"\s*", chunk[-1].strip()):
                        break
            blocks.append(("protected", "\n".join(chunk)))
            continue
        if line.startswith(">"):
            flush()
            quoted = [re.sub(r"^\s*> ?", "", raw)]
            while index < len(rows) and rows[index].lstrip().startswith(">"):
                quoted.append(re.sub(r"^\s*> ?", "", rows[index]))
                index += 1
            content = normalize_markdown("\n".join(quoted))
            blocks.append(("quote", "\n".join("> " + row if row else ">" for row in content.split("\n"))))
            continue
        if line.startswith("|") or ("|" in line and index < len(rows) and re.match(r"^\s*\|?\s*:?-{3,}", rows[index])):
            flush()
            chunk = [raw]
            while index < len(rows) and "|" in rows[index] and rows[index].strip():
                chunk.append(rows[index])
                index += 1
            blocks.append(("table", "\n".join(chunk)))
            continue
        if (line.startswith("<!--") or line.startswith("![") or re.match(r"^#{1,6}\s", line)
                or re.fullmatch(r"(?:---+|\*\*\*+|___+)", line)):
            flush()
            blocks.append(("protected", line))
            continue
        if LABEL.match(line) or re.match(r"^\[![\w가-힣-]+\]", line) or re.fullmatch(r"\*\*[^*]+\*\*", line):
            flush()
            blocks.append(("label", line))
            continue
        next_kind = "choice" if CHOICE.match(line) else "entry" if ENTRY.match(line) else "speaker" if SPEAKER.match(line) else None
        if next_kind:
            flush()
            kind = next_kind
        elif not pending:
            kind = "prose"
        pending.append(re.sub(r"\\$", "", line).rstrip())
    flush()
    result, previous = "", None
    for current, content in blocks:
        separator = "\n" if previous == "label" or current == previous and current in ("entry", "choice") else "\n\n"
        result += (separator if result else "") + content
        previous = current
    return result
