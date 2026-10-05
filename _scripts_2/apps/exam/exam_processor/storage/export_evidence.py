"""Content-bound publication evidence; hashes are integrity checks, not signatures."""
import hashlib
from pathlib import Path


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def payload_files(root):
    root = Path(root)
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('출력 근거에 symlink가 있습니다. 기존 파일을 보존하세요.')
        if path.is_file() and path != root / 'manifest.json':
            result[path.relative_to(root).as_posix()] = {
                'sha256': file_hash(path), 'size': path.stat().st_size,
            }
    return result
