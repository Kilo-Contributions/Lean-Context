"""Capture commands once, outside the language model, with bounded resources."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time

from .compact import compact
from .store import Store


@dataclass(frozen=True)
class RunResult:
    text: str
    exit_code: int
    stdout_ref: str
    stderr_ref: str
    result_ref: str
    elapsed_seconds: float
    timed_out: bool
    output_limited: bool
    stdout_bytes: int
    stderr_bytes: int
    original_chars: int
    emitted_chars: int


def _kill_group(process: subprocess.Popen) -> None:
    try:
        if os.name == 'posix':
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass


def run_command(argv: list[str], *, store: Store, cwd: str | Path | None = None,
                timeout: float = 120, max_chars: int = 6000,
                max_output_bytes: int = 64 * 1024 * 1024) -> RunResult:
    if not argv or any(not isinstance(a, str) or '\x00' in a for a in argv):
        raise ValueError('argv must contain non-NUL strings')
    if not 0 < timeout <= 86400:
        raise ValueError('timeout must be greater than zero and at most 86400 seconds')
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1200:
        raise ValueError('Command max_chars must be at least 1200')
    if not 1 <= max_output_bytes <= 1024 * 1024 * 1024:
        raise ValueError('Output cap must be between 1 byte and 1 GiB')
    working = Path(cwd or store.root).resolve()
    start = time.monotonic()
    limited = threading.Event()
    lock = threading.Lock()
    total = [0]
    errors: list[BaseException] = []
    timed_out = False
    with tempfile.TemporaryDirectory(prefix='capture-', dir=store.base) as tmp:
        paths = [Path(tmp) / 'stdout', Path(tmp) / 'stderr']
        process = subprocess.Popen(argv, cwd=working, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=(os.name == 'posix'))

        def drain(stream, path):
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as f:
                    while True:
                        chunk = stream.read(65536)
                        if not chunk:
                            break
                        with lock:
                            left = max_output_bytes - total[0]
                            keep = chunk[:max(0, left)]
                            total[0] += len(keep)
                            if len(chunk) > left:
                                limited.set()
                        f.write(keep)
                        if limited.is_set():
                            break
            except BaseException as exc:
                errors.append(exc)
                limited.set()
            finally:
                stream.close()

        threads = [threading.Thread(target=drain, args=(s, p), daemon=True)
                   for s, p in zip((process.stdout, process.stderr), paths)]
        for t in threads:
            t.start()
        try:
            while True:
                if limited.is_set():
                    _kill_group(process)
                    break
                done = process.poll() is not None and not any(t.is_alive() for t in threads)
                if done:
                    # A drainer may set the limit between the first check and
                    # finishing. Kill descendants even after their leader exits.
                    if limited.is_set():
                        _kill_group(process)
                    break
                if time.monotonic()-start >= timeout:
                    timed_out = True
                    _kill_group(process)
                    break
                time.sleep(0.01)
            process.wait(timeout=3)
            for t in threads:
                t.join(timeout=3)
            if any(t.is_alive() for t in threads):
                _kill_group(process)
                raise RuntimeError('Output pipe remained open after process termination')
        except BaseException:
            _kill_group(process)
            process.wait()
            for t in threads:
                t.join(timeout=3)
            raise
        if errors:
            raise OSError('Failed to capture command output') from errors[0]
        stdout, stderr = (p.read_bytes() for p in paths)
    elapsed = time.monotonic()-start
    exit_code = 124 if timed_out else 125 if limited.is_set() else process.returncode
    if exit_code < 0:
        exit_code = 128 - exit_code
    meta = {'argv': argv, 'cwd': str(working), 'exit_code': exit_code,
            'timed_out': timed_out, 'output_limited': limited.is_set(),
            'elapsed_seconds': round(elapsed, 4)}
    out_ref = store.save_bytes(stdout, 'stdout', meta)
    err_ref = store.save_bytes(stderr, 'stderr', meta)
    out_text = stdout.decode('utf-8', errors='backslashreplace')
    err_text = stderr.decode('utf-8', errors='backslashreplace')
    body = ('STDERR:\n' + err_text if stderr else '') + ('\nSTDOUT:\n' + out_text if stdout else '')
    raw_ref = store.save(body, 'command-result', {**meta, 'stdout_ref': out_ref, 'stderr_ref': err_ref})
    state = 'timeout; INCOMPLETE capture' if timed_out else 'output cap; INCOMPLETE capture' if limited.is_set() else 'completed'
    header = f'[LeanContext exit={exit_code}; {state}; {elapsed:.3f}s]\n'
    footer = f'\nRaw stdout={out_ref} stderr={err_ref}\n'
    view = compact(body, store=store, max_chars=max_chars-len(header)-len(footer),
                   kind='command-result', metadata=meta)
    result_text = header + view.text + footer
    # This local ledger includes preview/reference overhead and retrieval events.
    store.record({'operation': 'run', 'original_chars': len(body),
                  'emitted_chars': len(result_text), 'original_bytes': len(stdout)+len(stderr),
                  'emitted_bytes': len(result_text.encode('utf-8')),
                  'exit_code': exit_code, 'result_ref': raw_ref,
                  'elapsed_seconds': elapsed})
    return RunResult(result_text, exit_code, out_ref, err_ref, raw_ref, elapsed,
                     timed_out, limited.is_set(), len(stdout), len(stderr),
                     len(body), len(result_text))


def run_batch(commands: list[list[str]], *, store: Store, jobs: int = 1,
              cwd: str | Path | None = None, timeout: float = 120,
              max_chars: int = 6000, max_output_bytes: int = 64*1024*1024) -> list[RunResult]:
    if not 1 <= jobs <= 8 or not 1 <= len(commands) <= 16:
        raise ValueError('Use 1–8 workers and 1–16 commands; parallel jobs must be independent')
    def one(command):
        return run_command(command, store=store, cwd=cwd, timeout=timeout,
                           max_chars=max_chars, max_output_bytes=max_output_bytes)
    if jobs == 1:
        return [one(c) for c in commands]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(one, commands))
