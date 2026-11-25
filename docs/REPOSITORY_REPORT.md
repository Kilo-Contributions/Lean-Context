# Repository retrieval implementation report

Implemented `leancontext/repository.py` and behavioral tests in `tests/test_repository.py` against the shared design. No MCP, CLI, installer, or other implementation files were changed by this task. No commits were made.

## Verification

The first 16 tests were written and run before the module existed; all failed with the explicit missing-implementation assertion. Three further edge tests first failed for a small fitting read, unavailable fallback ignore rules, and package-manager credential exclusions, then passed after the corresponding fixes. Two additional bounded-scan/budget cases passed against the implementation.

Fresh focused run:

```text
$ python3 -m unittest tests.test_repository -v
Ran 21 tests in 1.018s
OK
```

Fresh project-suite run while the other tasks were present:

```text
$ python3 -m unittest discover -s tests
......................................................................................
Ran 86 tests in 2.811s
OK
```

Tests use actual files, an actual Git repository for Git-ignore behavior, a 5,002-file repository, and an 18 MiB repository fixture. They cover exact Unicode lines/ranges, continuation, literal regex-shaped queries, query output limits, Python AST and labeled heuristic outlines, unknown binary input, oversized files, secrets/generated exclusions, absolute/traversing paths, symlink escapes, fallback ignore handling, and result/scan limits. No authenticated model calls or network services were used.

## Shared interface

The public signatures remain exactly as specified:

```python
Repository(root)
files(glob='*', limit=200)
search(query, glob='*', limit=20, context=2)
read(path, start=1, end=None, max_chars=12000)
outline(path)
```

Every response contains `text`. File discovery and search also expose `source`, `ignore_mode`, `scanned_files`, `scanned_bytes`, `excluded`, `scan_limited`, `limit_reason`, `truncated`, `complete`, and `has_more`. `files` includes relative `paths`; search includes `matches` with relative `path` and original `line`, without hiding full source strings in match metadata.

`read` supplies exact, complete physical source lines labeled `path:line: text`, plus `start`, `end`, `requested_end`, `total_lines`, `next_start`, `omitted_lines`, and `required_chars_for_next_line`. When one line cannot fit, it is omitted instead of silently clipped; `next_start` stays on that line and the caller can increase `max_chars`. Empty files or a start beyond EOF return no source lines. File hashes are SHA-256 of the original UTF-8 bytes. CRLF separators are rendered as normal output newlines; source line bodies are retained.

`outline` returns `method='python_ast'` for parsed Python, with nested qualified symbol names and source/end lines. Other languages and invalid Python use `method='regex_heuristic'`, clearly labeled in the text. It is a declaration heuristic, not a full parser for those languages.

Invalid arguments, ignored paths, binary/oversized input, unsafe paths, and symlinks raise `ValueError`; missing files raise `FileNotFoundError`. Explicit reads cannot bypass the default exclusions or Git-ignore policy.

## Limits and scope

- No index or Git scan runs in the constructor. Each query discovers the current filesystem state.
- Git discovery uses `git ls-files -z --cached --others --exclude-standard`, with a 2-second process timeout, 5,000-entry cap, and 8 MiB discovery-output cap. NUL framing handles ordinary whitespace in names. Control-character names are excluded to protect source-label integrity.
- Each file is capped at 2 MiB, UTF-8 text only. Search/discovery content scans are capped at 5,000 files, 16 MiB read, and a 3-second deadline checked between files. Directory discovery is also bounded. These are local filesystem bounds, not hard real-time guarantees for an unresponsive filesystem.
- Listing, search, and outline have a 12,000-character text budget. `read` has the explicit caller-selected `max_chars` budget. These budgets apply to the **text field**, not the entire JSON encoding of metadata.
- `complete` means complete for eligible text in the enumerated scope. Known binary, oversized, ignored, secret, generated, and unsafe paths are deliberately excluded. Counts describe exclusions actually observed; Git-ignored untracked paths are never enumerated, so their total is unknown.
- Git supplies full ignore behavior when available, including visibility of tracked files that now match an ignore rule. The fallback walk supports common nested `.gitignore` patterns and negation, explicitly labels its approximate semantics, and refuses to scan when an existing fallback ignore file cannot be read or exceeds 64 KiB. It does not claim full Git wildmatch semantics.
- Default exclusions include dependency/build directories, `.env` variants, private-key files, package-manager credential files, Terraform state, and other common generated/secret paths. They are convenience filters, not secret detection.
- POSIX reads open each directory/file component using descriptor-relative `O_NOFOLLOW`, reject nonregular files, and do not follow symlinks. The portability fallback checks every component and final containment before opening. No outside-root source path is accepted.

For partial searches, narrow the literal query or glob; there is no persistent search index or cross-query cursor. For partial reads, use `next_start`. The primary integration should render `text` as the main model-visible result and retain structured metadata only where needed.
