"""Detect and conservatively repair common math transcription mistakes."""
import re

MATH = re.compile(r"\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\$(?!\$)(?:\\.|[^\\$\n])+\$|\\\([\s\S]+?\\\)")
LATEX_CMD = re.compile(
    r"\\(?:frac|underbrace|overbrace|cdots|ldots|quad|qquad|text|left|right|"
    r"cdot|times|therefore|sum|prod|binom|sqrt|mathrm|mathbf|begin|end)"
)
_BARE_LATEX = re.compile(
    r"(?<![\\$])"
    r"(\{?[=_\s]*(?:_[a-zA-Z0-9]+)?[^$\n]*?"
    r"\\(?:frac|underbrace|overbrace|cdots|quad|text\{)[^$\n]*)"
)


def _repair(match):
    source = match.group(0)
    if source.startswith("$$") or source.startswith("\\["):
        start, end = source[:2], source[-2:]
    elif source.startswith("\\("):
        start, end = source[:2], source[-2:]
    else:
        start, end = source[0], source[-1]
    tex = source[len(start):-len(end)]
    repaired = re.sub(r"\bherefore(?=\s*[A-Za-z0-9({])", r"\\therefore", tex)
    # In ``2imes5`` the command loses its backslash and is adjacent to the
    # next operand; do not touch the ``imes`` suffix of an existing ``\times``.
    repaired = re.sub(r"(?<![\\t])imes", r"\\times", repaired)
    return start + repaired + end


def inspect_math(text, section):
    """Return repaired text and auditable checks; prose is never normalized."""
    checks = []

    def replace(match):
        original = match.group(0)
        repaired = _repair(match)
        if repaired != original:
            checks.append({"check": "math_transcription", "section": section,
                           "source": original, "normalized": repaired,
                           "result": "auto_fixed", "review_required": True})
        return repaired

    return MATH.sub(replace, str(text or "")), checks


def prepare_audit_suggestion(suggestion, extracted):
    """Wrap bare LaTeX audit suggestions in math delimiters when appropriate."""
    sug = (suggestion or "").strip()
    ext = (extracted or "").strip()
    if not sug or not LATEX_CMD.search(sug):
        return suggestion
    if re.fullmatch(r"\$(?:\\.|[^$\\])+\$", sug, re.DOTALL) or re.fullmatch(r"\$\$.+\$\$", sug, re.DOTALL):
        return sug
    core = sug.strip("$").strip()
    if "$" in ext or LATEX_CMD.search(ext) or LATEX_CMD.search(core):
        return f"$$\n{core}\n$$" if "\n" in core else f"${core}$"
    return suggestion


def _collapse_spurious_inline_dollars(text):
    return re.sub(r"\$\s+(?=(?:\\(?:underbrace|frac|cdots|quad|text|left)))", "", text)


def _rest_is_math_delimited(rest):
    stripped = rest.strip()
    if stripped.startswith("$$") and stripped.endswith("$$"):
        return True
    if stripped.startswith("$") and stripped.endswith("$") and stripped.count("$") == 2:
        return True
    return False


def _repair_line_bare_latex(line):
    prefix_m = re.match(r"^(>\s*)", line)
    prefix = prefix_m.group(1) if prefix_m else ""
    rest = line[len(prefix) :]
    if not LATEX_CMD.search(rest):
        return line
    if _rest_is_math_delimited(rest):
        return line
    if re.search(r"[가-힣]", rest):
        fixed = _repair_bare_in_plain(rest)
        return prefix + fixed if fixed != rest else line
    core = rest.strip().strip("$").strip()
    if not core:
        return line
    wrapped = f"$$\n{core}\n$$" if "\n" in core else f"${core}$"
    return prefix + wrapped


def _repair_bare_in_plain(segment):
    if not LATEX_CMD.search(segment):
        return segment

    def repl(match):
        raw = match.group(1)
        if "$" in raw:
            return match.group(0)
        stripped = raw.strip().strip("$").strip()
        if not stripped:
            return match.group(0)
        return match.group(0).replace(raw, f"${stripped}$", 1)

    return _BARE_LATEX.sub(repl, segment)


def repair_unwrapped_latex(text):
    """Wrap obvious LaTeX command runs that sit outside $...$ delimiters."""
    text = _collapse_spurious_inline_dollars(str(text or ""))
    lines = [_repair_line_bare_latex(line) for line in text.splitlines()]
    text = "\n".join(lines)
    chunks, pos = [], 0
    for match in MATH.finditer(text):
        chunks.append(_repair_bare_in_plain(text[pos : match.start()]))
        chunks.append(match.group(0))
        pos = match.end()
    chunks.append(_repair_bare_in_plain(text[pos:]))
    return "".join(chunks)


def normalize_math_section(text, section):
    from .text_layout import normalize_markdown

    text = normalize_markdown(str(text or ""))
    text = repair_unwrapped_latex(text)
    return inspect_math(text, section)
