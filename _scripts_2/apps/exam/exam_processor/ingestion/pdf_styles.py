"""Recover meaningful source formatting from PDF geometry."""
import re
from difflib import SequenceMatcher


def _dedupe(records):
    seen, result = set(), []
    for record in records:
        key = (record["type"], record.get("page"), tuple(round(v, 1) for v in record["bbox"]))
        if key not in seen:
            seen.add(key)
            result.append(record)
    return result


def detect_underlines(page, page_no, bbox):
    x0, y0, x1, y1 = bbox
    records = []
    for line in page.lines:
        if not (x0 <= line["x0"] < line["x1"] <= x1 and y0 <= line["top"] <= y1):
            continue
        if abs(line["y0"] - line["y1"]) > .4 or not 4 <= line["width"] <= 360:
            continue
        if _closed_box_bottom(page, line):
            continue
        chars = sorted((c for c in page.chars
                        if abs(c["bottom"] - line["top"]) <= .7
                        and c["x1"] > line["x0"] - .5 and c["x0"] < line["x1"] + .5),
                       key=lambda c: c["x0"])
        covered = [c for c in chars if min(c["x1"], line["x1"]) - max(c["x0"], line["x0"])
                   >= min(1.0, (c["x1"] - c["x0"]) * .35)]
        text = "".join(c["text"] for c in covered).strip()
        if not text:
            continue
        row = sorted((c for c in page.chars if abs(c["bottom"] - chars[0]["bottom"]) <= .7), key=lambda c: c["x0"])
        records.append({"type": "underline", "page": page_no,
                        "bbox": [line["x0"], line["top"], line["x1"], line["bottom"]],
                        "text": text, "line_text": "".join(c["text"] for c in row).strip()})
    records = _dedupe(records)
    # A long underline is often emitted as one segment per printed line (and
    # sometimes as adjacent segments on the same line). Reconstruct the
    # logical sentence before handing it to the Markdown mapper.
    records.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
    merged = []
    for record in records:
        if merged and abs(merged[-1]["bbox"][1] - record["bbox"][1]) <= 1.2 \
                and record["bbox"][0] <= merged[-1]["bbox"][2] + 2.5:
            previous = merged[-1]
            previous["bbox"][2] = max(previous["bbox"][2], record["bbox"][2])
            previous["text"] = (previous["text"] + " " + record["text"]).strip()
            previous["line_text"] = (previous["line_text"] + " " + record["line_text"]).strip()
            continue
        merged.append(record)
    logical = []
    for record in merged:
        if logical:
            previous = logical[-1]
            gap = record["bbox"][1] - previous["bbox"][3]
            same_column = abs(record["bbox"][0] - previous["bbox"][0]) < 30 or record["bbox"][0] <= previous["bbox"][2] + 2
            continues = not re.search(r"[.!?。！？]$", previous["text"])
            if 10 <= gap <= 26 and same_column and continues:
                previous["bbox"][2] = max(previous["bbox"][2], record["bbox"][2])
                previous["bbox"][3] = record["bbox"][3]
                previous["text"] = (previous["text"] + " " + record["text"]).strip()
                previous["line_text"] = (previous["line_text"] + " " + record["line_text"]).strip()
                continue
        logical.append(record)
    return logical


def _closed_box_bottom(page, line):
    """A closed inline box border is not emphasis on the enclosed word."""
    x0, x1, bottom = line["x0"], line["x1"], line["top"]
    for rect in getattr(page, "rects", []):
        if (rect["bottom"] - rect["top"] >= 3 and abs(rect["bottom"]-bottom) <= .5
                and abs(rect["x0"]-x0) <= .5 and abs(rect["x1"]-x1) <= .5):
            return True
    verticals = [edge for edge in page.lines if abs(edge["x1"]-edge["x0"]) <= .4
                 and abs(edge["bottom"]-bottom) <= .5 and edge["bottom"]-edge["top"] >= 3]
    for left in (edge for edge in verticals if abs(edge["x0"]-x0) <= .5):
        for right in (edge for edge in verticals if abs(edge["x0"]-x1) <= .5):
            if abs(left["top"]-right["top"]) <= .5 and any(
                    abs(top["bottom"]-top["top"]) <= .4 and abs(top["top"]-left["top"]) <= .5
                    and abs(top["x0"]-x0) <= .5 and abs(top["x1"]-x1) <= .5 for top in page.lines):
                return True
    return False


def detect_bracket_groups(page, page_no, bbox):
    x0, y0, x1, y1 = bbox
    verticals = [line for line in page.lines if abs(line["x0"] - line["x1"]) <= .4
                 and line["height"] >= 25 and x0 <= line["x0"] <= x1
                 and line["bottom"] >= y0 and line["top"] <= y1]
    horizontals = [line for line in page.lines if abs(line["y0"] - line["y1"]) <= .4 and 4 <= line["width"] <= 25]
    words = page.extract_words(x_tolerance=1, y_tolerance=2)
    records = []
    for first in verticals:
        for second in verticals:
            if second["top"] <= first["top"] or abs(first["x0"] - second["x0"]) > .6:
                continue
            if not 4 <= second["top"] - first["bottom"] <= 35:
                continue
            top, bottom, edge = first["top"], second["bottom"], first["x0"]
            right_caps = (any(abs(h["top"] - top) <= .8 and abs(h["x1"] - edge) <= 1 for h in horizontals)
                          and any(abs(h["top"] - bottom) <= .8 and abs(h["x1"] - edge) <= 1 for h in horizontals))
            left_caps = (any(abs(h["top"] - top) <= .8 and abs(h["x0"] - edge) <= 1 for h in horizontals)
                         and any(abs(h["top"] - bottom) <= .8 and abs(h["x0"] - edge) <= 1 for h in horizontals))
            label = next((w for w in words if re.fullmatch(r"\[[A-Z]\]", w["text"])
                          and first["bottom"] - 2 <= w["top"] <= second["top"] + 2
                          and abs((w["x0"] + w["x1"]) / 2 - edge) <= 18), None)
            if not (right_caps or left_caps) or not label:
                continue
            side = "right" if right_caps else "left"
            selected = sorted((w for w in words if w is not label
                               and ((side == "right" and x0 <= w["x0"] and w["x1"] < edge)
                                    or (side == "left" and edge < w["x0"] and w["x1"] <= x1))
                               and w["bottom"] >= top - 3 and w["top"] <= bottom),
                              key=lambda w: (round(w["top"] / 3), w["x0"]))
            rows = []
            for word in selected:
                if not rows or abs(rows[-1][0]["top"] - word["top"]) > 3:
                    rows.append([word])
                else:
                    rows[-1].append(word)
            row_texts = [" ".join(w["text"] for w in sorted(row, key=lambda w: w["x0"])) for row in rows]
            meaningful = [w for w in selected if re.search(r"[가-힣A-Za-z0-9]", w["text"])]
            records.append({"type": "group", "label": label["text"][1:-1], "page": page_no,
                            "side": side,
                            "bbox": ([min((w["x0"] for w in selected), default=x0), top, edge, bottom]
                                     if side == "right" else
                                     [edge, top, max((w["x1"] for w in selected), default=x1), bottom]),
                            "start_text": meaningful[0]["text"] if meaningful else "",
                            "end_text": meaningful[-1]["text"] if meaningful else "",
                            "start_line": row_texts[0] if row_texts else "",
                            "end_line": row_texts[-1] if row_texts else ""})
    return _dedupe(records)


def detect_source_styles(page, page_no, bbox):
    return (detect_underlines(page, page_no, bbox)
            + detect_bracket_groups(page, page_no, bbox)
            + detect_boxes(page, page_no, bbox))


def detect_boxes(page, page_no, bbox):
    """Find closed PDF rectangles without mistaking a single rule for a box."""
    x0, y0, x1, y1 = bbox
    candidates = []
    for shape in list(page.rects) + list(page.curves):
        if shape.get("width", 0) >= 45 and shape.get("height", 0) >= 18:
            candidates.append((shape["x0"], shape["top"], shape["x1"], shape["bottom"], "vector"))
    verticals = [l for l in page.lines if abs(l["x0"] - l["x1"]) <= .5 and l["height"] >= 18]
    horizontals = [l for l in page.lines if abs(l["y0"] - l["y1"]) <= .5 and l["width"] >= 45]
    for left in verticals:
        for right in verticals:
            if right["x0"] <= left["x0"] + 20:
                continue
            top = max(left["top"], right["top"])
            bottom = min(left["bottom"], right["bottom"])
            if bottom - top < 18:
                continue
            has_top = any(abs(h["top"] - top) <= 1 and h["x0"] <= left["x0"] + 1 and h["x1"] >= right["x0"] - 1 for h in horizontals)
            has_bottom = any(abs(h["top"] - bottom) <= 1 and h["x0"] <= left["x0"] + 1 and h["x1"] >= right["x0"] - 1 for h in horizontals)
            if has_top and has_bottom:
                candidates.append((left["x0"], top, right["x0"], bottom, "lines"))
    records = []
    for bx0, by0, bx1, by1, source in candidates:
        if not (x0 <= bx0 and bx1 <= x1 and y0 <= by0 and by1 <= y1):
            continue
        words = [w for w in page.extract_words(x_tolerance=1, y_tolerance=2)
                 if bx0 + 2 <= w["x0"] and w["x1"] <= bx1 - 2 and by0 + 2 <= w["top"] <= by1 - 2]
        if not words:
            continue
        region_width, region_height = x1 - x0, y1 - y0
        box_width, box_height = bx1 - bx0, by1 - by0
        frame = (box_width >= region_width * .86 and box_height >= region_height * .52)
        records.append({"type": "box", "role": "passage_frame" if frame else "quote_box",
                        "page": page_no, "bbox": [bx0, by0, bx1, by1],
                        "detection": source, "box_detection": "certain" if source == "vector" else "likely",
                        "text_preview": " ".join(w["text"] for w in words[:20])})
    records = _dedupe(records)
    # Keep the outermost rectangle when a table/material is drawn with nested
    # cell borders; the inner cells are not independent Markdown boxes.
    filtered = []
    for record in sorted(records, key=lambda r: (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]), reverse=True):
        bx0, by0, bx1, by1 = record["bbox"]
        if any(other["bbox"][0] <= bx0 + 1 and other["bbox"][1] <= by0 + 1
               and other["bbox"][2] >= bx1 - 1 and other["bbox"][3] >= by1 - 1 for other in filtered):
            continue
        filtered.append(record)
    return filtered


def apply_underlines(markdown, styles):
    text = re.sub(r"</?u>", "", markdown, flags=re.I)
    missing = []
    spans = []
    used = set()
    for style in (s for s in styles if s["type"] == "underline"):
        token_pattern = r"\s+".join(re.escape(token) for token in style["text"].split())
        matches = list(re.finditer(token_pattern, text))
        if not matches:
            missing.append(style)
            continue
        source_line = re.sub(r"\s+", "", style.get("line_text", ""))
        compact_target = re.sub(r"\s+", "", style["text"])
        source_index = source_line.find(compact_target)
        source_context = (source_line[max(0, source_index - 14):source_index]
                          + compact_target
                          + source_line[source_index + len(compact_target):source_index + len(compact_target) + 14]) if source_index >= 0 else ""
        marker = re.search(r"[㉠-㉿ⓐ-ⓩ]", source_line[:max(source_index, 0)])
        scored = []
        for match in matches:
            if match.start() in used:
                continue
            if marker:
                line_start = text.rfind("\n", 0, match.start()) + 1
                prefix = text[line_start:match.start()]
                if marker.group() not in prefix:
                    continue
            start = max(0, match.start() - 14)
            end = min(len(text), match.end() + 14)
            current_context = re.sub(r"\s+", "", text[start:end])
            score = SequenceMatcher(None, source_context, current_context).ratio() if source_context else 0
            if marker:
                marker_position = prefix.rfind(marker.group())
                score += 10 / (len(prefix) - marker_position + 1)
            scored.append((score, match))
        if not scored:
            missing.append(style)
            continue
        _, match = max(scored, key=lambda pair: pair[0])
        used.add(match.start())
        spans.append((match.start(), match.end(), match.group()))
    for start, end, value in reversed(spans):
        text = text[:start] + "<u>" + value + "</u>" + text[end:]
    return text, missing


def apply_group_ranges(markdown, styles):
    """Mark geometry-backed bracket ranges without forcing a visual container."""
    text = re.sub(r"\s*<!--\s*GROUP:[A-Z]+\s+(?:START|END)\s*-->\s*", " ", markdown)
    missing = []

    def compact_with_positions(value):
        compact, positions = [], []
        in_tag = False
        for index, char in enumerate(value):
            if char == "<":
                in_tag = True
            if not in_tag and not char.isspace() and char not in "*_`┏┛┌┐└┘│":
                compact.append(char)
                positions.append(index)
            if char == ">":
                in_tag = False
        return "".join(compact), positions

    for style in (s for s in styles if s.get("type") == "group"):
        compact, positions = compact_with_positions(text)
        start_anchor = re.sub(r"\s+", "", style.get("start_line") or style.get("start_text", ""))
        end_anchor = re.sub(r"\s+", "", style.get("end_line") or style.get("end_text", ""))
        start_anchor = start_anchor[:36]
        end_anchor = end_anchor[-36:]
        label_at = compact.find(f"[{style['label']}]")
        before = compact.rfind(start_anchor, 0, label_at + 1) if start_anchor and label_at >= 0 else -1
        after = compact.find(start_anchor, label_at) if start_anchor and label_at >= 0 else -1
        candidates = [at for at in (before, after) if at >= 0]
        start_at = min(candidates, key=lambda at: abs(at - label_at)) if candidates else (compact.find(start_anchor) if start_anchor else -1)
        end_at = compact.find(end_anchor, max(0, start_at)) if end_anchor else -1
        if start_at < 0 or end_at < 0:
            missing.append(style)
            continue
        start = positions[start_at]
        end = positions[end_at + len(end_anchor) - 1] + 1
        label = style["label"]
        visible = re.search(rf"(?:<[^>]+>)*\[{re.escape(label)}\](?:</[^>]+>)*",
                            text[max(0, start - 240):end])
        prefix = "" if visible else f"[{label}] "
        text = (text[:start] + f"<!-- GROUP:{label} START -->\n" + prefix
                + text[start:end] + f"\n<!-- GROUP:{label} END -->" + text[end:])
    return text, missing
