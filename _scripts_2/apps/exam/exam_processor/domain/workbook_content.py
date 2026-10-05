"""Read-only workbook presentation and printed-label metadata extraction.

The source transcription stays in the job for auditing and editing. Only recognized
leading labels are omitted from the review/output projection.
"""
from __future__ import annotations

import re


_CODE = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?(?P<code>[A-Z]\d?[-·.]\d{2})(?:\*\*)?\s*(?P<rest>.*)$")
_ORIGIN = re.compile(r"(?P<year>\d{4})(?:\.(?P<session>\d{1,2}))?·(?P<tracks>[^|\n]+?\d+번(?:\s*,\s*[^|\n]+?\d+번)?)")
_RATE = re.compile(r"정답률\s*(?P<rate>\d+(?:\.\d+)?)\s*%")
_PATTERN = re.compile(r"Pattern\s+(?P<pattern>\d+)", re.I)
_PRINT_LABEL = re.compile(r"CHALLENGE|해설\s+ANALYSIS", re.I)
_VARIANT = re.compile(r"(?P<track>가(?:형)?(?:\s+이산)?|나(?:형)?(?:\s+이산)?|A(?:형)?|B(?:형)?|확통|미적|기하)\s*(?P<number>\d+)번")
_SHARED_VARIANTS = re.compile(r"(?P<first>가|나|A|B)\s*,\s*(?P<second>가|나|A|B)\s*(?P<number>\d+)번")
_METHOD = re.compile(r"(?:교과서적|수능적)\s*해법(?:\s*\d+)?")
_TRACK_NAMES = {"확통": "확률과 통계", "미적": "미적분", "기하": "기하"}


def _normalized_code(code):
    return re.sub(r"[^A-Za-z0-9]", "", code or "").upper()


def _origin_metadata(label, item_code):
    match = _ORIGIN.search(label)
    if not match:
        return None
    detail = match.group("tracks").strip()
    shared = _SHARED_VARIANTS.fullmatch(detail)
    if shared:
        variants = [{"track": track, "question_number": shared.group("number")}
                    for track in (shared.group("first"), shared.group("second"))]
    else:
        variants = [{"track": _TRACK_NAMES.get(part.group("track"), part.group("track")),
                     "question_number": part.group("number")}
                    for part in _VARIANT.finditer(detail)]
        if not variants or not re.sub(r"[\s,]", "", _VARIANT.sub("", detail)) == "":
            return None
    # This parser is used for the Hanwangi edition: its printed source labels
    # identify KICE past-exam questions, even when the session is omitted.
    origin = {"type": "kice", "authority": "KICE",
              "academic_year": match.group("year"),
              "session": match.group("session").zfill(2) if match.group("session") else "",
              "raw_label": match.group(0).strip(), "variants": variants}
    if item_code == "D1-13":
        origin["session"] = "09"  # confirmed from the source review
    if len(variants) == 1:
        origin["track"] = variants[0]["track"]
        origin["question_number"] = variants[0]["question_number"]
    return origin


def _strip_header(text, number):
    """Return text and metadata only when the leading code matches this item."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return text, {}
    if "<br" in lines[0].lower():
        boundary = re.search(r"<br\s*/?>\s*<br\s*/?>", text, re.I)
        if not boundary:
            return text, {}
        header = re.sub(r"<br\s*/?>", " ", text[:boundary.start()], flags=re.I)
        projected, meta = _strip_header(header + "\n\n" + text[boundary.end():], number)
        return (projected, meta) if meta else (text, {})
    match = _CODE.match(lines[0].rstrip("\r\n"))
    if not match or _normalized_code(match.group("code")) != _normalized_code(number):
        return text, {}
    code = match.group("code").replace("·", "-").replace(".", "-")
    rest = match.group("rest")
    plain = re.sub(r"<[^>]+>", "", rest)
    origin = _origin_metadata(plain, code)
    rate_match = _RATE.search(plain)
    rate = float(rate_match.group("rate")) / 100 if rate_match else None
    if rate is not None and not 0 <= rate <= 1:
        rate_match = None
        rate = None
    pattern_match = _PATTERN.search(plain)
    labels = _PRINT_LABEL.findall(plain)
    cleaned = plain
    for found in sorted((x for x in (_ORIGIN.search(plain) if origin else None, rate_match, pattern_match) if x),
                        key=lambda x: x.start(), reverse=True):
        cleaned = cleaned[:found.start()] + cleaned[found.end():]
    cleaned = _PRINT_LABEL.sub("", cleaned)
    cleaned = cleaned.strip(" |·\t")
    if "<" in rest and cleaned:
        return text, {}  # do not leave orphan markup around partly recognized labels
    # Printed labels may occupy the next few lines instead of the code line.
    for index in range(1, min(len(lines), 8)):
        if not lines[index].strip():
            continue
        next_plain = re.sub(r"<[^>]+>", "", lines[index]).strip(" |\t\r\n")
        next_origin = _origin_metadata(next_plain, code)
        next_rate = _RATE.search(next_plain)
        next_pattern = _PATTERN.search(next_plain)
        residue = next_plain
        for found in sorted((x for x in (_ORIGIN.search(next_plain) if next_origin else None,
                                         next_rate, next_pattern) if x), key=lambda x: x.start(), reverse=True):
            residue = residue[:found.start()] + residue[found.end():]
        if not (next_origin or next_rate or next_pattern):
            break
        inline_method = (next_rate and next_pattern
                         and re.match(r"^\s*교과서적\s*해법\s+", residue))
        if residue.strip(" |\t") and not inline_method:
            break
        if next_origin and origin and next_origin != origin:
            return text, {}
        if next_rate and not 0 <= float(next_rate.group("rate")) / 100 <= 1:
            return text, {}
        origin = origin or next_origin
        if next_rate and rate is None:
            rate = float(next_rate.group("rate")) / 100
        if next_pattern and not pattern_match:
            pattern_match = next_pattern
        lines[index] = (re.sub(r"^\s*교과서적\s*해법\s+", "", residue) + "\n") if inline_method else ""
    # A leading code by itself (or with recognized labels) is display metadata.
    result = ((cleaned + "\n") if cleaned else "") + "".join(lines[1:])
    return result.lstrip("\n"), {"item_code": code, "origin": origin, "correct_rate": rate,
                                     "pattern": f"Pattern {pattern_match.group('pattern')}" if pattern_match else None,
                                     "print_labels": labels}


def _strip_repeated_question(body, solution):
    """Remove a copied question block only with an exact opening and clear analysis boundary."""
    first_paragraph = body.split("\n\n", 1)[0].strip()
    if len(first_paragraph) < 40 or not solution.startswith(first_paragraph):
        return solution
    boundary = re.search(r"(?:\n|<br\s*/?>)\s*발문\s+분석\b", solution[:3000], re.I)
    if not boundary:
        return solution
    return solution[boundary.start():].lstrip("\n <br>")


def _strip_single_method_heading(solution):
    if re.search(r"수능적\s*해법", solution):
        return solution
    lines = solution.splitlines(keepends=True)
    headings = []
    for index, line in enumerate(lines):
        plain = re.sub(r"<[^>]+>", "", line).strip().strip("#* ")
        if _METHOD.fullmatch(plain):
            headings.append((index, plain))
    if len(headings) != 1 or headings[0][1] != "교과서적 해법":
        return solution
    lines.pop(headings[0][0])
    return "".join(lines).lstrip("\n")


def _strip_embedded_metadata(solution, number):
    """Handle a source figure that precedes its printed solution label."""
    lines = solution.splitlines(keepends=True)
    if not lines or not solution.lstrip().startswith("<!-- MATERIAL:"):
        return solution, {}
    for index, line in enumerate(lines[:24]):
        match = _CODE.match(line.rstrip("\r\n"))
        if not match or _normalized_code(match.group("code")) != _normalized_code(number):
            continue
        if not (_ORIGIN.search(match.group("rest")) or _RATE.search(match.group("rest"))):
            continue
        projected, meta = _strip_header("".join(lines[index:]), number)
        if meta:
            return "".join(lines[:index]) + projected, meta
    return solution, {}


def workbook_presentation(item):
    """Compute a clean view and metadata without mutating an approved transcription."""
    if hasattr(item, "question"):
        raw_body = item.body
        raw_solution = item.solution
        raw_number = item.question.question_number
    else:
        raw_body = item.get("body", "")
        raw_solution = item.get("solution", "")
        raw_number = item.get("number", "")

    body, body_meta = _strip_header(raw_body, raw_number)
    solution, solution_meta = _strip_header(raw_solution, raw_number)
    if not solution_meta:
        solution, solution_meta = _strip_embedded_metadata(solution, raw_number)
    conflicts = [key for key in ("origin", "correct_rate", "pattern")
                 if body_meta.get(key) is not None and solution_meta.get(key) is not None
                 and body_meta[key] != solution_meta[key]]
    if conflicts:
        return {"body": raw_body, "solution": raw_solution,
                "metadata": {}, "warnings": ["문제·해설 머리말의 메타데이터가 다릅니다: " + ", ".join(conflicts)]}
    metadata = {key: solution_meta.get(key) or body_meta.get(key)
                for key in ("item_code", "origin", "correct_rate", "pattern")}
    metadata["print_labels"] = list(dict.fromkeys(body_meta.get("print_labels", []) + solution_meta.get("print_labels", [])))
    solution = _strip_repeated_question(body, solution)
    return {"body": body, "solution": _strip_single_method_heading(solution),
            "metadata": metadata, "warnings": []}
