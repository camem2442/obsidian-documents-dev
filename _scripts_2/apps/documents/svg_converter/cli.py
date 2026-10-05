"""Command Line Interface for SVG Converter."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    from .engine import SvgConverter, SvgOptions
except (ImportError, ValueError):
    from engine import SvgConverter, SvgOptions


def copy_to_clipboard(text: str) -> bool:
    """Copy text to macOS clipboard via pbcopy."""
    try:
        proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        proc.communicate(text.encode("utf-8"))
        return proc.returncode == 0
    except Exception:
        return False


def run_cli(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="svg_converter",
        description="Convert academic 2D graphs, charts, and diagrams into clean, Obsidian-ready SVG vector graphics.",
    )
    parser.add_argument("input", type=Path, help="Input image file (PNG, JPG, WebP) or directory of images")
    parser.add_argument("-o", "--output", type=Path, help="Output SVG file path or directory (for batch)")
    parser.add_argument("-m", "--model", default="gemini-3.8-flash", help="Gemini model to use (default: gemini-3.8-flash)")
    parser.add_argument("-c", "--context", default="", help="Contextual description or surrounding textbook notes")
    parser.add_argument("--hint", default="", help="Additional custom instructions for SVG generation")
    parser.add_argument("--copy", action="store_true", help="Copy generated SVG XML directly to clipboard")
    parser.add_argument("--no-repair", action="store_true", help="Disable coordinate auto-repair & origin snapping")
    parser.add_argument("--no-validate", action="store_true", help="Disable strict SVG validation checks")

    parsed = parser.parse_args(args)
    input_path: Path = parsed.input

    if not input_path.exists():
        print(f"오류: 입력 파일 또는 디렉토리를 찾을 수 없습니다: {input_path}", file=sys.stderr)
        return 1

    opts = SvgOptions(
        model=parsed.model,
        auto_repair=not parsed.no_repair,
        validate=not parsed.no_validate,
        prompt_hint=parsed.hint,
    )
    converter = SvgConverter(default_options=opts)

    # 1. Batch directory conversion
    if input_path.is_dir():
        image_exts = {".png", ".jpg", ".jpeg", ".webp"}
        files = sorted([f for f in input_path.iterdir() if f.suffix.lower() in image_exts])
        if not files:
            print(f"디렉토리에 처리 가능한 이미지 파일이 없습니다: {input_path}", file=sys.stderr)
            return 1

        out_dir = parsed.output or (input_path / "svg_output")
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"🚀 일괄 변환 시작: 총 {len(files)}개 이미지 ➔ {out_dir}")
        success_count = 0
        for idx, f in enumerate(files, 1):
            print(f"[{idx}/{len(files)}] 처리 중: {f.name}...", end=" ", flush=True)
            res = converter.convert_image(f, context_text=parsed.context, options=opts)
            if not res.is_graph:
                print("건너뜀 (그래프 아님: NO_GRAPH)")
            elif res.error:
                print(f"실패 ({res.error})")
            elif res.svg:
                dest = out_dir / f"{f.stem}.svg"
                dest.write_text(res.svg, encoding="utf-8")
                print(f"완료 ➔ {dest.name}")
                success_count += 1

        print(f"✨ 일괄 변환 완료: {success_count}/{len(files)}개 SVG 생성")
        return 0

    # 2. Single file conversion
    print(f"🚀 SVG 변환 시작: {input_path.name} (모델: {opts.model})...")
    res = converter.convert_image(input_path, context_text=parsed.context, options=opts)

    if not res.is_graph:
        print("⚠️ 이 이미지는 좌표축/다이어그램 그래프가 아닙니다 (NO_GRAPH).", file=sys.stderr)
        return 2

    if res.error or not res.svg:
        print(f"❌ 변환 실패: {res.error}", file=sys.stderr)
        return 1

    svg_content = res.svg

    # Output file
    if parsed.output:
        out_path: Path = parsed.output
        if out_path.is_dir() or str(parsed.output).endswith("/"):
            out_path.mkdir(parents=True, exist_ok=True)
            out_file = out_path / f"{input_path.stem}.svg"
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_file = out_path
        out_file.write_text(svg_content, encoding="utf-8")
        print(f"✅ SVG 파일 저장 완료: {out_file}")
    else:
        # Default to same directory with .svg extension
        default_out = input_path.parent / f"{input_path.stem}.svg"
        default_out.write_text(svg_content, encoding="utf-8")
        print(f"✅ SVG 파일 저장 완료: {default_out}")

    if parsed.copy:
        if copy_to_clipboard(svg_content):
            print("📋 클립보드에 SVG 코드가 복사되었습니다.")
        else:
            print("⚠️ 클립보드 복사 실패 (pbcopy 오류).", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(run_cli())
