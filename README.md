# LeanContext

**Spend frontier-model context on decisions and relevant evidence.** LeanContext
is a local command tool and read-only MCP server for Codex and Claude Code. It
captures verbose output once, returns an adaptive diagnostic view, keeps the
original bytes available for retrieval, and supplies exact source ranges.

It also includes project installers, an optional Claude Bash-result hook, a
bounded cheaper-model worker, and an auditor for actual agent session usage.
Default operation makes **zero model requests** and needs **no API key, GPU,
database, service, or third-party Python dependency**.

**Requirements:** Python 3.11+. Developed and tested on Linux. The explicit
command runner is intended for noninteractive foreground commands; Windows
process-tree and Git pipe behavior have not been qualified. WSL/Linux is the
recommended environment for this release.

**New in 0.2.0:** semantic diagnostic compression preserves every identified
pytest failure, groups matching exception/traceback signatures, and returns a
whole captured representative per group. Batch recall retrieves several failure
details at once. If mandatory evidence exceeds the character target, the report
says so and keeps it. See [semantic diagnostics](docs/SEMANTIC_DIAGNOSTICS.md).

## Quick start

Extract the release archive into a location you will keep. The runnable
`leancontext.pyz` is self-contained; do not move it after registering it without
rerunning setup.

```bash
python3 leancontext.pyz doctor
python3 leancontext.pyz setup --root /absolute/path/to/your/project --agent both
```

Use `--agent codex` or `--agent claude` for one host. Add `--dry-run` to inspect the
exact proposed files before writing them. The installer preserves other settings,
creates private backups, and only edits the selected project. Restart the coding
agent inside that project and inspect `/mcp`; honor the host's project trust and
MCP prompts.

That installs:

- Codex: a project `.codex/config.toml` MCP entry and a short `AGENTS.md` block.
- Claude Code: a project `.mcp.json` MCP entry and a short `CLAUDE.md` block.
- No global model settings, permission grants, or automatic model switches.

The MCP server offers **one read-only `context` tool**. The managed instructions
explain when to use it and how to use the native command wrapper. This is
instruction-driven adoption; it does not force every agent action through a
universal interception layer. See [integration details](docs/INTEGRATIONS.md).

You can alternatively install the bundled wheel in a virtual environment or use
`pipx install .` from this source tree. These instructions use the zipapp so
installation does not require fetching packages. Replace `python3
/absolute/path/leancontext.pyz` with `leancontext` when using an installed CLI.

## What reduces frontier use

| Capability | What stays outside frontier context | What the agent receives |
| --- | --- | --- |
| Command capture | Full stdout/stderr, repetitive progress and passing-test output | Exit code, complete recognized failure index, representative traces, explicit uncertainty |
| Diagnostic grouping | Repeated matching traces and captured logs | All failure identifiers, one whole representative per signature, batch detail recall |
| Exact source retrieval | Unrelated files and unrelated functions | Literal search matches, actual source lines, symbol locations |
| Batch checks | Intermediate orchestration and separate round trips | One adaptive result per independent check |
| Optional worker | Bounded discovery/explanation on a configured cheaper model | A short untrusted draft with checked literal evidence quotes |
| Usage audit | Unsupported guesses about quota or dollar savings | Observed provider counters from explicitly supplied logs |

Output reduction and selective retrieval are established methods, also used by
RTK, Context Mode, Serena and other tools. This project does not claim they are
novel. Its implementation emphasizes a small offline distribution, recoverable
evidence, cautious host integration, and measurements with clear denominators.
See [research and comparisons](docs/RESEARCH.md).

## Capture a verbose command

```bash
python3 /absolute/path/leancontext.pyz run --root /path/to/project --timeout 120 -- python3 -m unittest discover -v
python3 /absolute/path/leancontext.pyz run --root /path/to/project --max-chars 6000 -- npm test
```

Arguments after `--` are passed directly to the executable. There is no implicit
shell. When shell syntax is actually needed, request it explicitly:

```bash
python3 /absolute/path/leancontext.pyz run --root /path/to/project -- bash -lc 'npm run lint && npm test'
```

The command runs exactly once. The wrapper preserves its exit code; timeout is
124 and output-cap termination is 125. A signal exit uses the customary 128+
signal value. Timeout and output-cap results explicitly say **INCOMPLETE**.
Defaults are 120 seconds, 64 MiB captured output and a 6,000-character return
target. `--timeout`, `--max-output-mb`, and `--max-chars` adjust them.

For semantic failure/compiler reports, **the character target is soft**: the
complete recognized index and whole representative traces take priority. A
visible notice and JSON `budget_exceeded` flag report an overrun. Source, ordinary
line recall, generic excerpts, and optional worker output retain hard budgets.
`--strategy generic` explicitly requests the old bounded excerpt with omission
notices; use `--strategy pytest`, `compiler`, or `source` to override detection.

The budget is characters, **not an estimated token count**. Tokens are measured
separately with a named tokenizer in the benchmark and with actual counters in
session audits. Archives preserve separate process streams byte-for-byte;
the combined diagnostic view does not promise original interleaving order.

The wrapper remains an ordinary native-shell command. It does not add approval
rules. Its arguments must be assessed under the host's normal approval process;
do not blanket-allow arbitrary `leancontext run` commands if you rely on
command-specific approvals.

**Keep short commands direct.** A wrapper adds status and reference text and can
increase tokens for a command that already emits a few lines. The setup
instructions deliberately reserve it for expected verbose work. Generic excerpts
remain lossy selections. Semantic reports preserve identified diagnostic records
but cannot restore identifiers or frames that the underlying tool never printed.
Read inventory-completeness notices before making an exhaustive claim.

### Recover the original without rerunning

```bash
python3 /absolute/path/leancontext.pyz recall ARTIFACT_REF --root /path/to/project --query AssertionError
python3 /absolute/path/leancontext.pyz recall ARTIFACT_REF --root /path/to/project --start 120 --lines 80
python3 /absolute/path/leancontext.pyz recall ARTIFACT_REF --root /path/to/project --raw > full-output.bin
```

For a long single line, bounded recall returns a continuation `--start` and
`--offset`. `--raw` deliberately removes the output budget and is intended for
file redirection or local processing. Sending it wholesale back to the model
undoes the savings. References are scoped to the project root used at capture.

### Diagnose failures and recall several details together

Recognized pytest output automatically gets F1, F2, … failure IDs and G1, G2, …
signature groups. Compiler errors use D1, D2, …. Grouping requires matching
evidence; equal exception classes alone are not enough. These groups suggest
where to investigate, without proving a shared underlying cause.

```bash
python3 /absolute/path/leancontext.pyz run --root /path/to/project -- python3 -m pytest -v
python3 /absolute/path/leancontext.pyz recall-many DIAGNOSTIC_REF F1 F8 F25 --root /path/to/project
python3 /absolute/path/leancontext.pyz diagnostics OLD_RAW_REF --root /path/to/project
```

Use the diagnostic reference printed beside `recall-many`. It names an immutable
snapshot, so a later command producing identical raw output cannot change its
IDs or completeness notices. The separate raw reference remains available for
exact line/byte recovery. `diagnostics` can also upgrade existing v0.1 raw archives
without rerunning the failed command.

MCP uses the existing `context` tool:

```json
{"op":"recall_many","ref":"DIAGNOSTIC_REF","selectors":["F1","F8","F25"]}
```

F/D selectors return whole per-record details, including captured logs. A G
selector returns that group's representative; an exact node ID returns every
phase of that test. Repeated selectors are deduplicated and unknown selectors
are reported alongside successful matches. Whole selected details can exceed
the character target.

### Batch independent checks

Create `checks.json` containing argument arrays:

```json
[
  ["python3", "-m", "unittest", "discover"],
  ["git", "diff", "--check"]
]
```

```bash
python3 /absolute/path/leancontext.pyz batch checks.json --root /path/to/project --jobs 2
```

Default is sequential. Only use parallel jobs for independent checks. Each
command has its own budget and exit code; the batch exits nonzero if any check
fails. At most 16 commands and 8 parallel workers are accepted. This reduces
agent round trips when it adopts batching; it does not shorten the underlying
tests or promise a faster whole task.

## Find code before reading it all

```bash
python3 /absolute/path/leancontext.pyz context --root /path/to/project files --glob 'src/*.py'
python3 /absolute/path/leancontext.pyz context --root /path/to/project search 'settle_pending' --glob '*.py'
python3 /absolute/path/leancontext.pyz context --root /path/to/project outline src/runtime.py
python3 /absolute/path/leancontext.pyz context --root /path/to/project read src/runtime.py --start 70 --end 120
```

Read output uses exact source lines and their real line numbers. It never
compresses code into paraphrases. Python outlines use the AST; other supported
patterns are labeled regex heuristics and can miss symbols. Read exact source
before editing. A line too large for the range budget is not clipped: the result
reports the required size. Use a native file tool for such a line if it exceeds
the MCP's 12,000-character limit.

Queries are literal, bounded and lazy. Git ignore rules apply when Git is
available; the fallback walker labels its approximate ignore handling. Default
exclusions cover generated/dependency directories and common secret filenames.
This is **not secret-content detection**: a key placed in an ordinary source
file is ordinary source to this tool. Symlinks, root escapes, nonregular files,
binary/non-UTF-8 files and files over 2 MiB are rejected. Searches report their
scope and any 5,000-entry, 16 MiB, time or output limit.

## Optional automatic Claude filtering

```bash
python3 /absolute/path/leancontext.pyz setup --root /path/to/project --agent claude --hooks
```

The synchronous PostToolUse hook filters large successful Bash results from
recognized test/build/log/status commands on Claude Code versions supporting
`updatedToolOutput` (documented from 2.1.121). It never rewrites the command,
changes permissions, or alters source Read results. Unknown shapes and storage
failures pass through unchanged.

The hook cannot replace PostToolUseFailure errors, recover content Claude already
truncated, or guarantee compatibility with every client version. Its archive is
the complete JSON result received by the hook, not necessarily all original
process bytes. Use the explicit runner for full capture including failed tests.
No equivalent automatic Codex hook is installed: current Codex post hooks do not
provide a reliable universal interception point. Adapter/protocol fixtures were
tested; a live authenticated Claude/Codex session was not run for this release.

## Optional local or cheaper-model worker

The default pipeline needs no smaller model. This optional feature is most useful
when frontier tokens are being spent on repeated discovery or extracting facts
from several files. It makes **one** bounded request, with no automatic retries,
tools, code edits, or model swaps.

Copy [worker.example.json](integrations/worker.example.json) and set the exact
model identifier exposed by your OpenAI-compatible inference server:

```json
{
  "base_url": "http://127.0.0.1:8080/v1",
  "model": "YOUR_LOCAL_MODEL_ID",
  "max_input_chars": 24000,
  "max_output_tokens": 1000,
  "timeout": 60
}
```

```bash
python3 /absolute/path/leancontext.pyz worker 'Explain where this module handles timeouts; cite exact evidence.' --root /path/to/project --config /path/to/worker.json --file src/runtime.py
```

Only explicitly named eligible files are included. File excerpts and the total
request are bounded; large files may be partial. The worker must return a JSON
answer with literal evidence quotes. Unknown IDs or invented quotes are
rejected. Valid quotes establish source attribution, not that the conclusion is
correct; the frontier model must review the untrusted draft.

For a remote endpoint, set `allow_remote: true`; HTTPS is required unless you
explicitly set `allow_insecure_http: true` for a trusted network. If a key is
needed, set `api_key_env` to the name of an environment variable containing it.
Never put the key itself in the JSON file. Redirects and environment proxy
settings are not used. `token_parameter` can select `max_completion_tokens`
instead of the default `max_tokens` for compatible backends.

The worker has been tested against a local protocol stub, **not benchmarked with
a real model**. Its quality and token/time tradeoff depend on the model. No claim
is made that every worker task is cheaper. This package does not load, retrain,
replace, or alter any of your models or their memory systems.

## Measure the right thing

```bash
python3 /absolute/path/leancontext.pyz stats --root /path/to/project
python3 /absolute/path/leancontext.pyz audit /path/to/baseline-session.jsonl > before.json
python3 /absolute/path/leancontext.pyz audit /path/to/treated-session.jsonl > after.json
python3 /absolute/path/leancontext.pyz compare before.json after.json
```

`stats` measures local emitted text/bytes and includes retrieval output. It is not
a cost or quota counter. `audit` only opens the explicit files supplied; it does
not discover sessions or read credential files. It supports recognized Codex
session cumulative counters, Codex exec per-turn usage, and Claude assistant
message usage. Include explicit subagent log paths when needed.

Codex cached input and reasoning output are subsets of input/output totals.
Claude's new-input, cache-read and cache-creation categories are added to compute
total input. Repeated snapshots and overlapping exports are deduplicated where
identities allow. Unknown or missing fields remain unknown. Reports always warn
that logs alone cannot prove complete billable coverage. Do not mix unrelated
tasks or runs and call the delta an intervention effect.

No API invoice, subscription accounting formula, weekly allowance gain, or
whole-agent latency improvement has been measured in this release. Use the
paired-run protocol in [RESEARCH.md](docs/RESEARCH.md): same task, clean starting
tree, same model/reasoning settings, multiple repetitions, compare correctness,
actual usage and wall time. Include worker usage, failures and recovery calls.

## Reproduce validation and the local benchmark

```bash
python3 -m unittest discover -v
python3 -m pip install -r benchmarks/requirements.txt
python3 benchmarks/run.py --output benchmarks/results-rerun.json
python3 benchmarks/semantic.py
```

[Recorded results](benchmarks/results.json) contain real `o200k_base` BPE counts,
the precise workload category, raw/reduced hashes, reference overhead and
recovery response tokens. These are exact counts for that named encoding,
**not exact current Codex/Claude billed tokens**. Per-row counts exclude model
prompts, tool-call arguments and protocol envelopes; the MCP tool schema is
counted separately. Real project commands, generated executed tests, synthetic
logs and the short-output counterexample are labeled separately.

The benchmark intentionally demonstrates where the wrapper is a bad fit. No
percentage in that file establishes an autonomous task's total savings. The
default 6,000-character budget is an adjustable engineering choice, not a
universal optimum.

The [semantic benchmark](benchmarks/semantic-results.json) measures failure-ID
retention and independently extracted whole traceback retention alongside token
counts. It executes 25-failure/202-pass pytest fixtures with both repeated and
distinct errors, plus a successful run. Its raw transcripts, returned text, and
batch recall are in `benchmarks/semantic-evidence/`. These are controlled generated
workloads, not the user's original failing suite or an autonomous task A/B.
See [release results](docs/RESULTS.md) for measurements and scope.

## Storage, cleanup and removal

Artifacts live in a private per-project directory under
`$XDG_CACHE_HOME/leancontext` (normally `~/.cache/leancontext`). Directories use
0700 and files 0600 on POSIX. Source/output stays on the local machine unless you
explicitly invoke the configured worker. Retained output can contain sensitive
data produced by your commands. No automatic expiration occurs while a
conversation may still reference it.

```bash
python3 /absolute/path/leancontext.pyz prune --root /path/to/project --days 30
python3 /absolute/path/leancontext.pyz prune --root /path/to/project --days 30 --apply
```

The first command is a dry run; the second deletes the selected old archives.
Deleted references cannot be recovered. Local measurement events remain.
Installation removal and backup restoration are documented in
[INTEGRATIONS.md](docs/INTEGRATIONS.md#disable-or-remove-the-integration).

## Release contents and license

The release contains the runnable zipapp, complete Python source, unittest suite,
integration instructions, example worker config, reproducible benchmark and
results, wheel when built, and SHA-256 checksums. No third-party implementation
has been vendored.

## License

LeanContext is licensed under the [Common Public Attribution License 1.0](LICENSE) (CPAL-1.0).

The CPAL is the Mozilla Public License 1.1 with two additions:

- **Attribution:** anything built from this code, including larger works that contain it, must display the
  attribution in Exhibit B each time it starts (where it presents a graphical user interface).

  > © 2026 Blaine Bouvier. Originally created by Blaine Bouvier. https://github.com/WebWalker3D/Lean-Context

- **Network use:** letting other people use a modified version over a network counts as distribution, so you
  must make your source code available.
