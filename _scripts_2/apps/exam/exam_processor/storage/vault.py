"""Explicit KS input selection and non-overwriting portable output."""
from pathlib import Path

from .. import config


def inside_vault(value, root=None):
    root = Path(root or config.KS_ROOT).resolve()
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError("KS 볼트 내부 경로를 선택하세요.")
    return path


def pdf_files(folder, store, root=None):
    folder = inside_vault(folder, root)
    if not folder.is_dir():
        raise ValueError("PDF 폴더가 없습니다.")
    known = {}
    for summary in store.list():
        job = store.get(summary["id"])
        known.setdefault(job["input_path"], []).append(job["id"])
    found = []
    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() != ".pdf" or not path.is_file():
            continue
        try:
            safe = inside_vault(path, root)
        except ValueError:
            continue
        found.append({"path": str(safe), "name": str(path.relative_to(folder)), "jobs": known.get(str(safe), [])})
        if len(found) >= 1000:
            break
    return {"files": found, "limit": 1000, "limited": len(found) == 1000}
