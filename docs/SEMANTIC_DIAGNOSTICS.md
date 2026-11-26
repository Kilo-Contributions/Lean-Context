# Semantic diagnostics in LeanContext 0.2.0

LeanContext now selects a compression strategy based on the output structure and
the command's intent. It does this locally with Python's standard library. No
LLM, embedding model, network service, or pytest plugin is involved.

## The evidence contract

For recognized pytest reports, the response contains every identified failure
and error record, an explicit mapping to signature groups, and one whole captured
traceback or diagnostic body per group. The index includes setup, call, teardown,
and collection records when the output distinguishes them. Each record has an
F ID; each signature group has a G ID. Compiler records use D IDs.

A complete index is more important than fitting an arbitrary character limit.
If the index plus representatives exceeds `--max-chars`, the response explicitly
reports that fact and returns the evidence. JSON results expose
`budget_exceeded`, `diagnostic_count`, `group_count`, `inventory_complete`, and
`diagnostic_ref`. The final CLI/MCP renderer does not apply a second truncation.

`inventory_complete=true` means the identified pytest records reconcile with
the captured final failure/error totals. It does not establish that the test run
executed everything intended, nor that pytest printed every frame. A count
mismatch, unresolved heading, output cap, or timeout prevents a completeness
claim. Missing totals produce unknown completeness. Compiler reports describe
recognized errors observed in the output and do not claim complete inventories.

## What grouping means

The normal pytest signature includes the execution phase, every recognized
displayed frame location, exception/assertion lines, marked source expressions,
and exception-chain relationships. Messages, assertion values, addresses, paths,
and line numbers are not erased to manufacture a match. Native Python traces use
their complete displayed trace text. Uncertain formats and hidden frames remain
separate. Compiler signatures cover the whole ANSI-normalized diagnostic block.

Matching signatures are evidence of similar failures, not proof of a shared
root cause. The model can inspect individual members when their captured logs or
inputs matter. All original bytes remain available in the raw archives.

Representatives preserve exactly what was captured, including the producer's
own omissions. `pytest --tb=short`, `--tb=line`, and `--tb=no` cannot provide the
same evidence as `--tb=long`. LeanContext does not invent missing frames or change
the test command. `--tb=line` records stay ungrouped when hidden frames cannot be
compared. Missing diagnostic bodies are reported explicitly. Unusual nonempty
bodies, including exception-group trees and failures without Python frames, are
retained conservatively.

## Use it

Run noisy tests through the wrapper:

```bash
python3 /absolute/path/leancontext.pyz run --root /path/to/project -- python3 -m pytest -v
```

A semantic response has this shape (illustrative identifiers):

```text
[LeanContext pytest-failures; 25 indexed diagnostics; 3 signature groups; ...]
25 failed, 202 passed in ...

Failure/error index (all identified records):
F1 [G1] FAILED tests/test_api.py::test_request[case-0]
F2 [G1] FAILED tests/test_api.py::test_request[case-1]
...
F25 [G3] FAILED tests/test_api.py::test_request[case-24]

G1: 10 record(s); representative F1
<whole captured representative>
...
Artifact DIAGNOSTIC_REF. Recall details in one call: recall-many ...
Raw output: RAW_REF; use line recall for omitted logs.
```

The ellipses above describe the format; real semantic output does not use them to
drop indexed records or cut a representative.

Retrieve selected failures together:

```bash
python3 /absolute/path/leancontext.pyz recall-many DIAGNOSTIC_REF F1 F8 F25 --root /path/to/project
python3 /absolute/path/leancontext.pyz recall-many DIAGNOSTIC_REF G2 'tests/test_api.py::test_request[case-24]' --root /path/to/project
```

Selection rules:

| Selector | Result |
| --- | --- |
| `F8` or `D8` | Whole record details, including any captured stdout/stderr/log section |
| `G2` | That group's representative record details |
| Exact test node ID | All records for that node, including multiple error phases |
| Repeated/overlapping selectors | Each record once, in requested order |
| Unknown selector | An explicit unmatched-selector notice; valid selections still returned |

The batch accepts 1–100 selectors. Its character limit is also a soft target;
complete selected details are returned without mid-trace clipping. The local
ledger counts the batch as one recovery call and includes all returned bytes.

The existing read-only MCP `context` tool exposes equivalent operations:

```json
{"op":"diagnostics","ref":"RAW_OR_DIAGNOSTIC_REF","max_chars":6000}
```

```json
{"op":"recall_many","ref":"DIAGNOSTIC_REF","selectors":["F1","F8","F25"],"max_chars":6000}
```

## Existing archives and stable references

Old v0.1 command archives can be reinterpreted without rerunning the command:

```bash
python3 /absolute/path/leancontext.pyz diagnostics OLD_RAW_REF --root /path/to/project
```

A diagnostic snapshot serializes the parsed records and their completeness
notices into a separate content-addressed artifact. Its IDs and evidence stay
stable even if another command later produces identical raw text with different
command metadata, or if a later release changes its parser. Use the diagnostic
reference printed in the report for reliable batch recall. Raw references remain
accepted for parsing legacy output on demand.

The raw reference is still the right input for ordinary line/byte recovery:

```bash
python3 /absolute/path/leancontext.pyz recall RAW_REF --root /path/to/project --start 120 --lines 80
python3 /absolute/path/leancontext.pyz recall RAW_REF --root /path/to/project --raw > raw-output.txt
```

## Strategy selection

| Output/intent | Strategy | Retention rule |
| --- | --- | --- |
| Pytest failures/errors | `pytest-failures` | All identified records and whole representative diagnostic bodies |
| Successful pytest/unittest | `test-success` | Actual final completion summary; progress and captured logs archived |
| Common GCC/Clang, TypeScript, MSVC, Rust errors | `compiler` | All recognized error blocks, source/carets/notes; only exact duplicate blocks group |
| Source/search commands and repository operations | `source` / exact repository retrieval | Whole exact lines, explicit omissions/continuation; no diagnostic rewriting |
| Unsupported or unrecognized output | `generic` | Existing bounded line-priority excerpt with raw recovery |

Warnings accompanying compiler errors are counted and explicitly omitted from
the semantic error report, while the raw artifact preserves them. Warning-only
output uses the generic strategy. Repository `context search/read/outline/files`
continues to use its existing exact-source and bounded-scope behavior.

Use `run --strategy pytest`, `compiler`, `source`, or `generic` to override auto
detection. `generic` restores the hard character budget, with explicit omissions;
it intentionally does not promise the semantic retention contract. Known source
commands take precedence over diagnostic-looking strings in their output.
The override applies to `run` and `batch`; `diagnostics`/`recall-many` can select
`pytest` or `compiler` when interpreting a raw archive. Existing snapshots retain
their original parsed interpretation.

## Integration boundaries

The explicit wrapper works with both Codex and Claude Code and preserves command
exit status. The optional Claude PostToolUse adapter uses these same strategies
for eligible results and does not recut a semantic report to meet its target.
The adapter still cannot replace PostToolUseFailure results. Use the explicit
wrapper to capture failing commands reliably. Installation does not change
permissions, reasoning settings, or models.

Host clients can impose their own output/context limits after LeanContext
returns a report. This tool cannot override those limits. If a host truncates a
large report, use targeted batch recall or inspect its raw artifact locally.
No transcript parser can recover output discarded before it reached the wrapper.

## Validation and remaining scope

The executable fixture runs 25 failing parametrized cases plus 202 passing
cases, with a separate all-distinct variant. Expected node IDs come from the
fixture definition; complete traceback bodies are extracted independently from
the controlled raw output. Tests verify both identity retention and verbatim
trace retention, not just a parser's own claim of completeness.

Tests also exercise setup/teardown/collection, class/parameter punctuation,
ANSI, suppressed/short/line/native traces, exception chains and groups,
incomplete captures, low targets, batch recall, immutable snapshot metadata,
CLI/MCP parity, source intent, compiler blocks, and hard-budget regressions.

This remains a conservative terminal-text parser. Third-party pytest reporters,
localized/custom formats, interleaved parallel logs, producer-level truncation,
and ambiguous headings can require raw recall. The implementation reports
unknown/incomplete inventories rather than treating that uncertainty as success.

Primary format references:

- [Pytest output and traceback options](https://docs.pytest.org/en/stable/how-to/output.html)
- [Pytest report API](https://docs.pytest.org/en/stable/reference/reference.html)
- [Clang diagnostic formatting](https://clang.llvm.org/docs/UsersManual.html)
- [Rust diagnostic structure](https://rustc-dev-guide.rust-lang.org/diagnostics.html)

See [RESULTS.md](RESULTS.md) and `benchmarks/semantic-results.json` for measured
payload reductions. These do not measure whole-agent task success, provider
quotas, billing, or end-to-end latency.
