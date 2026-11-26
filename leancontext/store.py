"""Private, atomic local artifacts. No database, network, or background service."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid


def _private_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f'Refusing symlink state directory: {path}')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == 'posix':
        path.chmod(0o700)


def _atomic(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Store:
    def __init__(self, root: str | Path, state_dir: str | Path | None = None):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError('Repository root must be an existing directory')
        if state_dir is None:
            cache = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache')))
            namespace = hashlib.sha256(os.fsencode(self.root)).hexdigest()[:16]
            state_dir = cache / 'leancontext' / namespace
        raw_base = Path(state_dir).expanduser().absolute()
        if raw_base.is_symlink():
            raise ValueError('State directory must not be a symlink')
        self.base = raw_base.resolve()
        if self.base in (self.root, Path.home().resolve(), Path('/')):
            raise ValueError('Use a dedicated state directory')
        _private_dir(self.base)
        _private_dir(self.base / 'artifacts')
        _private_dir(self.base / 'events')

    def _path(self, ref: str, suffix: str) -> Path:
        if not isinstance(ref, str) or not re.fullmatch(r'[0-9a-f]{24}', ref):
            raise ValueError('Artifact reference must be 24 lowercase hexadecimal characters')
        p = self.base / 'artifacts' / (ref + suffix)
        if p.is_symlink() or p.parent.is_symlink():
            raise ValueError('Refusing symlink artifact')
        return p

    def save(self, text: str, kind: str = 'output', metadata: dict | None = None) -> str:
        return self.save_bytes(text.encode('utf-8'), kind, metadata)

    def save_bytes(self, data: bytes, kind: str = 'output', metadata: dict | None = None) -> str:
        # Ref is content-addressed; a separate event log records individual uses.
        digest = hashlib.sha256(data).hexdigest()
        ref = digest[:24]
        p = self._path(ref, '.gz')
        if not p.exists():
            _atomic(p, gzip.compress(data, compresslevel=5, mtime=0))
        info = {'ref': ref, 'sha256': digest, 'bytes': len(data),
                'lines': len(data.splitlines()), 'kind': kind,
                'created_at': time.time(), 'metadata': metadata or {}}
        _atomic(self._path(ref, '.json'), json.dumps(info, ensure_ascii=False).encode('utf-8'))
        return ref

    def read_bytes(self, ref: str) -> bytes:
        with gzip.open(self._path(ref, '.gz'), 'rb') as f:
            raw = f.read(1024 * 1024 * 1024 + 1)
        if len(raw) > 1024 * 1024 * 1024:
            raise ValueError('Artifact exceeds 1 GiB recovery limit')
        if hashlib.sha256(raw).hexdigest()[:24] != ref:
            raise ValueError('Artifact content hash mismatch')
        return raw

    def read(self, ref: str) -> str:
        return self.read_bytes(ref).decode('utf-8', errors='replace')

    def info(self, ref: str) -> dict:
        return json.loads(self._path(ref, '.json').read_text(encoding='utf-8'))

    def record(self, event: dict) -> None:
        obj = dict(event)
        obj.setdefault('timestamp', time.time())
        name = f'{time.time_ns()}-{uuid.uuid4().hex}.json'
        _atomic(self.base / 'events' / name, json.dumps(obj, ensure_ascii=False).encode('utf-8'))

    def stats(self) -> dict:
        counts = {'events': 0, 'original_chars': 0, 'emitted_chars': 0,
                  'original_bytes': 0, 'emitted_bytes': 0, 'recovery_calls': 0}
        warnings = []
        for p in (self.base / 'events').glob('*.json'):
            if p.is_symlink():
                continue
            try:
                e = json.loads(p.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                warnings.append(f'Unreadable event: {p.name}')
                continue
            counts['events'] += 1
            for k in ('original_chars', 'emitted_chars', 'original_bytes', 'emitted_bytes'):
                if isinstance(e.get(k), int) and not isinstance(e[k], bool):
                    counts[k] += max(0, e[k])
            counts['recovery_calls'] += e.get('operation') in ('recall', 'recall_many')
        counts['artifacts'] = sum(1 for _ in (self.base / 'artifacts').glob('*.gz'))
        counts['state_dir'] = str(self.base)
        counts['warnings'] = warnings
        counts['measurement'] = 'Local text/byte volume, not provider tokens, cost, or quota.'
        return counts
