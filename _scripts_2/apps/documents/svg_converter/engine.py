"""Standalone Academic & Diagram SVG Converter Engine.

Converts 2D graphs, diagrams, and figures (from images or PDF slides)
into clean, validated, Obsidian dark-mode defensive SVG vector XML.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Tuple, Union

from _scripts_2.ai_core import AIClient

SVG_BASE_PROMPT = """첨부된 이미지를 분석하여 좌표축 기반 2차원 그래프 또는 학술 다이어그램을 완전한 SVG XML로 복원하라.

현재 이미지에 의미 있는 2차원 그래프/다이어그램이 없으면 정확히 NO_GRAPH만 출력한다.
(단순한 실사 사진, 인포그래픽 포스터, 복잡한 3차원 투시도 등은 NO_GRAPH로 처리한다.)

그래프/다이어그램이 있다면, 이미지에 보이는 모든 축, 선/곡선, 음영 영역, 절편, 점, 수식과 라벨을
시각적으로 충실하게 옮긴 완전한 SVG XML 하나만 출력한다. Markdown 코드펜스나 설명은 일절 쓰지 않는다.

필수 기술 규칙:
- 루트 엘리먼트: <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 420" width="100%" height="auto">
- 첫 번째 자식 레이어: 반드시 <rect width="100%" height="100%" fill="#ffffff"/> (옵시디언 다크모드 방어용 흰 배경)
- 모든 fill, stroke, font 속성은 각 요소에 인라인 속성으로 직접 작성한다. (<style>, class, script 금지)
- HTML 주석(<!-- -->)과 빈 줄(\n\n)을 절대 넣지 않는다. (옵시디언 파서의 불필요한 <p> 삽입 방지)
- marker ID는 반드시 충돌 방지용 고유 접두사를 사용한다. (예: id="marker-axis-arrow-{id_suffix}")
- 중앙 라벨은 text-anchor="middle" dominant-baseline="middle"을 사용한다.
- [폰트 및 LaTeX 수식 표기 규칙 (최우선 준수)]:
  * [수식 및 변수]: 모든 수식(예: C = -wl + wh + π - T, h + (π - T)/w), 단일 변수(예: x, y, P, Q, r, Y, L, K, w, h, l, C, T, π, λ, θ 등), 수식 기호, 절편 라벨은 반드시 font-family="'KaTeX_Math', 'KaTeX_Main', 'Cambria Math', 'STIX Two Math', 'Times New Roman', serif"를 사용한다.
  * [변수 이탤릭]: 모든 수학/경제 변수 알파벳은 반드시 이탤릭(font-style="italic")으로 표기한다. (예: <tspan font-style="italic" font-family="'KaTeX_Math', 'KaTeX_Main', 'Cambria Math', 'STIX Two Math', 'Times New Roman', serif">w</tspan>)
  * [혼합 라벨]: 'Consumption, C' 또는 'Leisure, l'처럼 일반 단어와 변수가 함께 쓰인 경우, 단어 부분은 font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"로, 변수 부분은 <tspan font-style="italic" font-family="'KaTeX_Math', 'KaTeX_Main', 'Cambria Math', 'STIX Two Math', 'Times New Roman', serif">로 명확히 분리한다.
  * [순수 일반 텍스트]: 제목, 일반 한국어/영어 설명 등 수식이 전혀 없는 텍스트는 font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"를 사용한다.
  * 아래첨자/위첨자(예: x₁, x₂, P₁, P₂, Q*, y*, r_t)는 유니코드 첨자(x₁, x₂, p₁, p₂, y*)를 사용하거나 <tspan dy="-0.5em" font-size="75%">*</tspan>, <tspan dy="0.3em" font-size="75%">1</tspan>을 사용한다.
  * 등호(=), 더하기(+), 빼기(−: U+2212), 숫자 등 연산자/상수는 직립(font-style="normal")으로 작성한다.
  * 분수 형태(예: m/p₁, MC/P, dy/dx)는 명확히 식별 가능하게 가로 분수선(<line>)이나 슬래시(/)를 적절히 배치한다.

학술/경제학 시맨틱 컬러 및 디자인 시스템:
- 캔버스 규격: 600×420, 메인 제목은 x=300, y=30 부근 (16px, bold, 가운데 정렬)
- 축(Axes): 원점 (x=80, y=340) 부근에서 시작, 축 두께 2px, 색상 #374151
- 보조선/점선: #9CA3AF 또는 #CBD5E1 (stroke-dasharray="4 4")
- 기준 상태 / 균형선 / 기본 곡선: 파랑 #2563EB (영역 음영: #DBEAFE, fill-opacity="0.6")
- 증가 / 우측이동 / 신규 선택 / 최적점: 초록 #16A34A (영역 음영: #DCFCE7)
- 감소 / 좌측이동 / 손실 / 조세 / 제약: 빨강 #DC2626 (영역 음영: #FEE2E2)
- 피벗 / 기준점 / 굴절(Kink)점: Amber/골드 #D97706
- 곡선(무차별곡선, 비용곡선, 생산함수, IS-LM 곡선 등): 부드러운 2차/3차 베지에 곡선 <path d="M ... Q ..."/> 사용
- 가로축과 세로축은 반드시 동일한 원점(예: 80, 340)에서 시작하며, 축 라벨(예: x₁, x₂, P, Y)이 캔버스 밖으로 잘리지 않도록 배치한다.
"""

ALLOWED_COLORS = {
    "#ffffff", "#111827", "#1f2937", "#374151", "#4b5563", "#6b7280",
    "#9ca3af", "#d1d5db", "#e5e7eb", "#f3f4f6", "#f8fafc", "#cbd5e1",
    "#2563eb", "#1d4ed8", "#3b82f6", "#dbeafe", "#eff6ff", "#93c5fd",
    "#16a34a", "#15803d", "#166534", "#dcfce7", "#f0fdf4", "#86efac",
    "#dc2626", "#b91c1c", "#fee2e2", "#fca5a5", "#d97706", "#b45309",
    "#fef3c7", "#fef08a", "#a16207", "#ca8a04", "none",
}

COLOR_NORMALIZATION = {
    "#000000": "#374151",
    "#000": "#374151",
    "#ef4444": "#DC2626",
    "#f87171": "#DC2626",
    "#22c55e": "#16A34A",
    "#10b981": "#16A34A",
    "#f97316": "#D97706",
    "#f59e0b": "#D97706",
    "#60a5fa": "#3B82F6",
}


@dataclass
class SvgOptions:
    task: str = "svg_graph"
    profile: str = "precision"
    model: Optional[str] = None
    max_retries: int = 2
    auto_repair: bool = True
    validate: bool = True
    temperature: float = 0.0
    id_suffix: str = "graph"
    prompt_hint: str = ""


@dataclass
class SvgResult:
    svg: Optional[str]
    is_graph: bool
    source_file: Optional[Path] = None
    error: Optional[str] = None
    retries_used: int = 0


class SvgConverter:
    """Standalone engine for converting images into clean, validated SVG vector graphics."""

    def __init__(self, ai_client: Optional[AIClient] = None, default_options: Optional[SvgOptions] = None):
        self.client = ai_client or AIClient(profile="precision")
        self.default_options = default_options or SvgOptions()


    def convert_image(
        self,
        image_path: Union[Path, str],
        context_text: str = "",
        options: Optional[SvgOptions] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> SvgResult:
        """Convert a single image file to SVG."""
        opts = options or self.default_options
        img_p = Path(image_path)
        if not img_p.is_file():
            if log_callback:
                log_callback(f"❌ 파일을 찾을 수 없음: {img_p}")
            return SvgResult(svg=None, is_graph=False, source_file=img_p, error=f"File not found: {img_p}")

        prompt = SVG_BASE_PROMPT.replace("{id_suffix}", opts.id_suffix)
        if context_text.strip():
            prompt += f"\n\nIMAGE CONTEXT / NOTES:\n{context_text.strip()}\n"
        if opts.prompt_hint.strip():
            prompt += f"\nADDITIONAL INSTRUCTIONS:\n{opts.prompt_hint.strip()}\n"

        return self._execute_conversion([img_p], prompt, opts, source_file=img_p, log_callback=log_callback)

    def convert_slide_with_context(
        self,
        image_paths: Sequence[Union[Path, str]],
        page_no: int,
        page_context: Sequence[Tuple[int, str]],
        options: Optional[SvgOptions] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        """Context-aware multi-slide conversion for Lecture PDF Extractor compatibility.
        
        Returns the raw SVG string, or None if NO_GRAPH. Raises RuntimeError on unrecoverable failure.
        """
        opts = options or self.default_options
        context_blocks = []
        for ctx_page, text in page_context:
            role = "CURRENT" if ctx_page == page_no else ("PREVIOUS" if ctx_page < page_no else "NEXT")
            compact = re.sub(r"\s+", " ", text).strip()[:2500]
            context_blocks.append(f"[{role} PAGE {ctx_page}]\n{compact or '[텍스트 없음]'}")

        order = ", ".join(
            f"image {pos + 1}=PAGE {ctx_p}{' (CURRENT)' if ctx_p == page_no else ''}"
            for pos, (ctx_p, _txt) in enumerate(page_context)
        )

        prompt = (
            SVG_BASE_PROMPT.replace("{id_suffix}", f"p{page_no}")
            + f"\nCURRENT PAGE NUMBER: {page_no}\nIMAGE ORDER: {order}\n\nPAGE CONTEXT:\n"
            + "\n\n".join(context_blocks)
        )

        res = self._execute_conversion(
            [Path(p) for p in image_paths],
            prompt,
            opts,
            page_no=page_no,
            log_callback=log_callback,
        )

        if not res.is_graph:
            return None
        if res.error:
            raise RuntimeError(f"SVG 변환 실패: {page_no}쪽 ({res.error})")
        return res.svg

    def _execute_conversion(
        self,
        image_paths: Sequence[Path],
        base_prompt: str,
        opts: SvgOptions,
        source_file: Optional[Path] = None,
        page_no: int = 1,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> SvgResult:
        def _log(msg: str):
            if log_callback:
                try:
                    log_callback(msg)
                except Exception:
                    pass

        correction = ""
        last_error: Optional[Exception] = None
        target_name = source_file.name if source_file else (f"{len(image_paths)}개 이미지" if image_paths else "이미지")
        model_display = opts.model or f"{opts.task} ({opts.profile})"
        _log(f"변환 파이프라인 가동: 대상={target_name}, 의도/설정={model_display}, 최대재시도={opts.max_retries}")

        for attempt in range(max(1, opts.max_retries)):
            attempt_num = attempt + 1
            full_prompt = base_prompt + correction
            _log(f"[시도 {attempt_num}/{opts.max_retries}] AI 요청 전송 중 ({model_display}, 프롬프트 크기: {len(full_prompt):,}자)...")
            t0 = time.time()
            try:
                gen_kwargs: dict[str, Any] = {
                    "task": opts.task,
                    "profile": opts.profile,
                    "temperature": opts.temperature,
                    "max_tokens": 8192,
                }
                if opts.model:
                    gen_kwargs["model_override"] = opts.model
                raw = self.client.generate_images(
                    image_paths,
                    prompt=full_prompt,
                    **gen_kwargs,
                ).strip()
                call_elapsed = time.time() - t0
                _log(f"[시도 {attempt_num}/{opts.max_retries}] AI 응답 수신 완료 (소요: {call_elapsed:.2f}초, 수신 데이터: {len(raw):,}자)")
            except Exception as e:
                call_elapsed = time.time() - t0
                last_error = e
                _log(f"[시도 {attempt_num}/{opts.max_retries}] ❌ AI API 호출 중 예외 발생 ({call_elapsed:.2f}초): {e}")

                correction = f"\n\nAPI 호출 중 오류 발생: {e}. 다시 시도한다."
                continue

            if raw == "NO_GRAPH":
                _log(f"[시도 {attempt_num}/{opts.max_retries}] ⚠️ 도표 분석 결과: 2차원 좌표축 그래프/다이어그램 미감지 (NO_GRAPH 판정)")
                return SvgResult(svg=None, is_graph=False, source_file=source_file, retries_used=attempt)

            svg = re.sub(r"^```(?:svg|xml|html)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
            _log(f"[시도 {attempt_num}/{opts.max_retries}] 마크다운 코드펜스 분리 완료 (SVG 코드 길이: {len(svg):,}자)")

            if opts.auto_repair:
                _log(f"[시도 {attempt_num}/{opts.max_retries}] SVG 자동 보정(viewBox 600×420, 다크모드 방어 흰색 rect, 색상 팔레트 정규화) 수행 중...")
                svg = self.clean_and_repair(svg, page_no)
                _log(f"[시도 {attempt_num}/{opts.max_retries}] SVG 자동 보정 완료")

            if opts.validate:
                _log(f"[시도 {attempt_num}/{opts.max_retries}] SVG XML 구조 및 다크모드 방어 규칙 유효성 검증 중...")
                try:
                    self.validate(svg, page_no)
                    _log(f"[시도 {attempt_num}/{opts.max_retries}] ✅ SVG 유효성 검증 성공!")
                    return SvgResult(svg=svg, is_graph=True, source_file=source_file, retries_used=attempt)
                except RuntimeError as exc:
                    last_error = exc
                    _log(f"[시도 {attempt_num}/{opts.max_retries}] ⚠️ 로컬 SVG 검증 실패: {exc}")
                    correction = (
                        f"\n\n직전 결과가 로컬 검사에서 실패했다: {exc}\n"
                        "모든 기술 규칙(첫 레이어 흰 배경 rect, 유효한 XML, 인라인 스타일)을 준수하여 전체 SVG XML을 처음부터 다시 출력하라."
                    )
            else:
                _log(f"[시도 {attempt_num}/{opts.max_retries}] ✅ 변환 완료 (검증 건너뜀)")
                return SvgResult(svg=svg, is_graph=True, source_file=source_file, retries_used=attempt)

        _log(f"❌ 최대 재시도({opts.max_retries}회) 초과로 변환 실패: {last_error}")
        return SvgResult(
            svg=None,
            is_graph=True,
            source_file=source_file,
            error=str(last_error or "Validation failed"),
            retries_used=opts.max_retries,
        )

    @classmethod
    def clean_and_repair(cls, svg: str, page_no: int = 1) -> str:
        """Sanitize, normalize colors, clamp out-of-bound coords, and snap axis origins."""
        svg = re.sub(r"<!--.*?-->", "", svg, flags=re.S)
        svg = re.sub(r"<text\b[^>]*>[^<]*(?:©|Copyright).*?</text>", "", svg, flags=re.I | re.S)
        svg = re.sub(rf"<text\b[^>]*>\s*{page_no}\s*</text>", "", svg, flags=re.I)
        svg = re.sub(r"<text(?![^>]*\bfill\s*=)", '<text fill="#111827"', svg, flags=re.I)

        for source, target in COLOR_NORMALIZATION.items():
            svg = re.sub(re.escape(source), target, svg, flags=re.I)

        svg = re.sub(r"#[0-9a-fA-F]{6}\b", lambda match: cls._nearest_palette_color(match.group(0)), svg)

        try:
            ET.register_namespace("", "http://www.w3.org/2000/svg")
            root = ET.fromstring(svg)
        except ET.ParseError:
            return svg

        # 1. Ensure viewBox & responsive dimensions
        root.set("viewBox", "0 0 600 420")
        root.set("width", "100%")
        root.set("height", "auto")

        # 2. Ensure white background is the very first child
        children = list(root)
        has_white_bg = False
        if children and children[0].tag.split("}")[-1] == "rect":
            first_attrs = children[0].attrib
            if first_attrs.get("fill", "").lower() == "#ffffff" and first_attrs.get("width") == "100%":
                has_white_bg = True

        if not has_white_bg:
            bg_rect = ET.Element("rect", {"width": "100%", "height": "100%", "fill": "#ffffff"})
            root.insert(0, bg_rect)

        elements = list(root.iter())

        # 3. Font family, coordinate clamping, and marker ID prefixing
        default_font = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
        id_prefix = f"p{page_no}"

        # Normalize marker IDs and their references to prevent cross-SVG collision
        marker_id_map = {}
        for element in elements:
            tag = element.tag.split("}")[-1]
            if tag == "marker" and "id" in element.attrib:
                old_id = element.attrib["id"]
                if not old_id.startswith(f"marker-"):
                    new_id = f"marker-{old_id}-{id_prefix}"
                elif not old_id.endswith(f"-{id_prefix}"):
                    new_id = f"{old_id}-{id_prefix}"
                else:
                    new_id = old_id
                marker_id_map[old_id] = new_id
                element.attrib["id"] = new_id

        if marker_id_map:
            for element in elements:
                for attr in ("marker-end", "marker-start", "marker-mid"):
                    if attr in element.attrib:
                        val = element.attrib[attr]
                        for old_id, new_id in marker_id_map.items():
                            val = re.sub(rf"url\(#{re.escape(old_id)}\)", f"url(#{new_id})", val)
                        element.attrib[attr] = val

        math_font = "'KaTeX_Math', 'KaTeX_Main', 'Cambria Math', 'STIX Two Math', 'Times New Roman', serif"
        default_font = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

        def _is_math_text(txt: str) -> bool:
            if not txt:
                return False
            txt = txt.strip()
            # Greek letters
            if re.search(r"[α-ωΑ-ΩπμσλθγδεηρωΔΣΩϕψτ]", txt):
                return True
            # Math operators, symbols, superscripts, subscripts
            if re.search(r"[−±×÷≠≤≥≈∈∉∂∫∑√∞=+\-/*^₀-₉*⁺⁻]", txt):
                return True
            # Standalone single-letter variables like w, h, l, C, T, x, y, etc.
            if re.match(r"^[A-Za-z](?:[\s₀-₉*⁺⁻_].*)?$", txt):
                return True
            return False

        def _is_math_element(elem: ET.Element) -> bool:
            f = elem.attrib.get("font-family", "").lower()
            if any(k in f for k in ("katex", "serif", "cambria", "times", "math", "stix")):
                return True
            if elem.attrib.get("font-style") == "italic":
                return True
            return _is_math_text("".join(elem.itertext()))

        for element in elements:
            tag = element.tag.split("}")[-1]
            if tag == "text":
                raw_prefix = (element.text or "").strip()
                has_descriptive_word = bool(re.search(r"[a-zA-Z가-힣]{3,}", raw_prefix))
                is_math = _is_math_element(element) or any(
                    _is_math_element(c) for c in element if c.tag.split("}")[-1] == "tspan"
                )

                if is_math and not has_descriptive_word:
                    element.set("font-family", math_font)
                else:
                    existing_f = element.attrib.get("font-family", "")
                    if any(k in existing_f.lower() for k in ("katex", "serif", "cambria", "times", "math", "stix")):
                        element.set("font-family", math_font)
                    else:
                        element.set("font-family", default_font)

                if not element.attrib.get("fill"):
                    element.set("fill", "#111827")

                for child in element:
                    if child.tag.split("}")[-1] == "tspan":
                        child_is_math = _is_math_element(child)
                        if child_is_math or (is_math and not has_descriptive_word):
                            child.set("font-family", math_font)
                        else:
                            child_f = child.attrib.get("font-family", "")
                            if any(k in child_f.lower() for k in ("katex", "serif", "cambria", "times", "math", "stix")):
                                child.set("font-family", math_font)
                            elif child_f:
                                child.set("font-family", default_font)
                            # If no explicit font-family, it inherits naturally from parent <text>

                for attr, upper in (("x", 595.0), ("y", 410.0)):
                    val_str = element.attrib.get(attr)
                    if val_str is not None:
                        try:
                            val = float(val_str)
                            clamped = min(max(val, 5.0), upper)
                            if clamped != val:
                                element.set(attr, f"{clamped:g}")
                        except ValueError:
                            pass

        # 4. Auto-repair: Axis origin snapping
        axis_candidates = []
        for element in elements:
            if element.tag.split("}")[-1] == "line" and "marker-end" in element.attrib:
                try:
                    coords = (
                        float(element.attrib["x1"]), float(element.attrib["y1"]),
                        float(element.attrib["x2"]), float(element.attrib["y2"])
                    )
                    axis_candidates.append((element, coords))
                except (KeyError, ValueError):
                    pass

        horizontal = max(
            (item for item in axis_candidates if abs(item[1][3] - item[1][1]) <= 6),
            key=lambda item: abs(item[1][2] - item[1][0]),
            default=None
        )
        vertical = max(
            (item for item in axis_candidates if abs(item[1][2] - item[1][0]) <= 6),
            key=lambda item: abs(item[1][3] - item[1][1]),
            default=None
        )

        if horizontal and vertical:
            h_elem, (hx1, hy1, hx2, hy2) = horizontal
            v_elem, (vx1, vy1, vx2, vy2) = vertical

            origin_x = min(hx1, hx2) if abs(hx1 - vx1) < 20 else vx1
            origin_y = max(vy1, vy2) if abs(vy2 - hy1) < 20 else hy1

            if hx2 >= hx1:
                h_elem.set("x1", f"{origin_x:g}")
                h_elem.set("y1", f"{origin_y:g}")
                h_elem.set("y2", f"{origin_y:g}")
            else:
                h_elem.set("x2", f"{origin_x:g}")
                h_elem.set("y1", f"{origin_y:g}")
                h_elem.set("y2", f"{origin_y:g}")

            if vy2 <= vy1:
                v_elem.set("x1", f"{origin_x:g}")
                v_elem.set("x2", f"{origin_x:g}")
                v_elem.set("y1", f"{origin_y:g}")
            else:
                v_elem.set("x1", f"{origin_x:g}")
                v_elem.set("x2", f"{origin_x:g}")
                v_elem.set("y2", f"{origin_y:g}")

        ET.indent(root, space="")
        rendered = ET.tostring(root, encoding="unicode", short_empty_elements=True)
        return "\n".join(line.strip() for line in rendered.splitlines() if line.strip()) + "\n"

    @staticmethod
    def _nearest_palette_color(value: str) -> str:
        lowered = value.lower()
        palette = [color for color in ALLOWED_COLORS if color.startswith("#")]
        if lowered in palette:
            return value
        try:
            rgb = tuple(int(lowered[i:i + 2], 16) for i in (1, 3, 5))
            nearest = min(
                palette,
                key=lambda color: sum((rgb[ch] - int(color[1 + ch * 2:3 + ch * 2], 16)) ** 2 for ch in range(3)),
            )
            return nearest.upper() if nearest != "#ffffff" else "#ffffff"
        except Exception:
            return "#374151"

    @staticmethod
    def validate(svg: str, page_no: int = 1) -> None:
        """Validate SVG against security, obsidian dark mode, and geometry rules."""
        if len(svg.encode("utf-8")) > 600_000:
            raise RuntimeError(f"SVG 크기가 한도를 초과했습니다 (600KB 이상): {page_no}쪽")

        lowered = svg.lower()
        forbidden = ("<style", "<script", "<foreignobject", "javascript:", " onload=", " onclick=", " class=")
        if any(token in lowered for token in forbidden):
            raise RuntimeError(f"SVG에 허용되지 않은 보안/스타일 요소가 포함되어 있습니다: {page_no}쪽")
        if re.search(r"(?:href|src)\s*=\s*[\"']https?://", lowered):
            raise RuntimeError(f"SVG가 외부 리소스를 참조합니다: {page_no}쪽")

        try:
            root = ET.fromstring(svg)
        except ET.ParseError as exc:
            raise RuntimeError(f"SVG XML이 유효하지 않습니다: {page_no}쪽 ({exc})") from exc

        if root.tag.split("}")[-1] != "svg":
            raise RuntimeError(f"SVG 루트 태그가 svg가 아닙니다: {page_no}쪽")

        children = list(root)
        if not children or children[0].tag.split("}")[-1] != "rect":
            raise RuntimeError(f"SVG의 첫 요소가 흰 배경 rect가 아닙니다: {page_no}쪽")
