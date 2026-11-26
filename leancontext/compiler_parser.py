"""Conservative text compiler diagnostics with exact, recallable block evidence.

This recognizes common human-readable Clang/GCC, TypeScript/MSVC, and rustc
formats. It is deliberately not a compiler or a JSON/SARIF parser. Source search
results can literally contain the same text; callers must prefer source-command
hints over this recognizer. A recognized inventory describes observed output,
never errors suppressed by the compiler or a capture limit.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from .diagnostic_types import Diagnostic, DiagnosticReport


_ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))')
_COLON = re.compile(
    r'^(?P<location>[^\r\n]+?:\d+(?::\d+)?)\s*(?::\s*| - )'
    r'(?P<severity>fatal error|error|warning|note)'
    r'(?: (?P<code>[A-Z]{1,6}\d+))?:\s*(?P<message>.*)$')
_PAREN = re.compile(
    r'^(?P<location>[^\r\n]+\(\d+(?:,\d+)?\))\s*:\s*'
    r'(?P<severity>fatal error|error|warning|note)'
    r'(?: (?P<code>[A-Z]{1,6}\d+))?:\s*(?P<message>.*)$')
_GLOBAL_CODE = re.compile(
    r'^(?P<severity>error|warning) (?P<code>TS\d+):\s*(?P<message>.*)$')
_RUST_CODE = re.compile(
    r'^(?P<severity>error|warning)\[(?P<code>[A-Za-z_][A-Za-z_0-9]*)\]:\s*(?P<message>.*)$')
_UNLOCATED = re.compile(r'^(?P<severity>fatal error|error|warning):\s*(?P<message>.*)$')
_ARROW = re.compile(r'^\s*(?:-->|:::)\s+(?P<location>.+:\d+:\d+)\s*$')
_LIMIT = re.compile(r'^(?:fatal )?error: (?:too many errors emitted|aborting due to \d+ previous errors?)\b')
_SUMMARY = re.compile(
    r'^(?:\d+ (?:warnings?|errors?)(?: and \d+ (?:warnings?|errors?))? generated\.'
    r'|Found \d+ errors?\b|error: could not compile\b|error: aborting due to\b'
    r'|Some errors have detailed explanations:|For more information about this error,'
    r'|(?:BUILD|Build) (?:FAILED|failed|SUCCEEDED|succeeded)\b)')
_BOUNDARY = re.compile(
    r'^(?:\[\s*\d+/\d+\]|\s*(?:Compiling|Checking|Finished)\s+|'
    r'ninja: build stopped:|(?:g?make)(?:\[\d+\])?: \*\*\*|STDOUT:|STDERR:)')
_PREFIX = re.compile(
    r'^(?:In file included from |\s+from .+:\d+|.+: In (?:function|member function|'
    r'instantiation|substitution|lambda function|constructor|destructor)\b|'
    r'.+:\d+(?::\d+)?:\s+(?:required from|instantiated from)\b)')


@dataclass(frozen=True)
class _Heading:
    line: int
    severity: str
    label: str
    message: str


def _heading(line: str, index: int) -> _Heading | None:
    for pattern in (_COLON, _PAREN, _GLOBAL_CODE, _RUST_CODE):
        match = pattern.match(line)
        if match:
            values = match.groupdict()
            location, code = values.get('location'), values.get('code')
            label = location or code or 'unlocated compiler diagnostic'
            if location and code:
                label += ' [' + code + ']'
            return _Heading(index, values['severity'], label, values['message'])
    return None


def parse_compiler(text: str) -> DiagnosticReport | None:
    """Return recognized error blocks, or None when semantic parsing is unsafe.

    Warning-only reports fall back to the generic summarizer. When errors exist,
    warning blocks are counted and left in the raw artifact; notes following an
    error remain in its whole block. Signatures hash exact ANSI-free blocks.
    """
    raw_lines = text.splitlines(keepends=True)
    lines = [_ANSI.sub('', line).rstrip('\r\n') for line in raw_lines]
    recognized = [_heading(line, i) for i, line in enumerate(lines)]
    credible = any(h is not None and h.severity != 'note' for h in recognized)
    # rustc has uncoded errors too. A following source arrow provides structural
    # evidence; prose starting "error:" on its own is not enough.
    for i, line in enumerate(lines):
        if recognized[i] is not None or _SUMMARY.match(line):
            continue
        match = _UNLOCATED.match(line)
        if not match:
            continue
        arrow = None
        for j in range(i + 1, min(i + 5, len(lines))):
            if recognized[j] is not None or _UNLOCATED.match(lines[j]) or _BOUNDARY.match(lines[j]):
                break
            candidate = _ARROW.match(lines[j])
            if candidate:
                arrow = candidate.group('location')
                break
        if credible or arrow or _LIMIT.match(line):
            recognized[i] = _Heading(i, match['severity'], arrow or 'unlocated compiler diagnostic', match['message'])

    primary = [h for h in recognized if h is not None and h.severity != 'note']
    if not any('error' in h.severity for h in primary):
        return None

    summaries = [line for line in lines if _SUMMARY.match(line)]
    diagnostics: list[Diagnostic] = []
    warnings = 0
    previous_end = 0
    for position, head in enumerate(primary):
        next_line = primary[position + 1].line if position + 1 < len(primary) else len(lines)
        end = next_line
        for j in range(head.line + 1, next_line):
            if _BOUNDARY.match(lines[j]) or _SUMMARY.match(lines[j]) or _PREFIX.match(lines[j]):
                end = j
                break
        # Delimiter whitespace is not part of a diagnostic block. Preserve all
        # interior whitespace, source, carets, notes, and unexplained continuations.
        while end > head.line + 1 and not lines[end - 1].strip():
            end -= 1
        start = head.line
        while start > previous_end and _PREFIX.match(lines[start - 1]):
            start -= 1
        previous_end = end
        if head.severity == 'warning':
            warnings += 1
            continue
        detail = ''.join(raw_lines[start:end])
        label = head.label
        if label == 'unlocated compiler diagnostic' or label.startswith('E') and label[1:].isdigit():
            arrow = next((_ARROW.match(s) for s in lines[head.line + 1:end] if _ARROW.match(s)), None)
            if arrow:
                label = arrow['location'] + (f' [{head.label}]' if head.label != 'unlocated compiler diagnostic' else '')
        signature = hashlib.sha256(_ANSI.sub('', detail).encode('utf-8')).hexdigest()
        diagnostics.append(Diagnostic(
            id=f'D{len(diagnostics) + 1}', label=label, status='ERROR',
            detail=detail, traceback=detail, message=head.message, phase='compile',
            signature=signature, start_line=start + 1, end_line=end))

    notices = [
        'Inventory covers recognized compiler errors observed in captured output; '
        'compiler error limits and unsupported formats may hide additional errors.'
    ]
    if warnings:
        notices.append(f'{warnings} warning blocks omitted from this error report; raw output retains them.')
    summary = f'Observed compiler errors: {len(diagnostics)}.'
    if summaries:
        summary += '\n' + '\n'.join(dict.fromkeys(summaries))
    return DiagnosticReport(strategy='compiler', summary=summary,
                            diagnostics=tuple(diagnostics), notices=tuple(notices),
                            inventory_complete=None, expected_count=None)
