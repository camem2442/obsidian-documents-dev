"""Cached local OCR of workbook number anchors, never content approval."""
import csv
import io
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pypdfium2 as pdfium

from ..domain.models import digest, identity
from ..storage.store import atomic_json


def number_anchors(path, pages, prefix, cache):
    executable = shutil.which("tesseract")
    if not executable:
        raise ValueError("번호 후보 인식에는 로컬 tesseract가 필요합니다. 확인된 영역 기록으로 직접 가져올 수도 있습니다.")
    if not re.fullmatch(r"[A-Z][0-9]*", prefix):
        raise ValueError("교재 번호 접두사가 잘못되었습니다.")
    path = Path(path).resolve()
    cache = Path(cache).expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    fingerprint = digest(path)
    version = subprocess.run([executable, "--version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    pattern = re.compile(rf"^{prefix}[·.\-:•]?([0-9]{{2}})$")
    result = []
    with pdfium.PdfDocument(path) as document:
        for page_number in pages:
            if type(page_number) is not int or not 1 <= page_number <= len(document):
                raise ValueError("번호 인식 페이지가 원본 밖입니다.")
            key = identity(fingerprint, page_number, "number-anchors-2", version, prefix, 2, 11, "eng")
            saved = cache / f"{key}.json"
            if saved.is_file():
                result.extend(json.loads(saved.read_text(encoding="utf-8"))["anchors"])
                continue
            page = document[page_number - 1]
            width, height = page.get_size()
            with tempfile.TemporaryDirectory() as tmp:
                image_path = (Path(tmp) / "page.png").resolve()
                bitmap = page.render(scale=2)
                bitmap.to_pil().save(image_path)
                bitmap.close()
                page.close()
                output = subprocess.run([executable, str(image_path), "stdout", "-l", "eng", "--psm", "11", "tsv"],
                                        capture_output=True, text=True, check=True, timeout=90)
            anchors = []
            for word in csv.DictReader(io.StringIO(output.stdout), delimiter="\t"):
                match = pattern.fullmatch(word.get("text", "").strip())
                if not match:
                    continue
                left, top, w, h = (int(word[k]) / 2 for k in ("left", "top", "width", "height"))
                if not (.04 * width < left < .9 * width and .05 * height < top < .9 * height):
                    continue
                anchors.append({"number": f"{prefix}·{match[1]}", "page": page_number,
                                "bbox": [left, top, left + w, top + h], "width": width, "height": height,
                                "confidence": float(word["conf"]), "status": "candidate"})
            anchors.sort(key=lambda a: (a["bbox"][0] > width * .42, a["bbox"][1]))
            atomic_json(saved, {"source_sha256": fingerprint, "engine": version, "anchors": anchors})
            result.extend(anchors)
    return result
