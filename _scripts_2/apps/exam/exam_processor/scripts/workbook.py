"""Manage source-bound workbook structure before importing any questions."""
import argparse
import json
from pathlib import Path

from ..ingestion.formats.math.hanwangi_profile import profile
from ..ingestion.formats.korean.kice_korean import page_range
from ..storage.store import atomic_json
from ..pipeline.workbook import (
    new_structure,
    read_structure,
    confirm_span,
    verify_sources,
    structure_report,
    split_pdfs,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="문제집 목차와 PDF 분할 준비")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--problem", required=True)
    init.add_argument("--solution", required=True)
    init.add_argument("--output", required=True)
    check = commands.add_parser("check")
    check.add_argument("structure")
    confirm = commands.add_parser("confirm")
    confirm.add_argument("structure")
    confirm.add_argument("--node", required=True)
    confirm.add_argument("--document", choices=["problem", "solution"], required=True)
    confirm.add_argument("--pages", required=True)
    confirm.add_argument("--evidence", required=True)
    confirm.add_argument("--printed-first", type=int)
    confirm.add_argument("--printed-last", type=int)
    split = commands.add_parser("split")
    split.add_argument("structure")
    split.add_argument("--nodes", nargs="+", required=True)
    split.add_argument("--output", required=True)
    anchors = commands.add_parser("anchors")
    anchors.add_argument("structure")
    anchors.add_argument("--node", required=True)
    anchors.add_argument("--cache", required=True)
    anchors.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "init":
        target = Path(args.output).expanduser().resolve()
        if target.exists():
            parser.error("기존 구조 기록을 덮어쓰지 않습니다.")
        structure = new_structure(profile(), args.problem, args.solution)
        atomic_json(target, structure)
        result = {"structure": str(target), **structure_report(structure)}
    else:
        structure = read_structure(args.structure)
        if args.command == "anchors":
            from ..ingestion.scan_anchors import number_anchors
            verify_sources(structure)
            node = next((n for n in structure["nodes"] if n["id"] == args.node), None)
            if not node or any(s["status"] != "confirmed" for s in node["spans"].values()):
                parser.error("문제편과 해설편 범위를 먼저 확인하세요.")
            target = Path(args.output).expanduser().resolve()
            if target.exists():
                parser.error("기존 번호 후보 기록을 덮어쓰지 않습니다.")
            result = {"workbook_id": structure["id"], "node": args.node, "status": "candidate",
                      "anchors": {role: number_anchors(structure["documents"][role]["path"],
                          node["spans"][role]["pages"], args.node, args.cache) for role in ("problem", "solution")}}
            atomic_json(target, result)
        elif args.command == "split":
            result = split_pdfs(structure, args.output, args.nodes)
        else:
            verify_sources(structure)
            if args.command == "confirm":
                structure = confirm_span(structure, args.node, args.document,
                    page_range(args.pages, structure["documents"][args.document]["page_count"]),
                    args.evidence, args.printed_first, args.printed_last)
                atomic_json(args.structure, structure)
            result = structure_report(structure)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
