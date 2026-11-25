"""Conservative accounting for explicitly supplied coding-agent JSONL logs.

No session discovery, credential access, network, or price estimation occurs here.
Only recognized usage records are retained in memory; message text is not copied
into a report.  Reported totals describe observed counters, not account quotas.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any


METRICS = (
    "input_tokens", "output_tokens", "cached_input_tokens",
    "cache_creation_input_tokens", "reasoning_output_tokens", "total_tokens",
)


@dataclass
class _Usage:
    harness: str
    source: str
    session: str
    provider: str
    model: str
    metrics: dict[str, int | None]
    identity: str | None
    timestamp: float | None
    path: str
    line: int


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _time(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # A timezone-free timestamp cannot safely order independently exported logs.
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _counter(data: dict, key: str, warnings: list[str], where: str,
             default: int | None = None) -> int | None:
    if key not in data:
        return default
    value = data[key]
    if _integer(value):
        return value
    warnings.append(f"{where}: invalid {key}; that counter is unknown.")
    return None


def _usage_metrics(data: dict, *, claude: bool, warnings: list[str],
                   where: str) -> dict[str, int | None] | None:
    base_input = _counter(data, "input_tokens", warnings, where)
    output = _counter(data, "output_tokens", warnings, where)
    if base_input is None or output is None:
        warnings.append(f"{where}: invalid or partial usage: input/output counters are missing.")
    if base_input is None and output is None:
        return None

    if claude:
        # Claude's three input categories are disjoint. Absent optional cache
        # fields in a recognized Messages usage object mean no such usage.
        cached = _counter(data, "cache_read_input_tokens", warnings, where, 0)
        created = _counter(data, "cache_creation_input_tokens", warnings, where, 0)
        input_tokens = (base_input + cached + created
                        if all(value is not None for value in (base_input, cached, created))
                        else None)
        # Extended thinking is included in output_tokens; its breakout is not
        # normally present in Claude's Messages usage object.
        reasoning = None
    else:
        input_tokens = base_input
        details = data.get("input_tokens_details")
        details = details if isinstance(details, dict) else {}
        output_details = data.get("output_tokens_details")
        output_details = output_details if isinstance(output_details, dict) else {}
        cached = (_counter(data, "cached_input_tokens", warnings, where)
                  if "cached_input_tokens" in data
                  else _counter(details, "cached_tokens", warnings, where))
        created = _counter(details, "cache_write_tokens", warnings, where)
        reasoning = (_counter(data, "reasoning_output_tokens", warnings, where)
                     if "reasoning_output_tokens" in data
                     else _counter(output_details, "reasoning_tokens", warnings, where))
        # OpenAI/Codex cached input and reasoning output are subsets, not extras.
        for name, value, total in (("cached_input_tokens", cached, input_tokens),
                                   ("reasoning_output_tokens", reasoning, output),
                                   ("cache_write_tokens", created, input_tokens)):
            if value is not None and total is not None and value > total:
                warnings.append(f"{where}: {name} exceeds its total; that subset is unknown.")
                if name == "cached_input_tokens":
                    cached = None
                elif name == "reasoning_output_tokens":
                    reasoning = None
                else:
                    created = None

    total = input_tokens + output if input_tokens is not None and output is not None else None
    supplied_total = data.get("total_tokens")
    if supplied_total is not None and supplied_total != total:
        warnings.append(f"{where}: total_tokens disagrees with input plus output; using the separate counters.")
    return dict(zip(METRICS, (input_tokens, output, cached, created, reasoning, total)))


def _sum(rows: list[dict[str, int | None]]) -> dict[str, int | None]:
    """An unreported category stays unknown rather than turning into zero."""
    return {
        name: sum(row[name] for row in rows)
        if rows and all(row.get(name) is not None for row in rows) else None
        for name in METRICS
    }


def _parse_file(path: Path, warnings: list[str]) -> tuple[list[_Usage], dict]:
    label = str(path)
    result = {"path": label, "status": "ok", "lines": 0, "usage_records": 0}
    rows: list[_Usage] = []
    digest = hashlib.sha256()
    # Avoid resolving again before the guarded read: explicit paths may contain
    # broken links, and one bad file must not prevent auditing the other files.
    session = f"file:{path.absolute()}"
    provider = "unknown"
    model = "unknown"
    try:
        if not path.is_file():
            result["status"] = "not_a_regular_file"
            warnings.append(f"{label}: not a regular file; no directories are scanned.")
            return rows, result
        with path.open("rb") as stream:
            for line_number, raw in enumerate(stream, 1):
                result["lines"] = line_number
                digest.update(raw)
                if not raw.strip():
                    continue
                where = f"{label}:{line_number}"
                try:
                    record = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    warnings.append(f"{where}: invalid JSON; record ignored.")
                    continue
                if not isinstance(record, dict):
                    warnings.append(f"{where}: unknown non-object JSONL record ignored.")
                    continue
                kind = record.get("type")
                payload = record.get("payload")
                payload = payload if isinstance(payload, dict) else {}
                if kind == "session_meta":
                    session = _text(payload.get("id")) or session
                    provider = _text(payload.get("model_provider")) or provider
                    model = _text(payload.get("model")) or model
                    continue
                if kind == "thread.started":
                    session = _text(record.get("thread_id")) or session
                    provider = _text(record.get("provider")) or provider
                    model = _text(record.get("model")) or model
                    continue
                if kind == "turn_context":
                    model = _text(payload.get("model")) or model
                    provider = _text(payload.get("model_provider")) or provider
                    continue

                harness = source = ""
                usage: Any = None
                identity = None
                row_session = (_text(record.get("session_id"))
                               or _text(record.get("sessionId")) or session)
                row_provider = _text(record.get("provider")) or provider
                row_model = _text(record.get("model")) or model
                if kind == "event_msg" and payload.get("type") == "token_count":
                    harness, source = "codex", "codex_cumulative"
                    info = payload.get("info")
                    if isinstance(info, dict):
                        usage = info.get("total_token_usage")
                    if not isinstance(usage, dict):
                        warnings.append(f"{where}: partial token_count without cumulative usage ignored.")
                        continue
                    row_session = _text(payload.get("session_id")) or row_session
                    row_model = _text(payload.get("model")) or row_model
                elif kind == "turn.completed":
                    harness, source = "codex", "codex_exec"
                    usage = record.get("usage")
                    identity = _text(record.get("turn_id"))
                elif kind == "assistant" and isinstance(record.get("message"), dict):
                    message = record["message"]
                    if not isinstance(message.get("usage"), dict):
                        continue
                    harness, source = "claude", "claude_message"
                    usage = message["usage"]
                    identity = _text(message.get("id"))
                    row_model = _text(message.get("model")) or row_model
                    row_provider = _text(record.get("provider")) or "anthropic"
                else:
                    message = record.get("message")
                    message = message if isinstance(message, dict) else {}
                    if any(isinstance(candidate.get("usage"), dict)
                           for candidate in (record, payload, message)):
                        warnings.append(f"{where}: unknown or alternative usage format ignored.")
                    continue
                if not isinstance(usage, dict):
                    warnings.append(f"{where}: invalid usage object ignored.")
                    continue
                metrics = _usage_metrics(usage, claude=harness == "claude",
                                         warnings=warnings, where=where)
                if metrics is None:
                    continue
                rows.append(_Usage(harness, source, row_session, row_provider,
                                   row_model, metrics, identity, _time(record.get("timestamp")),
                                   label, line_number))
        result["sha256"] = digest.hexdigest()
    except (OSError, ValueError) as exc:
        result["status"] = "read_error"
        warnings.append(f"{label}: file could not be fully read ({type(exc).__name__}).")
    result["usage_records"] = len(rows)
    return rows, result


def _ordered(rows: list[_Usage], warnings: list[str], session: str) -> list[_Usage]:
    if all(row.timestamp is not None for row in rows):
        return sorted(rows, key=lambda row: row.timestamp)
    if len({row.path for row in rows}) > 1:
        warnings.append(f"Session {session}: some timestamps are absent; supplied file order is used for untimed snapshots.")
    return rows


def _deduplicate(rows: list[_Usage], warnings: list[str], session: str) -> tuple[list[_Usage], int]:
    """Deduplicate identities or overlapping events, never equal consecutive turns."""
    identified: dict[tuple[str, str], _Usage] = {}
    anonymous: list[_Usage] = []
    seen: set[tuple] = set()
    occurrences: Counter = Counter()
    duplicates = 0
    ambiguous = False
    paths = {row.path for row in rows}
    for row in rows:
        if row.identity is not None:
            key = (row.source, row.identity)
            if key in identified:
                duplicates += 1
            # Streaming usage snapshots replace the preceding snapshot, including
            # corrections that reduce a counter. Never take independent maxima.
            identified[key] = row
            continue
        signature = (row.source, row.timestamp, tuple(row.metrics[name] for name in METRICS))
        if row.timestamp is None:
            occurrences[(row.path, signature)] += 1
            signature += (occurrences[(row.path, signature)],)
            ambiguous = len(paths) > 1
        if signature in seen:
            duplicates += 1
        else:
            seen.add(signature)
            anonymous.append(row)
    if ambiguous:
        warnings.append(f"Session {session}: overlapping untimed records lack request IDs; equal occurrences across files were deduplicated heuristically.")
    if any(row.source == "claude_message" and row.identity is None for row in rows):
        warnings.append(f"Session {session}: Claude message IDs are missing; repeated snapshots cannot be reliably distinguished from separate messages.")
    # Preserve the established chronological/encounter order after replacing snapshots.
    selected = {id(row) for row in anonymous + list(identified.values())}
    return [row for row in rows if id(row) in selected], duplicates


def _cumulative(rows: list[_Usage], warnings: list[str], session: str
                ) -> tuple[dict, list[tuple[_Usage, dict]], int]:
    epochs: list[dict] = []
    contributions: list[tuple[_Usage, dict]] = []
    previous: dict | None = None
    resets = 0
    for row in rows:
        current = row.metrics
        reset = previous is not None and any(
            current[name] is not None and previous[name] is not None
            and current[name] < previous[name]
            for name in ("input_tokens", "output_tokens")
        )
        if reset:
            epochs.append(previous)
            resets += 1
            warnings.append(f"Session {session}: cumulative counter reset at {row.path}:{row.line}; a new epoch is added, but its cause/completeness is unproven.")
        if previous is None or reset:
            delta = current.copy()
        else:
            delta = {}
            for name in METRICS:
                new, old = current[name], previous[name]
                if new is None or old is None:
                    delta[name] = None
                elif new < old:
                    delta[name] = None
                    warnings.append(f"Session {session}: cumulative {name} decreased without a full counter reset; model attribution for that category is unknown.")
                else:
                    delta[name] = new - old
        # Cache/reasoning corrections can arrive without another input/output
        # increment. Keep those changes too, including unknown attribution when
        # a subset decreases; unchanged snapshots still contribute nothing.
        if previous is None or current != previous:
            contributions.append((row, delta))
        previous = current
    if previous is not None:
        epochs.append(previous)
    # The last snapshot in each epoch is authoritative even if early snapshots
    # omitted optional cache/reasoning fields; attribution may be less complete.
    return _sum(epochs), contributions, resets


def audit_paths(paths: list[Path]) -> dict:
    """Read only the named JSONL files and return observed provider token usage.

    Session completeness cannot be established from these formats. Duplicate file
    copies, cumulative snapshots, and identified message/turn snapshots are
    deduplicated. Unknown fields or unsupported usage formats are disclosed.
    """
    warnings = [
        "Completeness is not proven: only explicitly supplied files are inspected; other sessions, subagents, failed requests or earlier records may be absent.",
        "Token counters are reported usage, not monetary cost or subscription quota. Cached input and reasoning output are subsets unless the provider's documented format says otherwise.",
    ]
    files: list[dict] = []
    all_rows: list[_Usage] = []
    seen_paths: set[Path] = set()
    seen_hashes: dict[str, str] = {}
    duplicate_files = 0
    for value in paths:
        path = Path(value)
        try:
            canonical = path.resolve()
        except (OSError, RuntimeError):
            canonical = path.absolute()
        if canonical in seen_paths:
            duplicate_files += 1
            files.append({"path": str(path), "status": "duplicate_path"})
            continue
        seen_paths.add(canonical)
        rows, file_info = _parse_file(path, warnings)
        fingerprint = file_info.get("sha256")
        if fingerprint is not None and fingerprint in seen_hashes:
            duplicate_files += 1
            file_info["status"] = "duplicate_content"
            file_info["duplicate_of"] = seen_hashes[fingerprint]
            warnings.append(f"{path}: byte-identical to {seen_hashes[fingerprint]}; counted once. Without session IDs, identical independent exports cannot be distinguished from copies.")
        else:
            if fingerprint is not None:
                seen_hashes[fingerprint] = str(path)
            all_rows.extend(rows)
        files.append(file_info)

    buckets: dict[tuple[str, str], list[_Usage]] = defaultdict(list)
    for row in all_rows:
        buckets[(row.harness, row.session)].append(row)
    sessions = []
    groups: dict[tuple[str, str], list[tuple[_Usage, dict]]] = defaultdict(list)
    duplicates = resets = counted = 0
    for (harness, session), rows in buckets.items():
        start = len(warnings)
        sources = {row.source for row in rows}
        if "codex_cumulative" in sources and len(sources) > 1:
            warnings.append(f"Session {session}: alternative per-turn usage is ignored because cumulative counters already cover the session; sources are not added together.")
            rows = [row for row in rows if row.source == "codex_cumulative"]
        unproven_cumulative_order = (
            rows and rows[0].source == "codex_cumulative"
            and len({row.path for row in rows}) > 1
            and any(row.timestamp is None for row in rows)
        )
        rows = _ordered(rows, warnings, session)
        rows, removed = _deduplicate(rows, warnings, session)
        duplicates += removed
        if unproven_cumulative_order:
            warnings.append(f"Session {session}: cross-file cumulative chronology is unproven; totals and model attribution are unknown. Supply one canonical export or timestamped records. A decrease may be stale data, not a reset.")
            totals = _sum([])
            contributions = [(row, totals.copy()) for row in rows]
        elif rows and rows[0].source == "codex_cumulative":
            totals, contributions, reset_count = _cumulative(rows, warnings, session)
            resets += reset_count
        else:
            contributions = [(row, row.metrics) for row in rows]
            totals = _sum([value for _, value in contributions])
        counted += len(contributions)
        for row, metrics in contributions:
            groups[(row.provider, row.model)].append((row, metrics))
        providers = sorted({row.provider for row in rows})
        models = sorted({row.model for row in rows})
        if session.startswith("file:"):
            warnings.append(f"{session}: no explicit session ID; distinct partial exports cannot be reliably joined.")
        if "unknown" in models or "unknown" in providers:
            warnings.append(f"Session {session}: model or provider metadata is unknown; no model identity is inferred from token counts.")
        sessions.append({
            "id": session, "harness": harness,
            "provider": providers[0] if len(providers) == 1 else "mixed",
            "models": models, "source": sorted({row.source for row in rows}),
            "totals": totals, "records": len(contributions),
            "warnings": warnings[start:],
        })
    if not sessions:
        warnings.append("No recognized usage was found; unknown totals are null, not zero.")
    group_report = []
    for (provider, model), contributions in sorted(groups.items()):
        group_report.append({
            "provider": provider, "model": model,
            **_sum([value for _, value in contributions]),
            "sessions": len({(row.harness, row.session) for row, _ in contributions}),
            "records": len(contributions),
        })
    return {
        "format": "leancontext-usage-v1",
        "complete": False,
        "scope": "recognized counters in explicitly supplied JSONL files",
        "totals": _sum([session["totals"] for session in sessions]),
        "groups": group_report,
        "sessions": sessions,
        "files": files,
        "coverage": {"recognized_usage_records": len(all_rows), "records_counted": counted,
                     "duplicate_records": duplicates, "duplicate_files": duplicate_files,
                     "counter_resets": resets},
        "warnings": list(dict.fromkeys(warnings)),
    }


def compare_reports(before: dict, after: dict) -> dict:
    """Compare observed counts only; a reduction is not proof of equivalent work."""
    warnings = [
        "This compares reported usage, not correctness, money or quota. Equivalent tasks, model settings and complete coverage must be checked independently.",
    ]
    if not before.get("complete") or not after.get("complete"):
        warnings.append("At least one report does not prove complete usage coverage; percentages apply only to its observed counters.")
    before_groups = {(row.get("provider"), row.get("model"))
                     for row in before.get("groups", []) if isinstance(row, dict)}
    after_groups = {(row.get("provider"), row.get("model"))
                    for row in after.get("groups", []) if isinstance(row, dict)}
    if before_groups != after_groups:
        warnings.append("Provider/model groups differ; the runs may not be comparable.")
    first = before.get("totals")
    first = first if isinstance(first, dict) else {}
    second = after.get("totals")
    second = second if isinstance(second, dict) else {}
    result = {}
    for name in METRICS:
        a, b = first.get(name), second.get(name)
        a = a if _integer(a) else None
        b = b if _integer(b) else None
        known = a is not None and b is not None
        result[name] = {
            "before": a, "after": b, "delta": b - a if known else None,
            "reduction_pct": 100.0 * (a - b) / a if known and a > 0 else None,
        }
    return {"format": "leancontext-comparison-v1", "metrics": result,
            "percentage_definition": "100 * (before - after) / before; positive means fewer tokens; zero baselines are undefined",
            "warnings": warnings}
