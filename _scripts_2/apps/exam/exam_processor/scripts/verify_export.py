"""Verify an exported Exam Processor batch without opening Obsidian."""
import argparse
import hashlib
import json
import re
from pathlib import Path


DEFAULT_BAD_FRAGMENTS = [
    "\uf149", "사이 테스", "질 문", "내 용", "정 도", "하 는", "된정보",
    "평가 를", "협약’ 이라고",
]


def parse_answers(values):
    answers = {}
    for value in values:
        if "=" not in value:
            raise ValueError("정답은 35=④ 형식으로 입력하세요.")
        number, answer = value.split("=", 1)
        answers[number.strip()] = answer.strip()
    return answers


def verify(export_dir, code, expected_answers=None, bad_fragments=None):
    export = Path(export_dir).expanduser().resolve()
    if not export.is_dir():
        raise ValueError(f"출력 폴더가 없습니다: {export}")
    bad_fragments = DEFAULT_BAD_FRAGMENTS if bad_fragments is None else bad_fragments
    expected_answers = expected_answers or {}
    markdown_files = sorted(export.rglob("*.md"))
    combined = sorted(path for path in markdown_files if re.fullmatch(rf"{re.escape(code)}_\d+\.md", path.name))
    questions = sorted(
        path for path in markdown_files
        if re.fullmatch(rf"{re.escape(code)}(?:3[5-9]|4[0-5])\.md", path.name)
    )
    asset_manifest_path = export / "asset-manifest.json"
    asset_manifest = json.loads(asset_manifest_path.read_text(encoding="utf-8")) if asset_manifest_path.is_file() else []
    errors = []
    answers = {}
    answer_line_counts = {}
    bad_hits = []
    comment_spacing_errors = []
    choice_spacing_errors = []
    image_links = []
    for note in markdown_files:
        text = note.read_text(encoding="utf-8")
        rel_note = str(note.relative_to(export))
        rows = text.splitlines()
        for index, row in enumerate(rows):
            if re.fullmatch(r"<!-- (?:SECTION|MATERIAL):[^>]+ -->", row.strip()):
                if index > 0 and rows[index - 1].strip():
                    comment_spacing_errors.append(f"{rel_note}:{index + 1} missing blank before comment")
                if index + 1 < len(rows) and rows[index + 1].strip():
                    comment_spacing_errors.append(f"{rel_note}:{index + 1} missing blank after comment")
            if not row.strip() and index > 0 and index + 1 < len(rows):
                previous = rows[index - 1].strip()
                following = rows[index + 1].strip()
                if re.match(r"^[①②③④⑤]\s*", previous) and re.match(r"^[①②③④⑤]\s*", following):
                    choice_spacing_errors.append(f"{rel_note}:{index + 1} blank line between choices")
        for fragment in bad_fragments:
            if fragment and fragment in text:
                bad_hits.append(f"{rel_note}: {fragment}")
        for link in re.findall(r"!\[\[([^\]]+)\]\]", text):
            target = (note.parent / link).resolve()
            image_links.append({"note": rel_note, "link": link})
            if not target.is_file() or not target.is_relative_to(export):
                errors.append(f"bad image link {rel_note} -> {link}")
        if note in questions:
            number = re.search(rf"{re.escape(code)}(\d+)\.md$", note.name)[1]
            answer = re.search(r'answer: "([①②③④⑤])"', text)
            answers[number] = answer[1] if answer else ""
            answer_line_counts[number] = len(re.findall(r"\*\*정답\s*:", text))
            for marker in (
                "<!-- SECTION:PROBLEM_START -->",
                "<!-- SECTION:PROBLEM_END -->",
                "<!-- SECTION:SOLUTION_START -->",
                "<!-- SECTION:SOLUTION_END -->",
            ):
                if marker not in text:
                    errors.append(f"missing {marker} {rel_note}")
            if number in expected_answers and answers[number] != expected_answers[number]:
                errors.append(f"wrong answer {number}: {answers[number]} expected {expected_answers[number]}")
            if answer_line_counts[number] != 1:
                errors.append(f"duplicate/missing answer line {number}: {answer_line_counts[number]}")
            if "### 정답 해설" not in text:
                errors.append(f"missing solution section {number}")
            if f"공통 지문: [{code}_" not in text:
                errors.append(f"missing common passage link {number}")
    for entry in asset_manifest:
        target = export / entry["output"]
        if not target.is_file():
            errors.append(f"missing asset {entry['output']}")
        elif hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
            errors.append(f"sha mismatch {entry['output']}")
    manifest_outputs = {entry["output"] for entry in asset_manifest}
    linked_outputs = set()
    for link in image_links:
        note_path = export / link["note"]
        linked_outputs.add(str((note_path.parent / link["link"]).resolve().relative_to(export.resolve())))
    orphan_manifest_assets = sorted(manifest_outputs - linked_outputs)
    if orphan_manifest_assets:
        errors.extend(f"manifest asset not linked: {path}" for path in orphan_manifest_assets)
    report = {
        "export": str(export),
        "code": code,
        "markdown_count": len(markdown_files),
        "combined_count": len(combined),
        "question_count": len(questions),
        "asset_count": len(asset_manifest),
        "image_link_count": len(image_links),
        "answers": answers,
        "answer_line_counts": answer_line_counts,
        "errors": errors,
        "bad_hits": bad_hits,
        "comment_spacing_errors": comment_spacing_errors,
        "choice_spacing_errors": choice_spacing_errors,
    }
    report["passed"] = (
        not errors and not bad_hits and not comment_spacing_errors and not choice_spacing_errors
        and len(combined) == 3 and len(questions) == 11 and len(markdown_files) == 14
    )
    return report


def main():
    parser = argparse.ArgumentParser(description="Verify an Exam Processor Markdown export batch.")
    parser.add_argument("export_dir")
    parser.add_argument("--code", required=True)
    parser.add_argument("--answer", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = verify(args.export_dir, args.code, parse_answers(args.answer))
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else report)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
