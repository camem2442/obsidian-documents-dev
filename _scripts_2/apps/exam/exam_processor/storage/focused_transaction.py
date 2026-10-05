"""Scoped exception rollback for R3's existing multi-file service writes."""
from contextlib import contextmanager
import os
import tempfile


def _restore(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.r3-restore-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def restore_on_failure(paths):
    def files():
        return {p for root in paths for p in (root.rglob('*') if root.is_dir() else [root]) if p.is_file()}
    before = {path: path.read_bytes() for path in files()}
    try:
        yield
    except Exception:
        for path in files() - before.keys():
            path.unlink()
        for path, content in before.items():
            if not path.exists() or path.read_bytes() != content:
                _restore(path, content)
        raise
