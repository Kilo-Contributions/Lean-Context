"""Bounded exact artifact retrieval; never rerun a command to recover its output."""
from __future__ import annotations

from .store import Store


def recall(store: Store, ref: str, *, start: int = 1, limit: int = 80,
           query: str | None = None, max_chars: int = 6000, offset: int = 0) -> dict:
    for name, value, low, high in (('start', start, 1, 10**9), ('limit', limit, 1, 1000),
                                  ('max_chars', max_chars, 512, 50000), ('offset', offset, 0, 10**9)):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f'{name} must be an integer from {low} to {high}')
    if query is not None and (not isinstance(query, str) or not 1 <= len(query) <= 1000):
        raise ValueError('query must be 1–1000 literal characters')
    raw = store.read(ref)
    lines = raw.splitlines()
    matches = [i for i, s in enumerate(lines) if query in s] if query else None
    if query:
        candidates = [i for i in matches if i >= start-1][:limit]
    else:
        candidates = list(range(start-1, min(len(lines), start-1+limit)))
    header = f'[artifact {ref}; {len(lines)} lines; exact text excerpts]\n'
    if query:
        header += f'Literal matches: {len(matches)}; returning up to {limit}.\n'
    parts = [header]
    remaining = max_chars - len(header) - 160
    next_start = None
    next_offset = 0
    shown = 0
    for index in candidates:
        line = lines[index]
        line_offset = offset if index == start-1 else 0
        if line_offset > len(line):
            raise ValueError('offset is beyond the requested line')
        prefix = f'L{index+1}' + (f' [char {line_offset}]' if line_offset else '') + ': '
        item = prefix + line[line_offset:] + '\n'
        if len(item) > remaining:
            if shown:
                next_start = index+1
                break
            take = max(0, remaining-len(prefix)-2)
            parts.append(prefix + line[line_offset:line_offset+take] + '\n')
            next_start, next_offset = index+1, line_offset+take
            break
        parts.append(item)
        remaining -= len(item)
        shown += 1
    else:
        if candidates:
            last = candidates[-1]+1
            following = next((i+1 for i in (matches or []) if i+1 > last), None) if query else last+1
            if following and following <= len(lines):
                next_start = following
    if next_start:
        parts.append(f'[More available: --start {next_start} --offset {next_offset}; omitted text retained]\n')
    result = ''.join(parts)
    store.record({'operation': 'recall', 'ref': ref, 'emitted_chars': len(result),
                  'emitted_bytes': len(result.encode('utf-8'))})
    return {'text': result, 'ref': ref, 'total_lines': len(lines), 'next_start': next_start,
            'next_offset': next_offset, 'matches': len(matches) if matches is not None else None,
            'truncated': next_start is not None}
