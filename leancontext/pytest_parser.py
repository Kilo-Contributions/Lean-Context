"""Conservative parsing of pytest terminal reports, without importing pytest.

This indexes printed evidence, not pytest's internal state. Summary counts and
unresolved headings make missing evidence explicit; suppressed output cannot be
reconstructed. Signatures identify equal observed exception/frame evidence and
must not be interpreted as proof that two failures have the same root cause.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import re

from .diagnostic_types import Diagnostic, DiagnosticReport


_ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))')
_SECTION = re.compile(r'^={3,}\s*(.*?)\s*={3,}\s*$')
_HEADING = re.compile(r'^_{2,}\s+(.+?)\s+_{2,}\s*$')
_CAPTURED = re.compile(r'^-{3,}\s+Captured\s+.+?\s+-{3,}\s*$')
_COUNT = re.compile(r'\b(\d+)\s+(failed|errors?|passed|skipped|xfailed|xpassed|deselected|warnings?|rerun)\b')
_SUMMARY_ROW = re.compile(r'^(FAILED|ERROR)\s+(.+?)\s*$')
_PROGRESS = re.compile(r'^(?P<node>.+?::.+?)\s+(?P<status>FAILED|ERROR|PASSED|SKIPPED|XFAIL|XPASS)(?:\s+\[\s*\d+%\])?\s*$')
_XDIST_PROGRESS = re.compile(r'^\[gw\d+\]\s+(?:\[\s*\d+%\]\s+)?(?P<status>FAILED|ERROR|PASSED|SKIPPED|XFAIL|XPASS)\s+(?P<node>.+?)\s*$')
_FRAME = re.compile(r'^(?P<path>.+?):(?P<line>\d+):(?P<tail>.*)$')
_NATIVE_FRAME = re.compile(r'^\s+File "([^"]+)", line (\d+)(?:, in .+)?$')
_EXCEPTION_LINE = re.compile(r'^E(?:\s+|$)(.*)$')


@dataclass
class _Entry:
    label: str
    status: str
    summary: str
    full_identifier: bool = True
    block: '_Block | None' = None


@dataclass
class _Block:
    heading: str
    status: str
    phase: str
    start: int
    end: int
    detail: str
    traceback: str
    frames: tuple[str, ...]
    message: str
    signature: str | None


def _alias(nodeid: str) -> str:
    """Pytest headings replace class separators, but not parameter contents."""
    rest = nodeid.split('::', 1)[-1]
    name, bracket, parameters = rest.partition('[')
    return name.replace('::', '.') + bracket + parameters


def _summary_parts(rest: str, known: set[str], headings: set[str]) -> tuple[str, str, bool]:
    # Verbose progress supplies an unambiguous identifier even when parameters
    # contain the summary delimiter itself. Prefer the longest matching one.
    if rest in known:
        return rest, '', True
    delimiters = [match.start() for match in re.finditer(' - ', rest)]
    for position in reversed(delimiters):
        if rest[:position] in known:
            return rest[:position], rest[position + 3:], True
    # Literal ']' characters can make depth counting close the parameter early.
    # A complete failure heading disambiguates the identifier even in -q mode.
    if _alias(rest) in headings:
        return rest, '', True
    for position in reversed(delimiters):
        if _alias(rest[:position]) in headings:
            return rest[:position], rest[position + 3:], True
    parameter_delimiters = [position for position in delimiters
                            if rest[:position].endswith(']')
                            and re.match(r'(?:[\w.]+:|assert\b|\[XPASS\(strict\)\])', rest[position + 3:])]
    if parameter_delimiters:
        if len(parameter_delimiters) == 1:
            position = parameter_delimiters[0]
            return rest[:position], rest[position + 3:], True
        # With --tb=no and no verbose progress there may be no way to distinguish
        # a delimiter inside an ID from one inside its exception message. Keep
        # the entire printed summary rather than truncating an unknown node ID.
        return rest, '', False
    depth = 0
    for i, char in enumerate(rest):
        if char == '[':
            depth += 1
        elif char == ']':
            depth = max(0, depth - 1)
        if depth == 0 and rest.startswith(' - ', i):
            return rest[:i], rest[i + 3:], True
    return rest, '', not bool(delimiters)


def _frame_path(line: str) -> str | None:
    match = _FRAME.match(line)
    if match:
        path = match.group('path')
        if path.endswith(('.py', '.pyw', '.pyx')) or path.startswith('<'):
            return path
    match = _NATIVE_FRAME.match(line)
    return match.group(1) if match else None


def _make_block(heading: str, section: str, start: int, end: int,
                raw_lines: list[str], lines: list[str]) -> _Block:
    phase = 'call'
    status = 'FAILED'
    name = heading
    if section == 'ERRORS' or heading.startswith('ERROR '):
        status, phase = 'ERROR', 'unknown'
        for prefix, candidate in (('ERROR at setup of ', 'setup'),
                                  ('ERROR at teardown of ', 'teardown'),
                                  ('ERROR collecting ', 'collect')):
            if heading.startswith(prefix):
                name, phase = heading[len(prefix):], candidate
                break
    detail = ''.join(raw_lines[start:end])
    trace_end = next((i for i in range(start + 1, end) if _CAPTURED.match(lines[i])), end)
    trace_lines = lines[start + 1:trace_end]
    frames = tuple(line.rstrip() for line in trace_lines if _frame_path(line) is not None)
    exceptions = tuple(match.group(1).strip() for line in trace_lines
                       if (match := _EXCEPTION_LINE.match(line)))
    native = any(line.startswith('Traceback (most recent call last):') for line in trace_lines)
    # A failure body can be a complete exception-group tree or arbitrary text
    # from pytest.fail(pytrace=False), without familiar E/path lines. Preserve
    # every nonempty pre-capture diagnostic body; unfamiliar syntax stays a
    # singleton instead of disappearing from the representative evidence.
    traceback = ''.join(raw_lines[start + 1:trace_end]) if any(line.strip() for line in trace_lines) else ''
    signature = None
    message = '\n'.join(exceptions)
    if native and frames:
        # Native exception chains are arbitrary multiline text. Hash all of the
        # displayed traceback rather than dropping a message continuation.
        signature_data = [phase, 'native', trace_lines]
        signature = hashlib.sha256(json.dumps(signature_data, ensure_ascii=False).encode()).hexdigest()
        if not message:
            message = next((line for line in reversed(trace_lines) if line and not line[0].isspace()), '')
    elif frames and exceptions:
        # Keep every displayed frame, exception/assertion line, and marked source
        # expression. Do not normalize addresses, values, paths, or line numbers.
        source = tuple(line.rstrip() for line in trace_lines if line.startswith('>'))
        chains = tuple(line for line in trace_lines
                       if line.startswith(('The above exception was the direct cause',
                                           'During handling of the above exception')))
        signature_data = [phase, frames, exceptions, source, chains]
        # Exception-group trees carry additional nested frames outside pytest's
        # ordinary E/path format. Preserve all text but do not equate a partial
        # signature that excludes those nested branches.
        if not any('Exception Group Traceback' in line or re.match(r'^\s*[|+]\s', line) for line in trace_lines):
            signature = hashlib.sha256(json.dumps(signature_data, ensure_ascii=False).encode()).hexdigest()
    return _Block(name, status, phase, start, end, detail, traceback, frames, message, signature)


def _matches_file(block: _Block, label: str) -> bool:
    wanted = label.split('::', 1)[0].replace('\\', '/')
    for line in block.frames:
        path = (_frame_path(line) or '').replace('\\', '/')
        if path == wanted or path.endswith('/' + wanted):
            return True
    return False


def _line_blocks(start: int, end: int, entries: list[_Entry], raw_lines: list[str],
                 lines: list[str]) -> list[_Block] | None:
    """Recognize --tb=line only when every segment matches its summary in order.

    Pytest prints these failure records and short-summary rows in report order.
    A mismatch (including a frame-looking line inside a captured log) leaves the
    entire headingless section unresolved instead of assigning a guessed trace.
    A line-style record never gets a group signature: intervening stack frames
    were not printed and are therefore unavailable for equality checking.
    """
    candidates = [entry for entry in entries if entry.status == 'FAILED' and entry.block is None]
    result = []
    cursor = start
    for entry in candidates:
        if entry.summary.startswith('[XPASS(strict)]'):
            stop = cursor
            while stop < end and (not lines[stop].strip() or lines[stop].startswith('[XPASS(strict)]')):
                stop += 1
            if stop == cursor:
                return None
            result.append(_Block(_alias(entry.label), 'FAILED', 'call', cursor, stop,
                                 ''.join(raw_lines[cursor:stop]), '', (), entry.summary, None))
        else:
            stop = next((i for i in range(cursor, end) if _frame_path(lines[i]) is not None), None)
            if stop is None:
                return None
            frame = _FRAME.match(lines[stop])
            if frame is None or frame.group('tail').strip() != entry.summary:
                return None
            stop += 1
            captured = next((i for i in range(cursor, stop) if _CAPTURED.match(lines[i])), stop - 1)
            trace = ''.join(raw_lines[cursor:captured]) + raw_lines[stop - 1]
            result.append(_Block(_alias(entry.label), 'FAILED', 'call', cursor, stop,
                                 ''.join(raw_lines[cursor:stop]), trace, (lines[stop - 1],), entry.summary, None))
        cursor = stop
    return result if result and not any(line.strip() for line in lines[cursor:end]) else None


def parse_pytest(text: str) -> DiagnosticReport | None:
    """Index available failures and full captured sections; return None if unknown.

    ``inventory_complete`` addresses identifiers only. Missing traceback bodies
    are reported separately in ``notices`` and never given group signatures.
    ``None`` completeness means no final pytest total was available to reconcile.
    """
    raw_lines = text.splitlines(keepends=True)
    lines = [_ANSI.sub('', line).rstrip('\r\n') for line in raw_lines]
    if not lines:
        return None
    sections = {i: match.group(1).strip() for i, line in enumerate(lines)
                if (match := _SECTION.match(line))}
    credible = any(title in {'test session starts', 'FAILURES', 'ERRORS', 'short test summary info'}
                   for title in sections.values())
    credible = credible or any(re.search(r'\bpytest-\d+\.\d+', line) for line in lines[:20])
    progress = []
    diagnostic_start = min((i for i, title in sections.items()
                            if title in ('FAILURES', 'ERRORS', 'short test summary info')), default=len(lines))
    for i, line in enumerate(lines[:diagnostic_start]):
        match = _PROGRESS.match(line) or _XDIST_PROGRESS.match(line)
        if match:
            nodeid, status = match.group('node').rstrip(), match.group('status')
            progress.append((i, nodeid, status))
    credible = credible or bool(progress)
    if not credible:
        return None

    summaries = []
    for i, line in enumerate(lines):
        cleaned = sections.get(i, line).strip()
        counts = list(_COUNT.finditer(cleaned))
        if counts and re.search(r'\bin\s+\S+', cleaned) and (i in sections or not line[:1].isspace()):
            # Terminal completion starts with the counters, never a source/log
            # phrase containing numbers somewhere inside its message.
            if _COUNT.match(cleaned):
                summaries.append((i, cleaned, counts))
    summary = summaries[-1][1] if summaries else 'Pytest run: final test totals unavailable.'
    expected = (sum(int(m.group(1)) for m in summaries[-1][2]
                    if m.group(2) in ('failed', 'error', 'errors')) if summaries else None)

    known = {node for _, node, _ in progress}
    known_headings: set[str] = set()
    current = ''
    for i, line in enumerate(lines):
        if i in sections:
            current = sections[i]
        elif current in ('FAILURES', 'ERRORS') and (match := _HEADING.match(line)):
            heading = match.group(1)
            for prefix in ('ERROR at setup of ', 'ERROR at teardown of ', 'ERROR collecting '):
                if heading.startswith(prefix):
                    heading = heading[len(prefix):]
                    break
            known_headings.add(heading)
    entries: list[_Entry] = []
    current = ''
    for i, line in enumerate(lines):
        if i in sections:
            current = sections[i]
            continue
        if current == 'short test summary info' and (match := _SUMMARY_ROW.match(line)):
            nodeid, message, identified = _summary_parts(match.group(2), known, known_headings)
            entries.append(_Entry(nodeid, match.group(1), message, full_identifier=identified))
    # -rN can suppress short summaries. Keep every otherwise-unrepresented
    # failing progress record, including a second error phase for the same node.
    observed: dict[tuple[str, str], int] = {}
    entry_counts = Counter((entry.label, entry.status) for entry in entries)
    for _, nodeid, status in progress:
        if status not in ('FAILED', 'ERROR'):
            continue
        pair = (nodeid, status)
        observed[pair] = observed.get(pair, 0) + 1
        already = entry_counts[pair]
        if observed[pair] > already:
            entries.append(_Entry(nodeid, status, ''))
            entry_counts[pair] += 1

    block_starts: list[tuple[int, str, str]] = []
    current = ''
    for i, line in enumerate(lines):
        if i in sections:
            current = sections[i]
        elif current in ('FAILURES', 'ERRORS') and (match := _HEADING.match(line)):
            block_starts.append((i, match.group(1), current))
    blocks = []
    boundaries = sorted(set(sections) | {i for i, _, _ in block_starts} | {len(lines)})
    for start, heading, section in block_starts:
        end = boundaries[bisect_right(boundaries, start)]
        blocks.append(_make_block(heading, section, start, end, raw_lines, lines))

    notices: list[str] = []
    ambiguous_summaries = sum(not entry.full_identifier for entry in entries)
    if ambiguous_summaries:
        notices.append(f'{ambiguous_summaries} summary line(s) have ambiguous identifier/message separators; full printed lines retained without claiming exact identifier resolution.')
    for start, title in sections.items():
        if title != 'FAILURES':
            continue
        end = boundaries[bisect_right(boundaries, start)]
        # There should be a heading immediately after an ordinary section.
        # Any substantial headingless portion must be retained, including
        # plugin-specific diagnostics that the standard parser cannot name.
        if not any(line.strip() for line in lines[start + 1:end]):
            continue
        found = _line_blocks(start + 1, end, entries, raw_lines, lines)
        if found:
            blocks.extend(found)
            notices.append('Line-style pytest diagnostics preserve only printed exception locations; full stacks are unavailable and these records are not grouped.')
        else:
            detail = ''.join(raw_lines[start + 1:end])
            blocks.append(_Block('headingless FAILURES section', 'FAILED', 'call', start + 1, end,
                                 detail, detail, (), '', None))
    unresolved = 0
    entry_index: dict[tuple[str, str], list[_Entry]] = defaultdict(list)
    for entry in entries:
        entry_index[(entry.status, _alias(entry.label))].append(entry)
    for block in blocks:
        candidates = [entry for entry in entry_index[(block.status, block.heading)] if entry.block is None]
        # A class/function heading can occur in several files. Only use a frame
        # to disambiguate when it uniquely identifies one full node ID.
        if len({entry.label for entry in candidates}) > 1:
            candidates = [entry for entry in candidates if _matches_file(block, entry.label)]
        if candidates and len({entry.label for entry in candidates}) == 1:
            candidates[0].block = block
        elif block.phase == 'collect' and not candidates:
            entries.append(_Entry(block.heading, block.status, block.message, block=block))
        else:
            unresolved += 1
            entries.append(_Entry(f'[unresolved identifier] {block.heading}', block.status,
                                  block.message, full_identifier=False, block=block))
    if unresolved:
        notices.append(f'{unresolved} traceback heading(s) could not be tied unambiguously to a full test identifier; retained separately.')
    missing_tracebacks = sum(entry.block is None or not entry.block.traceback for entry in entries)
    if missing_tracebacks:
        notices.append(f'{missing_tracebacks} failure/error record(s) have no captured traceback; pytest may have suppressed it or produced a non-traceback failure. Details remain separate.')

    diagnostics = []
    for i, entry in enumerate(entries, 1):
        block = entry.block
        diagnostics.append(Diagnostic(
            id=f'F{i}', label=entry.label, status=entry.status,
            detail=block.detail if block else f'{entry.status} {entry.label}' + (f' - {entry.summary}' if entry.summary else '') + '\n',
            traceback=block.traceback if block else '',
            message=block.message or entry.summary if block else entry.summary,
            phase=block.phase if block else ('call' if entry.status == 'FAILED' else 'unknown'),
            signature=block.signature if block and entry.full_identifier else None,
            start_line=block.start + 1 if block else None,
            end_line=block.end if block else None,
        ))
    complete = expected == len(diagnostics) and not unresolved and not ambiguous_summaries if expected is not None else None
    if expected is None:
        notices.append('Final pytest failure/error total unavailable; identifier inventory completeness is unknown and the capture may be incomplete.')
    elif expected != len(diagnostics):
        notices.append(f'Pytest reports {expected} failure/error record(s), but {len(diagnostics)} were indexed; identifier inventory is incomplete or ambiguous.')
    if any(line.startswith('INTERNALERROR>') for line in lines):
        # Do not convert an internal-error run to a successful-test summary.
        return None
    if diagnostics or (expected is not None and expected > 0):
        return DiagnosticReport('pytest-failures', summary, tuple(diagnostics), tuple(notices), complete, expected)
    if summaries and expected == 0:
        if any(title == 'warnings summary' for title in sections.values()):
            notices.append('Warnings summary and passing-test details remain in the raw artifact.')
        return DiagnosticReport('test-success', summary, (), tuple(notices), True, 0)
    return None
