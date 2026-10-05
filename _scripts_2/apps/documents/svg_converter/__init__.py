"""SVG Converter — Standalone Academic 2D Graph & Diagram Vectorizer."""

from .engine import SvgConverter, SvgOptions, SvgResult

__all__ = ["SvgConverter", "SvgOptions", "SvgResult"]


def convert_image(image_path, output_path=None, context_text="", model="gemini-3.8-flash"):
    """Convenience functional API for converting a single image to SVG."""
    from pathlib import Path
    opts = SvgOptions(model=model)
    converter = SvgConverter(default_options=opts)
    res = converter.convert_image(image_path, context_text=context_text, options=opts)
    if not res.is_graph:
        return None
    if res.error:
        raise RuntimeError(res.error)
    if output_path and res.svg:
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(res.svg, encoding="utf-8")
    return res.svg
