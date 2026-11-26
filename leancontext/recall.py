"""Bounded exact artifact retrieval; never rerun a command to recover its output."""
from __future__ import annotations

from .store import Store


def recall_many(store: Store, ref: str, *, selectors: list[str], max_chars: int = 6000,
                strategy: str = 'auto') -> dict:
    """Recall whole diagnostic details in one call, never rerun their command.

    F/D IDs select records; G IDs select a group's representative. An exact
    test identifier selects all phases of that test. Duplicates are returned
    once, in requested order. Unknown selectors are reported alongside hits.
    """
    from .diagnostics import group_diagnostics, load_report, semantic_result, validate_target
    validate_target(max_chars)
    if (not isinstance(selectors, list) or not 1 <= len(selectors) <= 100 or
            any(not isinstance(s, str) or not 1 <= len(s) <= 2000 for s in selectors)):
        raise ValueError('selectors must be a list of 1–100 IDs or exact identifiers (1–2000 characters each)')
    report = load_report(store, ref, strategy=strategy)
    groups = dict(group_diagnostics(report.diagnostics))
    by_id = {item.id: item for item in report.diagnostics}
    selected = {}
    missing = []
    for selector in selectors:
        if selector in by_id:
            found = [by_id[selector]]
        elif selector in groups:
            found = [groups[selector][0]]
        else:
            found = [item for item in report.diagnostics if item.label == selector]
        if not found and selector not in missing:
            missing.append(selector)
        for item in found:
            selected.setdefault(item.id, item)
    parts = [f'[LeanContext diagnostic recall; artifact {ref}; {len(selected)} complete record(s)]\n']
    for item in selected.values():
        parts.append(f'\n{item.id} {item.status} {item.label} ({item.phase})\n')
        parts.append(item.detail or item.traceback or item.message or 'No detail was captured for this record.')
        if not parts[-1].endswith('\n'):
            parts.append('\n')
    if missing:
        parts.append('\nUnmatched selectors: ' + ', '.join(missing) + '\n')
    for notice in report.notices:
        parts.append(f'NOTICE: {notice}\n')
    result = semantic_result(''.join(parts), max_chars=max_chars, ref=ref, strategy=report.strategy,
                             selected_ids=list(selected), missing=missing,
                             inventory_complete=report.inventory_complete)
    store.record({'operation': 'recall_many', 'ref': ref, 'emitted_chars': len(result['text']),
                  'emitted_bytes': len(result['text'].encode('utf-8'))})
    return result


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
