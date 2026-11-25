"""Conservative Claude PostToolUse result filtering; never a permission hook."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import shlex

from .compact import compact
from .store import Store


def _noisy_command(command: str) -> bool:
    """Recognize direct noisy invocations, not shell programs or source readers."""
    if (not command or "leancontext" in command.casefold()
            or any(c in command for c in ("\n", "\r", "`", "$"))):
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        return False
    if not words or any(token and all(c in ";&|<>()" for c in token) for token in words):
        return False
    executable = Path(words[0]).name
    args = words[1:]
    if executable in {"pytest", "py.test"}:
        return True
    if re.fullmatch(r"python(?:3(?:\.\d+)?)?", executable):
        return len(args) >= 2 and args[:2] in (["-m", "pytest"], ["-m", "unittest"])
    if executable in {"npm", "pnpm", "yarn"}:
        if args and args[0] == "run":
            args = args[1:]
        return bool(args) and args[0] in {"test", "build", "lint", "check", "typecheck"}
    if executable in {"cargo", "go"}:
        return bool(args) and args[0] in {"test", "build", "check"}
    if executable == "git":
        if args and args[0] == "status":
            return True
        summary_flag = re.compile(
            r"(?:--oneline|--all|--graph|--no-decorate|--decorate(?:=(?:short|full|auto|no))?"
            r"|--max-count=\d+|-\d+|-n)"
        )
        return (bool(args) and args[0] == "log" and "--oneline" in args
                and all(not a.startswith("-") or summary_flag.fullmatch(a) for a in args[1:]))
    if executable == "docker":
        return bool(args) and args[0] == "logs"
    if executable == "journalctl":
        return True
    if executable == "tail":
        files = [a for a in args if not a.startswith("-") and not a.isdigit()]
        return bool(files) and all(
            re.search(r"\.log(?:\.\d+)?$", path, flags=re.IGNORECASE)
            or "logs" in Path(path).parts for path in files
        )
    return False


def handle_claude_hook(payload: dict, *, max_chars: int = 6000,
                       state_dir: str | Path | None = None) -> dict:
    """Return a supported result replacement, or {} to preserve host behavior.

    The archive is the complete JSON value received here, not a promise that
    Claude supplied untruncated process output. Bash stdout already interleaves
    the process streams; its stderr field contains host notices and is preserved.
    """
    if (not isinstance(payload, dict) or type(max_chars) is not int or max_chars < 640
            or payload.get("hook_event_name") != "PostToolUse"
            or payload.get("tool_name") != "Bash"):
        return {}
    tool_input = payload.get("tool_input")
    response = payload.get("tool_response")
    cwd = payload.get("cwd")
    if (not isinstance(tool_input, dict) or not isinstance(response, dict)
            or not isinstance(cwd, str) or not cwd):
        return {}
    command = tool_input.get("command")
    stdout = response.get("stdout")
    if (not isinstance(command, str) or not _noisy_command(command)
            or not isinstance(stdout, str) or len(stdout) <= max_chars
            or not isinstance(response.get("stderr"), str)
            or response.get("interrupted") is not False
            or response.get("isImage", False) is not False
            or response.get("backgroundTaskId")
            or response.get("is_error")
            or response.get("error")
            or payload.get("error")):
        return {}
    for key in ("exit_code", "exitCode", "returncode", "returnCode"):
        if key in response and response[key] not in (None, 0):
            return {}
    try:
        # Serialize before writing so malformed/non-JSON input causes no changes.
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":"))
        store = Store(cwd, state_dir=state_dir)
        raw_ref = store.save(raw, kind="claude-hook-json", metadata={
            "tool_use_id": payload.get("tool_use_id"),
            "session_id": payload.get("session_id"),
            "complete_received_payload": True,
        })
        notice = f"\n[Raw Claude hook JSON: {raw_ref}]\n"
        view = compact(stdout, store=store, max_chars=max_chars - len(notice),
                       kind="claude-bash-output", metadata={"raw_hook_ref": raw_ref})
        if not view.changed:
            return {}
        text = view.text + notice
        if len(text) > max_chars or len(text) >= len(stdout):
            return {}
        replacement = deepcopy(response)
        replacement["stdout"] = text
        store.record({
            "operation": "claude_hook",
            "raw_hook_ref": raw_ref,
            "ref": view.ref,
            "original_chars": len(stdout),
            "emitted_chars": len(text),
            "original_bytes": len(stdout.encode("utf-8")),
            "emitted_bytes": len(text.encode("utf-8")),
        })
        return {"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "updatedToolOutput": replacement,
        }}
    except Exception:
        # Compression is optional. Never withhold the original on any failure.
        return {}
