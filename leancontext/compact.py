"""Deterministic, extractive result views with explicit omissions and raw recall."""
from __future__ import annotations

from dataclasses import dataclass
import re

from .store import Store

ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))')
DIAGNOSTIC = re.compile(
    r'(?i)(\b(?:error|failed|failure|fatal|exception|traceback|panic|warning|warn|mismatch|'
    r'assertionerror|typeerror|valueerror|syntaxerror|keyerror|segmentation|timeout|timed out)\b'
    r'|^\s*E\s+|^\s*FAIL(?:\s|:)|^\s*error\[|^\s*at\s+.*:\d+)')
SUMMARY = re.compile(r'(?i)(\b\d+\s+(?:passed|failed|errors?|warnings?|tests?|skipped)\b|'
                     r'\b(?:test result:|tests run:|total:|build succeeded|build failed)\b)')
SEVERE = re.compile(r'(?i)(\b(?:error|failed|failure|fatal|exception|traceback|panic|mismatch|'
                    r'assertionerror|typeerror|valueerror|syntaxerror|keyerror|segmentation|timeout)\b'
                    r'|^\s*E\s+|^\s*FAIL(?:\s|:))')


@dataclass(frozen=True)
class View:
    text: str
    ref: str | None
    original_chars: int
    emitted_chars: int
    omitted_lines: int
    changed: bool


def _short_line(line: str, width: int) -> str:
    if len(line) <= width:
        return line
    spare = max(0, width - 30)
    left = spare * 2 // 3
    return line[:left] + ' ... [line excerpt] ... ' + line[-(spare-left):]


def compact(text: str, *, store: Store, max_chars: int = 6000,
            kind: str = 'output', metadata: dict | None = None) -> View:
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 512:
        raise ValueError('max_chars must be an integer of at least 512')
    if len(text) <= max_chars:
        return View(text, None, len(text), len(text), 0, False)
    # Saving succeeds before we suppress any input. Callers can fail open on I/O errors.
    ref = store.save(text, kind=kind, metadata=metadata)
    lines = ANSI.sub('', text).splitlines()
    if not lines:
        lines = ['']
    footer = (f'\nFull text: {ref}. Use the context tool to recall, or append '
              f'"recall {ref} --start 1 --lines 80" to your configured launcher '
              '(same project --root).\n')
    header = f'[LeanContext excerpt: {len(text)} chars, {len(lines)} lines; original line numbers]\n'
    available = max_chars - len(header) - len(footer) - 95
    width = max(70, min(900, available // 3))
    rendered = [f'L{i+1}: {_short_line(s, width)}\n' for i, s in enumerate(lines)]
    selected: set[int] = set()
    priorities: list[int] = []
    diagnostics = [i for i, s in enumerate(lines) if DIAGNOSTIC.search(s)]
    summaries = [i for i, s in enumerate(lines) if SUMMARY.search(s)]
    # An ocean of warnings must not crowd out a later fatal error. Alternate
    # early/late errors so a long failure list doesn't hide the final cause.
    severe = [i for i in diagnostics if SEVERE.search(lines[i])]
    for left in range((len(severe)+1)//2):
        priorities.append(severe[left])
        priorities.append(severe[-1-left])
    priorities.extend(reversed(summaries))
    for distance in (1, 2, 3):
        for i in severe:
            priorities.extend((i + distance, i - distance))
    priorities.extend(diagnostics)
    priorities.extend(range(max(0, len(lines)-8), len(lines)))
    priorities.extend(range(min(8, len(lines))))
    # Fill a little context from the beginning only if the informative set is small.
    priorities.extend(range(min(50, len(lines))))
    spent = 0
    for i in priorities:
        if i < 0 or i >= len(lines) or i in selected:
            continue
        cost = len(rendered[i]) + 32  # conservative space for a gap marker
        if spent + cost <= available:
            selected.add(i)
            spent += cost
    if not selected:
        selected.add(diagnostics[0] if diagnostics else 0)
    parts = [header]
    previous = -1
    for i in sorted(selected):
        if i > previous + 1:
            parts.append(f'... {i-previous-1} lines omitted ...\n')
        parts.append(rendered[i])
        previous = i
    if previous < len(lines)-1:
        parts.append(f'... {len(lines)-previous-1} lines omitted ...\n')
    omitted = len(lines) - len(selected)
    hidden_diag = sum(i not in selected for i in diagnostics)
    if hidden_diag:
        parts.append(f'NOTICE: {hidden_diag} diagnostic lines omitted; search/recall the artifact.\n')
    result = ''.join(parts) + footer
    if len(result) > max_chars:
        # Very small budgets and a giant line: make any hard cut explicit.
        marker = '\n[excerpt budget reached; omitted text is in artifact]\n'
        result = result[:max_chars-len(marker)-len(footer)] + marker + footer
    if len(result) >= len(text):
        return View(text, None, len(text), len(text), 0, False)
    return View(result, ref, len(text), len(result), omitted, True)
