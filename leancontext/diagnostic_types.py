"""Parser contracts; no I/O, model calls, or optional runtime dependencies."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Diagnostic:
    id: str
    label: str
    status: str
    detail: str
    traceback: str = ''
    message: str = ''
    phase: str = 'unknown'
    signature: str | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass(frozen=True)
class DiagnosticReport:
    strategy: str
    summary: str
    diagnostics: tuple[Diagnostic, ...]
    notices: tuple[str, ...] = ()
    inventory_complete: bool | None = None
    expected_count: int | None = None
