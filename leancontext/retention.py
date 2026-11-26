"""Dependency-aware offline archive pruning. No background maintenance.

The operator must stop all writers/readers before applying a plan. Inventory
rechecks detect drift but are not a cross-process locking protocol.
"""
from __future__ import annotations

from collections import deque
import gzip
import hashlib
import json
import math
import re
import stat
import time
import zlib

REF = re.compile(r'[0-9a-f]{24}\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
MAX_METADATA = 1024 * 1024
MAX_MANIFEST = 64 * 1024 * 1024
MAX_ARTIFACTS = 100_000
KEYS = ('raw_ref', 'stdout_ref', 'stderr_ref', 'result_ref', 'diagnostic_ref')
CAPTURE = b'{"leancontext_capture":1,'
DIAGNOSTIC = b'{"leancontext_diagnostics":1,'


def _stamp(path):
    s = path.lstat()
    if (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or
            getattr(s, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
        raise ValueError('Archive contains a linked or nonregular entry; prune held')
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _inventory(store):
    directory = store.base / 'artifacts'
    if directory.is_symlink() or getattr(directory, 'is_junction', lambda: False)():
        raise ValueError('Linked archive directory; prune held')
    if any(store.base.glob('capture-*')):
        raise ValueError('Capture directory present; stop writers or review interrupted capture')
    entries = {}
    for p in directory.iterdir():
        if p.name.startswith('.write-'):
            raise ValueError('Atomic write in progress or interrupted; prune held')
        if p.suffix not in ('.json', '.gz'):
            continue
        if not REF.fullmatch(p.stem):
            raise ValueError('Invalid archive filename; prune held')
        entries[p.name] = _stamp(p)
        if len(entries) > MAX_ARTIFACTS * 2:
            raise ValueError('Archive inventory limit exceeded; prune held')
    refs = {name[:-5] for name in entries if name.endswith('.json')}
    if set(entries) != {ref + ext for ref in refs for ext in ('.gz', '.json')}:
        raise ValueError('Unpaired archive entries; prune held')
    return entries, refs


def _refs(obj, *, required=()):
    result = set()
    if not isinstance(obj, dict):
        raise ValueError('Invalid archive dependency metadata')
    for key in KEYS:
        value = obj.get(key)
        if value is None and key not in required:
            continue
        if not isinstance(value, str) or not REF.fullmatch(value):
            raise ValueError('Invalid archive dependency reference')
        result.add(value)
    return result


def _dependencies(store, ref, entries):
    meta_path = store._path(ref, '.json')
    if entries[meta_path.name][2] > MAX_METADATA:
        raise ValueError('Archive metadata exceeds maintenance bound')
    with meta_path.open('rb') as stream:
        meta_raw = stream.read(MAX_METADATA + 1)
    if len(meta_raw) > MAX_METADATA:
        raise ValueError('Archive metadata exceeds maintenance bound')
    meta = json.loads(meta_raw)
    if (not isinstance(meta, dict) or meta.get('ref') != ref or
            not isinstance(meta.get('sha256'), str) or not SHA.fullmatch(meta['sha256']) or
            not meta['sha256'].startswith(ref) or type(meta.get('bytes')) is not int or meta['bytes'] < 0):
        raise ValueError('Invalid archive metadata; prune held')
    dependencies = _refs(meta.get('metadata', {}))
    # Raw bytes may have been saved under a different kind since a manifest
    # was created. Identify the canonical payload, not just mutable metadata.
    with gzip.open(store._path(ref, '.gz'), 'rb') as stream:
        prefix = stream.read(64)
        marker = CAPTURE if prefix.startswith(CAPTURE) else DIAGNOSTIC if prefix.startswith(DIAGNOSTIC) else None
        if marker:
            raw = prefix + stream.read(MAX_MANIFEST + 1 - len(prefix))
            if len(raw) > MAX_MANIFEST or len(raw) != meta['bytes'] or hashlib.sha256(raw).hexdigest() != meta['sha256']:
                raise ValueError('Manifest size or hash invalid; prune held')
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError('Invalid manifest object')
            if marker == CAPTURE:
                required = ('stdout_ref', 'stderr_ref', 'result_ref')
                if type(payload.get('leancontext_capture')) is not int or payload['leancontext_capture'] != 1:
                    raise ValueError('Unsupported capture schema')
                for key in required:
                    digest = payload.get(key.replace('_ref', '_sha256'))
                    target = payload.get(key)
                    if not isinstance(digest, str) or not SHA.fullmatch(digest) or not isinstance(target, str) or not digest.startswith(target):
                        raise ValueError('Invalid capture dependency digest')
            else:
                required = ('raw_ref',)
                if type(payload.get('leancontext_diagnostics')) is not int or payload['leancontext_diagnostics'] != 1 or not isinstance(payload.get('report'), dict):
                    raise ValueError('Unsupported diagnostic schema')
            dependencies.update(_refs(payload, required=required))
        elif meta.get('kind') in ('capture-manifest', 'diagnostic-snapshot'):
            raise ValueError('Manifest marker missing; prune held')
    dependencies.discard(ref)  # legacy metadata may identify identical stream bytes
    return dependencies


def _plan(store, cutoff):
    entries, refs = _inventory(store)
    graph = {ref: _dependencies(store, ref, entries) for ref in sorted(refs)}
    fresh = {ref for ref in refs if max(entries[ref + ext][3] for ext in ('.gz', '.json')) >= cutoff}
    retained = set(fresh)
    queue = deque(sorted(fresh))
    while queue:
        ref = queue.popleft()
        for dependency in sorted(graph[ref]):
            if dependency not in refs:
                raise ValueError('Retained archive references missing evidence; prune held')
            if dependency not in retained:
                retained.add(dependency)
                queue.append(dependency)
    expired = refs - retained
    # Delete dependents before dependencies, so an interrupted deletion does
    # not remove a leaf while an expired parent still exists.
    incoming = dict.fromkeys(expired, 0)
    for ref in expired:
        for dependency in graph[ref] & expired:
            incoming[dependency] += 1
    queue = deque(sorted(ref for ref, n in incoming.items() if not n))
    order = []
    while queue:
        ref = queue.popleft()
        order.append(ref)
        for dependency in sorted(graph[ref] & expired):
            incoming[dependency] -= 1
            if not incoming[dependency]:
                queue.append(dependency)
    if len(order) != len(expired):
        raise ValueError('Archive dependency cycle; prune held')
    if _inventory(store)[0] != entries:
        raise ValueError('Archive inventory changed during scan; prune held')
    return entries, order, len(retained), len(retained - fresh)


def prune(store, days=30, *, apply=False, quiescent=False):
    if type(days) not in (int, float) or not math.isfinite(days) or not 0 <= days <= 36500:
        raise ValueError('days must be 0–36500')
    if apply and not quiescent:
        raise ValueError('Stop all readers/writers and explicitly confirm --quiescent before applying prune')
    cutoff = int((time.time() - days * 86400) * 1_000_000_000)
    try:
        entries, order, retained, protected = _plan(store, cutoff)
    except (EOFError, zlib.error, RecursionError) as exc:
        raise ValueError('Malformed archive; prune held') from exc
    if apply:
        for ref in order:
            paths = [store._path(ref, ext) for ext in ('.gz', '.json')]
            if any(_stamp(p) != entries[p.name] for p in paths):
                raise ValueError('Archive changed before deletion; remaining prune held')
            for p in paths:
                p.unlink()
    return {'dry_run': not apply, 'artifacts': len(order), 'retained': retained,
            'protected_dependencies': protected, 'days': days,
            'warning': 'Pruned references cannot be recalled. Apply requires an offline, quiescent store; inventory checks are not a lock.',
            'state_dir': str(store.base)}
