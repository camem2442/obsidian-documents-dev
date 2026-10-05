"""Keep an AI crop from trimming a mostly selected embedded PDF image."""
import math
from pathlib import Path

import pdfplumber


def embedded_image_boxes(document, page):
    try:
        with pdfplumber.open(document) as pdf:
            return [[i["x0"], i["top"], i["x1"], i["bottom"]] for i in pdf.pages[page - 1].images]
    except Exception:
        return []


def normalize_proposed_box(proposed):
    if not isinstance(proposed, (list, tuple)) or len(proposed) != 4:
        raise ValueError("자료 크롭 좌표가 잘못되었습니다.")
    y0, x0, y1, x1 = (int(round(value)) for value in proposed)
    if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
        raise ValueError("자료 크롭 좌표가 잘못되었습니다.")
    return [y0, x0, y1, x1]


def pixel_box_from_proposed(size, proposed):
    """Map a 0–1000 ymin,xmin,ymax,xmax box onto pixel xyxy without raster snapping."""
    width, height = size
    y0, x0, y1, x1 = normalize_proposed_box(proposed)
    pixels = [
        max(0, math.floor(x0 * width / 1000)),
        max(0, math.floor(y0 * height / 1000)),
        min(width, math.ceil(x1 * width / 1000)),
        min(height, math.ceil(y1 * height / 1000)),
    ]
    if pixels[0] >= pixels[2] or pixels[1] >= pixels[3]:
        raise ValueError("자료 크롭 좌표가 잘못되었습니다.")
    return pixels


def region_bbox_from_proposed(region_bbox, proposed):
    rx0, ry0, rx1, ry1 = region_bbox
    y0, x0, y1, x1 = normalize_proposed_box(proposed)
    width, height = rx1 - rx0, ry1 - ry0
    return [
        rx0 + x0 / 1000 * width,
        ry0 + y0 / 1000 * height,
        rx0 + x1 / 1000 * width,
        ry0 + y1 / 1000 * height,
    ]


def _embedded_image_snap_applies(size, proposed, region, image_boxes):
    if not image_boxes:
        return False
    width, height = size
    rx0, ry0, rx1, ry1 = region
    y0, x0, y1, x1 = normalize_proposed_box(proposed)
    sx, sy = width / (rx1 - rx0), height / (ry1 - ry0)
    initial = [x0 * width / 1000, y0 * height / 1000, x1 * width / 1000, y1 * height / 1000]
    bounds = initial.copy()
    for box in image_boxes:
        ax0, ay0, ax1, ay1 = box
        if not (rx0 <= ax0 < ax1 <= rx1 and ry0 <= ay0 < ay1 <= ry1):
            continue
        obj = [(ax0 - rx0) * sx, (ay0 - ry0) * sy, (ax1 - rx0) * sx, (ay1 - ry0) * sy]
        area = (obj[2] - obj[0]) * (obj[3] - obj[1])
        if area <= 0:
            continue
        overlap = max(0, min(initial[2], obj[2]) - max(initial[0], obj[0])) * max(
            0, min(initial[3], obj[3]) - max(initial[1], obj[1])
        )
        if overlap / area < 0.6:
            continue
        expanded = obj[0] < bounds[0] or obj[1] < bounds[1] or obj[2] > bounds[2] or obj[3] > bounds[3]
        if expanded:
            return True
    return False


def _refine_box_from_ink(image, proposed):
    """Shrink a Vision box toward non-text ink inside the proposal (scan-friendly)."""
    proposed = normalize_proposed_box(proposed)
    px = pixel_box_from_proposed(image.size, proposed)
    x0, y0, x1, y1 = px
    if x1 - x0 < 12 or y1 - y0 < 12:
        return proposed
    crop = image.crop((x0, y0, x1, y1)).convert("L")
    width, height = crop.size
    scale = min(1.0, 512 / max(width, height))
    sw, sh = max(1, int(width * scale)), max(1, int(height * scale))
    small = crop.resize((sw, sh)) if scale < 1 else crop
    pixels = list(small.getdata())
    ink = [value < 210 for value in pixels]

    components = []
    seen = [False] * (sw * sh)
    for start in range(sw * sh):
        if not ink[start] or seen[start]:
            continue
        stack = [start]
        seen[start] = True
        minx = maxx = start % sw
        miny = maxy = start // sw
        count = 0
        while stack:
            index = stack.pop()
            count += 1
            x, y = index % sw, index // sw
            minx, maxx = min(minx, x), max(maxx, x)
            miny, maxy = min(miny, y), max(maxy, y)
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < sw and 0 <= ny < sh:
                    neighbor = ny * sw + nx
                    if ink[neighbor] and not seen[neighbor]:
                        seen[neighbor] = True
                        stack.append(neighbor)
        components.append((minx, miny, maxx, maxy, count, maxx - minx + 1, maxy - miny + 1))

    if not components:
        return proposed

    kept = []
    noise = max(6, sw * sh * 0.00025)
    for minx, miny, maxx, maxy, count, cw, ch in components:
        if count < noise:
            continue
        rel_h = ch / sh
        rel_w = cw / sw
        if rel_h < 0.055 and rel_w > 0.32:
            continue
        if rel_h < 0.09 and rel_w > 0.68 and miny > sh * 0.5:
            continue
        kept.append((minx, miny, maxx, maxy))

    if not kept:
        return proposed

    kminx = min(part[0] for part in kept)
    kminy = min(part[1] for part in kept)
    kmaxx = max(part[2] for part in kept)
    kmaxy = max(part[3] for part in kept)
    py0, px0, py1, px1 = proposed
    sub_w, sub_h = px1 - px0, py1 - py0
    pad = 0.01
    ny0 = py0 + (kminy / sh) * sub_h - pad * sub_h
    ny1 = py0 + ((kmaxy + 1) / sh) * sub_h + pad * sub_h
    nx0 = px0 + (kminx / sw) * sub_w - pad * sub_w
    nx1 = px0 + ((kmaxx + 1) / sw) * sub_w + pad * sub_w
    ny0 = max(py0, ny0)
    nx0 = max(px0, nx0)
    ny1 = min(py1, ny1)
    nx1 = min(px1, nx1)
    if ny1 - ny0 < 8 or nx1 - nx0 < 8:
        return proposed
    return normalize_proposed_box([ny0, nx0, ny1, nx1])


def _refine_box_exclude_pdf_text(size, proposed, document, page, region_bbox):
    proposed = normalize_proposed_box(proposed)
    try:
        with pdfplumber.open(document) as pdf:
            chars = pdf.pages[page - 1].chars or []
    except Exception:
        return proposed
    if not chars:
        return proposed
    rx0, ry0, rx1, ry1 = region_bbox
    rw, rh = rx1 - rx0, ry1 - ry0
    if rw <= 0 or rh <= 0:
        return proposed
    py0, px0, py1, px1 = proposed
    in_box = []
    for char in chars:
        mid_x = (char["x0"] + char["x1"]) / 2
        mid_y = (char["top"] + char["bottom"]) / 2
        if mid_x < rx0 or mid_x > rx1 or mid_y < ry0 or mid_y > ry1:
            continue
        yn = (mid_y - ry0) / rh * 1000
        xn = (char["x0"] - rx0) / rw * 1000
        if yn < py0 or yn > py1 or xn < px0 or xn > px1:
            continue
        in_box.append((yn, xn))
    if len(in_box) < 12:
        return proposed
    in_box.sort(key=lambda pair: pair[0])
    lines, current = [], [in_box[0]]
    for item in in_box[1:]:
        if item[0] - current[-1][0] <= 8:
            current.append(item)
        else:
            lines.append(current)
            current = [item]
    lines.append(current)
    box_w = px1 - px0
    text_tops = []
    for line in lines:
        if len(line) < 4:
            continue
        avg_y = sum(value[0] for value in line) / len(line)
        span = max(value[1] for value in line) - min(value[1] for value in line)
        if span > box_w * 0.45 and avg_y > py0 + (py1 - py0) * 0.35:
            text_tops.append(avg_y)
    if not text_tops:
        return proposed
    cutoff = min(text_tops) - max(4.0, (py1 - py0) * 0.02)
    if cutoff <= py0 + (py1 - py0) * 0.15:
        return proposed
    return normalize_proposed_box([py0, px0, int(round(cutoff)), px1])


def refine_extract_diagram_box(image, proposed, region_bbox, document, page, image_boxes):
    """Adjust extract Vision boxes before raster crop; keep embedded-PDF snap untouched."""
    vision = normalize_proposed_box(proposed)
    meta = {"vision_proposed_box": vision, "box_refine": "none"}
    if _embedded_image_snap_applies(image.size, vision, region_bbox, image_boxes):
        meta["box_refine"] = "embedded_snap"
        return vision, meta
    if vision == [0, 0, 1000, 1000]:
        meta["box_refine"] = "full_region"
        return vision, meta
    refined = _refine_box_from_ink(image, vision)
    if document and page and Path(document).is_file():
        refined = _refine_box_exclude_pdf_text(image.size, refined, document, page, region_bbox)
    if refined != vision:
        meta["box_refine"] = "ink_pdf_text"
    return refined, meta


def diagram_crop_from_vision(image, vision_box, region_bbox, document_path, page, image_boxes):
    """Run extract-style Vision box refine and raster crop on one region image."""
    refined, refine_meta = refine_extract_diagram_box(
        image, vision_box, region_bbox, document_path, page, image_boxes,
    )
    pixel_box, provenance = complete_image_crop(image.size, refined, region_bbox, image_boxes)
    provenance.update(refine_meta)
    if refine_meta.get("box_refine") == "ink_pdf_text":
        provenance["review_required"] = True
    return image.crop(tuple(pixel_box)), refined, pixel_box, provenance


def region_for_asset(item, asset):
    crop = asset.get("crop") or {}
    key = "solution_regions" if asset.get("section") == "solution" else "regions"
    pool = item.get(key) or []
    document = crop.get("document")
    page = crop.get("page")
    index = crop.get("region_index")
    if index is not None:
        try:
            idx = int(index)
        except (TypeError, ValueError):
            idx = None
        if idx is not None and 0 <= idx < len(pool):
            region = pool[idx]
            if (not document or region.get("document") == document) and (
                    page is None or region.get("page") == page):
                return region
    source = crop.get("source_region")
    matches = []
    for region in pool:
        if document and region.get("document") != document:
            continue
        if page is not None and region.get("page") != page:
            continue
        if source is not None and list(region.get("bbox") or []) != list(source):
            continue
        matches.append(region)
    if len(matches) == 1:
        return matches[0]
    if source is not None:
        loose = []
        for region in pool:
            if document and region.get("document") != document:
                continue
            if page is not None and region.get("page") != page:
                continue
            loose.append(region)
        if len(loose) == 1:
            return loose[0]
        raise ValueError("자료의 원본 영역을 찾지 못했습니다.")
    if source is None and len(pool) == 1:
        return pool[0]
    raise ValueError("자료의 원본 영역을 찾지 못했습니다.")


def complete_image_crop(size, proposed, region, image_boxes):
    """Return pixel xyxy plus provenance; never expand beyond the source region.

    Only mostly selected raster objects wholly inside the region are completed.
    Captions outside the raster, vectors, and full-page scans still need review.
    """
    width, height = size
    rx0, ry0, rx1, ry1 = region
    y0, x0, y1, x1 = proposed
    if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
        raise ValueError("자료 크롭 좌표가 잘못되었습니다.")
    sx, sy = width / (rx1 - rx0), height / (ry1 - ry0)
    initial = [x0 * width / 1000, y0 * height / 1000, x1 * width / 1000, y1 * height / 1000]
    bounds = initial.copy()
    completed = []
    for box in image_boxes:
        ax0, ay0, ax1, ay1 = box
        if not (rx0 <= ax0 < ax1 <= rx1 and ry0 <= ay0 < ay1 <= ry1):
            continue
        obj = [(ax0-rx0)*sx, (ay0-ry0)*sy, (ax1-rx0)*sx, (ay1-ry0)*sy]
        area = (obj[2]-obj[0])*(obj[3]-obj[1])
        overlap = max(0, min(initial[2],obj[2])-max(initial[0],obj[0])) * max(0,min(initial[3],obj[3])-max(initial[1],obj[1]))
        if overlap / area < 0.6:
            continue
        if obj[0] < bounds[0] or obj[1] < bounds[1] or obj[2] > bounds[2] or obj[3] > bounds[3]:
            completed.append(box)
            bounds = [min(bounds[0],obj[0]),min(bounds[1],obj[1]),max(bounds[2],obj[2]),max(bounds[3],obj[3])]
    pixels = [max(0,math.floor(bounds[0])-2),max(0,math.floor(bounds[1])-2),
              min(width,math.ceil(bounds[2])+2),min(height,math.ceil(bounds[3])+2)]
    return pixels, {"proposed_box": proposed, "pixel_box": pixels, "completed_image_boxes": completed,
                    "source_region": region, "review_required": True}
