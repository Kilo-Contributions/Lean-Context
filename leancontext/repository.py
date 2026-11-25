"""Bounded, read-only repository discovery and source-exact retrieval.

No index is built at construction. Git supplies ignore semantics when available;
the fallback walk labels its smaller, common-pattern .gitignore implementation.
Paths are relative to the configured root. Symlinks are never followed.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, field
import fnmatch
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import selectors
import stat
import subprocess
import time


MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SCAN_FILES = 5000
MAX_SCAN_BYTES = 16 * 1024 * 1024
OUTPUT_BUDGET = 12000
GIT_TIMEOUT = 2.0
SCAN_TIMEOUT = 3.0
MAX_DISCOVERY_BYTES = 8 * 1024 * 1024
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "vendor",
    "build", "dist", "target", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".next", ".nuxt", "coverage", ".cache",
    ".ssh", ".aws", ".azure", ".gcp", ".kube", ".docker", ".leancontext",
})
_SKIP_NAMES = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx",
               "id_rsa*", "id_ed25519*", "credentials.json", "credentials.yaml",
               "credentials.yml", "secrets.*", "*.min.js", "*.min.css", "*.map",
               "*.pyc", "*.pyo", "*.lock", "package-lock.json", "pnpm-lock.yaml",
               ".npmrc", ".netrc", ".pypirc", ".envrc", "*.tfstate", "*.tfstate.*",
               "kubeconfig", ".DS_Store")


class _Excluded(ValueError):
    def __init__(self, reason: str, consumed: int = 0):
        super().__init__(reason)
        self.reason = reason
        self.consumed = consumed


@dataclass
class _Inventory:
    paths: list[str] = field(default_factory=list)
    source: str = "walk"
    enumerated: int = 0
    limited: bool = False
    reason: str | None = None
    excluded: Counter = field(default_factory=Counter)


def _integer(value, name, *, minimum=1, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return value


def _lines(text):
    result = text.split("\n")
    if result and result[-1] == "":
        result.pop()
    return [line.removesuffix("\r") for line in result]


def _matches_glob(path, pattern):
    return (fnmatch.fnmatchcase(path, pattern)
            or (pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:])))


class Repository:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Repository root must be a directory")

    @staticmethod
    def _relative(path):
        if not isinstance(path, str) or not path or "\\" in path:
            raise ValueError("Use a nonempty relative POSIX repository path")
        if (PurePosixPath(path).is_absolute() or PureWindowsPath(path).drive
                or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in path)
                or ".." in PurePosixPath(path).parts):
            raise ValueError("Path must stay inside the repository root")
        result = PurePosixPath(path).as_posix()
        if result == ".":
            raise ValueError("A file path is required")
        return result

    @staticmethod
    def _glob(pattern):
        if not isinstance(pattern, str) or not pattern or len(pattern) > 512:
            raise ValueError("glob must be a nonempty string of at most 512 characters")
        Repository._relative(pattern)
        return pattern

    @staticmethod
    def _default_excluded(path):
        parts = PurePosixPath(path).parts
        if any(part.lower() in _SKIP_DIRS for part in parts):
            return True
        return any(fnmatch.fnmatchcase(parts[-1].lower(), p.lower()) for p in _SKIP_NAMES)

    def _read_bytes(self, path, *, remaining=MAX_FILE_BYTES):
        """Open each path component without following symlinks on POSIX."""
        path = self._relative(path)
        if self._default_excluded(path):
            raise _Excluded("default_exclusion")
        descriptors = []
        try:
            if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                directory = os.open(self.root, flags)
                descriptors.append(directory)
                parts = PurePosixPath(path).parts
                for part in parts[:-1]:
                    directory = os.open(part, flags, dir_fd=directory)
                    descriptors.append(directory)
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
            else:
                candidate = self.root
                for part in PurePosixPath(path).parts:
                    candidate = candidate / part
                    if candidate.is_symlink():
                        raise _Excluded("symlink")
                candidate.resolve(strict=True).relative_to(self.root)
                fd = os.open(candidate, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            descriptors.append(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise _Excluded("not_regular_file")
            if info.st_size > MAX_FILE_BYTES:
                raise _Excluded("oversize")
            if info.st_size > remaining:
                raise _Excluded("scan_byte_limit")
            cap = min(MAX_FILE_BYTES, remaining)
            chunks = []
            size = 0
            while size <= cap:
                chunk = os.read(fd, min(65536, cap + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            if size > cap:
                raise _Excluded("oversize" if cap == MAX_FILE_BYTES else "scan_byte_limit", size)
            return b"".join(chunks)
        except FileNotFoundError:
            raise
        except _Excluded:
            raise
        except (OSError, ValueError):
            raise _Excluded("unsafe_or_unreadable") from None
        finally:
            for fd in reversed(descriptors):
                os.close(fd)

    def _text(self, path, *, remaining=MAX_FILE_BYTES):
        data = self._read_bytes(path, remaining=remaining)
        if any(b < 32 and b not in (9, 10, 12, 13) for b in data):
            raise _Excluded("binary", len(data))
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise _Excluded("binary", len(data)) from None
        return text, len(data), hashlib.sha256(data).hexdigest()

    def _ignore_rules(self, directory):
        path = f"{directory}/.gitignore" if directory else ".gitignore"
        try:
            raw = self._read_bytes(path, remaining=65536).decode("utf-8")
        except FileNotFoundError:
            return []
        except (OSError, ValueError, UnicodeDecodeError):
            raise _Excluded("fallback ignore rules unavailable or over 64 KiB") from None
        rules = []
        for line in raw.splitlines():
            line = line.rstrip()
            if not line or line.startswith("#"):
                continue
            negative = line.startswith("!")
            if negative:
                line = line[1:]
            if line.startswith((r"\#", r"\!")):
                line = line[1:]
            if line:
                rules.append((directory, line.lstrip("/"), negative,
                              line.endswith("/"), line.startswith("/")))
        return rules

    @staticmethod
    def _ignored(path, rules, *, directory=False):
        ignored = False
        for base, pattern, negative, directory_only, anchored in rules:
            prefix = base + "/" if base else ""
            if not path.startswith(prefix):
                continue
            relative = path[len(prefix):]
            pattern = pattern.rstrip("/")
            if "/" in pattern or anchored:
                matches = fnmatch.fnmatchcase(relative, pattern)
                if directory_only:
                    matches = matches and directory or relative.startswith(pattern + "/")
            else:
                pieces = relative.split("/")
                if directory_only and not directory:
                    pieces = pieces[:-1]
                matches = any(fnmatch.fnmatchcase(piece, pattern) for piece in pieces)
            if matches:
                ignored = not negative
        return ignored

    def _is_ignored(self, path):
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), "check-ignore", "--quiet", "--", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=GIT_TIMEOUT, check=False)
            if result.returncode in (0, 1):
                return result.returncode == 0
        except subprocess.TimeoutExpired:
            raise ValueError("Git ignore check timed out; file was not read") from None
        except OSError:
            pass
        rules = []
        parts = PurePosixPath(path).parts
        for n in range(len(parts)):
            directory = "/".join(parts[:n])
            rules.extend(self._ignore_rules(directory))
            if n and self._ignored(directory, rules, directory=True):
                return True
        return self._ignored(path, rules)

    def _git_inventory(self):
        try:
            process = subprocess.Popen(
                ["git", "-C", str(self.root), "ls-files", "-z", "--cached",
                 "--others", "--exclude-standard"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            return None
        inventory = _Inventory(source="git")
        buffer = b""
        received = 0
        deadline = time.monotonic() + GIT_TIMEOUT
        selector = selectors.DefaultSelector()
        try:
            try:
                selector.register(process.stdout, selectors.EVENT_READ)
            except (OSError, ValueError):
                return None
            done = False
            while not done:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    inventory.limited, inventory.reason = True, "git discovery timeout"
                    break
                if not selector.select(remaining):
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                received += len(chunk)
                buffer += chunk
                while b"\0" in buffer:
                    item, buffer = buffer.split(b"\0", 1)
                    if inventory.enumerated >= MAX_SCAN_FILES:
                        inventory.limited, inventory.reason = True, "5000-entry discovery limit"
                        done = True
                        break
                    inventory.enumerated += 1
                    try:
                        inventory.paths.append(self._relative(os.fsdecode(item)))
                    except ValueError:
                        inventory.excluded["unsafe_path"] += 1
                if received >= MAX_DISCOVERY_BYTES:
                    inventory.limited, inventory.reason = True, "discovery byte limit"
                    break
            if not inventory.limited:
                try:
                    if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
                        return None
                except subprocess.TimeoutExpired:
                    inventory.limited, inventory.reason = True, "git discovery timeout"
            inventory.paths = sorted(set(inventory.paths))
            return inventory
        finally:
            selector.close()
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()

    def _walk_inventory(self):
        inventory = _Inventory()
        pending = [("", [])]
        deadline = time.monotonic() + SCAN_TIMEOUT
        while pending and not inventory.limited:
            relative, inherited = pending.pop()
            try:
                rules = inherited + self._ignore_rules(relative)
            except _Excluded as error:
                inventory.limited, inventory.reason = True, error.reason
                inventory.excluded["ignore_rules_unavailable"] += 1
                break
            try:
                with os.scandir(self.root / relative) as entries:
                    for entry in entries:
                        if inventory.enumerated >= MAX_SCAN_FILES:
                            inventory.limited, inventory.reason = True, "5000-entry discovery limit"
                            break
                        if time.monotonic() >= deadline:
                            inventory.limited, inventory.reason = True, "walk discovery timeout"
                            break
                        inventory.enumerated += 1
                        name = f"{relative}/{entry.name}" if relative else entry.name
                        try:
                            name = self._relative(name)
                            is_dir = entry.is_dir(follow_symlinks=False)
                            if entry.is_symlink():
                                inventory.excluded["symlink"] += 1
                            elif self._default_excluded(name):
                                inventory.excluded["default_exclusion"] += 1
                            elif self._ignored(name, rules, directory=is_dir):
                                inventory.excluded["gitignore"] += 1
                            elif is_dir:
                                pending.append((name, rules))
                            elif entry.is_file(follow_symlinks=False):
                                inventory.paths.append(name)
                            else:
                                inventory.excluded["not_regular_file"] += 1
                        except (OSError, ValueError):
                            inventory.excluded["unsafe_or_unreadable"] += 1
            except OSError:
                inventory.excluded["unreadable_directory"] += 1
        inventory.paths.sort()
        return inventory

    def _inventory(self):
        result = self._git_inventory()
        return result if result is not None else self._walk_inventory()

    @staticmethod
    def _scope(inventory):
        return {
            "source": inventory.source,
            "ignore_mode": ("git ls-files --cached --others --exclude-standard"
                            if inventory.source == "git" else
                            "fallback: common .gitignore patterns; not full Git semantics"),
            "scope": "repository-relative UTF-8 text; default secret/generated exclusions",
            "enumerated_entries": inventory.enumerated,
            "scanned_files": 0,
            "scanned_bytes": 0,
            "scan_file_limit": MAX_SCAN_FILES,
            "file_size_limit_bytes": MAX_FILE_BYTES,
            "scan_byte_limit": MAX_SCAN_BYTES,
            "output_budget_chars": OUTPUT_BUDGET,
            "excluded": dict(inventory.excluded),
            "scan_limited": inventory.limited,
            "limit_reason": inventory.reason,
        }

    def _texts(self, inventory, glob, scope):
        deadline = time.monotonic() + SCAN_TIMEOUT
        for path in inventory.paths:
            if not _matches_glob(path, glob):
                continue
            if time.monotonic() >= deadline:
                scope.update(scan_limited=True, limit_reason="content scan time limit")
                break
            if scope["scanned_files"] >= MAX_SCAN_FILES:
                scope.update(scan_limited=True, limit_reason="5000-file scan limit")
                break
            scope["scanned_files"] += 1
            try:
                text, size, digest = self._text(path, remaining=MAX_SCAN_BYTES - scope["scanned_bytes"])
            except FileNotFoundError:
                reason, consumed = "missing", 0
            except _Excluded as error:
                reason, consumed = error.reason, error.consumed
                if reason == "scan_byte_limit":
                    scope.update(scan_limited=True, limit_reason="content scan byte limit")
                    break
            else:
                scope["scanned_bytes"] += size
                yield path, text, digest
                continue
            scope["scanned_bytes"] += consumed
            scope["excluded"][reason] = scope["excluded"].get(reason, 0) + 1

    @staticmethod
    def _finish(scope, *, output_limited=False, result_limited=False):
        scope["truncated"] = bool(scope["scan_limited"] or output_limited or result_limited)
        scope["complete"] = not scope["truncated"]
        scope["has_more"] = scope["truncated"]
        scope["output_limited"] = output_limited
        scope["result_limited"] = result_limited
        status = "Partial" if scope["truncated"] else "Complete for eligible text in the scanned scope"
        reasons = [r for r in (scope.get("limit_reason"),
                              "output budget" if output_limited else None,
                              "result limit" if result_limited else None) if r]
        exclusions = ", ".join(f"{k}={v}" for k, v in sorted(scope["excluded"].items())) or "none observed"
        return (f"\n[{status}; scanned {scope['scanned_files']} files / {scope['scanned_bytes']} bytes; "
                f"excluded: {exclusions}; limits: {', '.join(reasons) or 'not reached'}. "
                "Narrow glob/query or increase result limit to refine.]\n")

    def files(self, glob="*", limit=200):
        glob = self._glob(glob)
        _integer(limit, "limit", maximum=MAX_SCAN_FILES)
        inventory = self._inventory()
        scope = self._scope(inventory)
        paths = []
        size = 0
        output_limited = result_limited = False
        for path, _text, _digest in self._texts(inventory, glob, scope):
            if len(paths) >= limit:
                result_limited = True
                break
            if size + len(path) + 1 > OUTPUT_BUDGET - 800:
                output_limited = True
                break
            paths.append(path)
            size += len(path) + 1
        footer = self._finish(scope, output_limited=output_limited, result_limited=result_limited)
        body = "\n".join(paths) or "No eligible text files in the scanned scope."
        scope.update(paths=paths, shown=len(paths), glob=glob,
                     text=f"Files ({inventory.source}; glob {glob}):\n{body}{footer}")
        return scope

    def search(self, query: str, glob="*", limit=20, context=2):
        if not isinstance(query, str) or not query or len(query) > 4096 or "\n" in query or "\r" in query:
            raise ValueError("query must be a nonempty literal single-line string of at most 4096 characters")
        glob = self._glob(glob)
        _integer(limit, "limit", maximum=MAX_SCAN_FILES)
        _integer(context, "context", minimum=0, maximum=20)
        inventory = self._inventory()
        scope = self._scope(inventory)
        matches, rendered = [], []
        rendered_size = 0
        omitted = 0
        result_limited = output_limited = False
        for path, text, _digest in self._texts(inventory, glob, scope):
            lines = _lines(text)
            emitted = set()
            for index, line in enumerate(lines):
                if query not in line:
                    continue
                if len(matches) >= limit:
                    result_limited = True
                    break
                matches.append({"path": path, "line": index + 1})
                selected = [n for n in range(max(0, index-context), min(len(lines), index+context+1)) if n not in emitted]
                for offset, n in enumerate(selected):
                    label = f"{path}:{n+1}: {lines[n]}\n"
                    if rendered_size + len(label) > OUTPUT_BUDGET - 800:
                        omitted += len(selected) - offset
                        output_limited = True
                        break
                    rendered.append(label)
                    rendered_size += len(label)
                    emitted.add(n)
                if output_limited:
                    break
            if result_limited or output_limited:
                break
        footer = self._finish(scope, output_limited=output_limited, result_limited=result_limited)
        body = "".join(rendered) or ("Matching lines did not fit the output budget.\n" if matches else "No literal matches in the scanned scope.\n")
        scope.update(matches=matches, match_count=len(matches), omitted_lines=omitted,
                     query=query, glob=glob,
                     text=f"Literal search ({inventory.source}; glob {glob}):\n{body}{footer}")
        return scope

    def _source(self, path):
        path = self._relative(path)
        if self._default_excluded(path):
            raise ValueError("File is excluded by the default secret/generated-path policy")
        if self._is_ignored(path):
            raise ValueError("File is excluded by repository ignore rules")
        try:
            text, size, digest = self._text(path)
        except _Excluded as error:
            raise ValueError(f"Cannot read repository file: {error.reason}") from None
        return path, _lines(text), size, digest

    def read(self, path: str, start=1, end=None, max_chars=12000):
        _integer(start, "start")
        _integer(max_chars, "max_chars")
        if end is not None:
            _integer(end, "end", minimum=start)
        path, lines, size, digest = self._source(path)
        last = min(len(lines), len(lines) if end is None else end)
        header = f"Read {path}; requested lines {start}-{last}; total {len(lines)}.\n"
        chosen = []
        chosen_size = 0
        next_start = None
        required = None
        for n in range(start, last+1):
            record = f"{path}:{n}: {lines[n-1]}\n"
            reserve = len("[End of requested range.]\n" if n == last else
                          f"[Partial: output budget; next_start={n+1}. Increase max_chars if a whole line will not fit.]\n")
            if len(header) + chosen_size + len(record) + reserve > max_chars:
                next_start = n
                required = len(header) + len(record) + reserve
                break
            chosen.append(record)
            chosen_size += len(record)
        truncated = next_start is not None
        footer = (f"[Partial: output budget; next_start={next_start}. Increase max_chars if a whole line will not fit.]\n"
                  if truncated else "[End of requested range.]\n")
        text = header + "".join(chosen) + footer
        if len(text) > max_chars:
            text = ("[Output budget too small; increase max_chars.]\n")[:max_chars]
        return {"text": text, "path": path, "start": start, "end": start+len(chosen)-1,
                "requested_end": last, "total_lines": len(lines), "truncated": truncated,
                "complete": not truncated, "next_start": next_start,
                "required_chars_for_next_line": required, "omitted_lines": max(0, last-start+1-len(chosen)),
                "output_budget_chars": max_chars, "file_bytes": size, "sha256": digest}

    def outline(self, path: str):
        path, lines, size, digest = self._source(path)
        method = "regex_heuristic"
        symbols = []
        if path.lower().endswith(".py"):
            try:
                tree = ast.parse("\n".join(lines))
                def visit(node, parents=()):
                    for child in ast.iter_child_nodes(node):
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            name = ".".join((*parents, child.name))
                            symbols.append({"name": name, "line": child.lineno,
                                            "end_line": child.end_lineno, "kind": type(child).__name__})
                            visit(child, (*parents, child.name))
                        else:
                            visit(child, parents)
                visit(tree)
                method = "python_ast"
            except (SyntaxError, ValueError, RecursionError, MemoryError):
                symbols = []
        if method == "regex_heuristic":
            pattern = re.compile(r"^\s*(?:(?:export|default|public|private|protected|static|async|pub)\s+)*(?:class|interface|struct|enum|trait|function|def|fn|func)\s+([A-Za-z_$][\w$]*)")
            for n, line in enumerate(lines, 1):
                match = pattern.match(line)
                if match:
                    symbols.append({"name": match.group(1), "line": n, "kind": "heuristic"})
        symbols.sort(key=lambda item: item["line"])
        header = (f"Outline {path} ({'Python AST' if method == 'python_ast' else 'regex heuristic; may miss or misidentify symbols'}):\n")
        emitted, selected, used = [], [], len(header)
        for symbol in symbols:
            record = f"{path}:{symbol['line']}: {lines[symbol['line']-1]}\n"
            if used + len(record) > OUTPUT_BUDGET - 200:
                break
            emitted.append(record)
            selected.append(symbol)
            used += len(record)
        omitted = len(symbols) - len(selected)
        footer = f"[{'Partial: output budget; ' if omitted else ''}{len(selected)} symbols shown; {omitted} omitted.]\n"
        return {"text": header + ("".join(emitted) or "No recognized symbols.\n") + footer,
                "path": path, "method": method, "symbols": selected, "omitted_symbols": omitted,
                "truncated": bool(omitted), "complete": not omitted,
                "next_start": symbols[len(selected)]["line"] if omitted else None,
                "output_budget_chars": OUTPUT_BUDGET, "file_bytes": size, "sha256": digest}
