# Release results — LeanContext 0.2.0

## Semantic evidence benchmark

The benchmark executes generated tests under real pytest 9.2.0 on Linux with
Python 3.12.14. Counts use tiktoken 0.14.0 and the named `o200k_base` encoding.
The original user transcript was unavailable; these fixtures reproduce its
25-failure/202-pass counts, not its code or underlying bugs.

| Executed workload | Original tokens | Report tokens | Report + batch recall | Reduction including recall | Evidence retained |
| --- | ---: | ---: | ---: | ---: | --- |
| 25 failures in 3 signature groups; 202 passes | 12,241 | 1,398 | 2,320 | 81.05% | 25/25 identifiers, 3/3 whole representative traces; one batch retrieves 3 full details |
| 25 distinct failures; 202 passes; 1,200-character target | 10,683 | 5,487 | 5,487 | 48.64% | 25/25 identifiers, 25/25 whole traces |
| 202 successful tests | 4,163 | 69 | 69 | 98.34% | Final completion summary; raw logs remain recoverable |

The grouped report alone is **88.58% smaller** and occupies 4,773 characters.
The distinct case occupies **17,972 characters** and explicitly sets
`budget_exceeded=true`: all 25 traces remain visible even though they cannot fit
the 1,200-character target. This is an intentional retention result, not a failed
attempt to enforce a hard cap. No blanket token-reduction percentage is promised.

Expected identifiers come from the fixture definition. Whole traceback bodies
are independently extracted from the controlled raw transcript, then checked
verbatim in the returned report. This prevents a shortened parser selection from
passing merely because the parser calls its own selection a complete traceback.

Each fixture executes once. Baseline is captured stdout plus stderr; treatment
includes the semantic report's index, representatives, notices and references.
The grouped total includes one batch recall of F1, F11 and F19, including their
captured logs. The command-runner status/raw-stream footer and JSON/MCP envelopes
are excluded from this microbenchmark. The MCP tool schema is **279 tokens**,
reported separately. Tiny commands and all-distinct workloads can expand once
wrapper/index overhead is counted; keep already-short commands direct.

Local transformation timings are in the JSON report. They include local parsing
and storage, not agent reasoning, provider inference, or end-to-end task latency.
No frontier/local model, live authenticated coding agent, task-equivalence A/B,
invoice, or subscription-quota measurement was performed.

Reproduce:

```bash
python3 -m pip install -r benchmarks/requirements.txt
python3 -m unittest discover -q
python3 benchmarks/semantic.py
```

Evidence: [semantic-results.json](../benchmarks/semantic-results.json) and the
raw stdout/stderr, returned reports, and batch recall files in
`benchmarks/semantic-evidence/`. Results vary slightly between runs because timing,
temporary paths, and content-addressed references vary. Every recorded file has
a SHA-256 digest in the machine-readable report.

## Release verification

The complete suite passes **156 tests**, including the four executed pytest
fixtures. A standard-library-only run passes the other 152 and explicitly skips
those four optional fixture tests. Code compilation and source/document whitespace
checks pass; raw pytest transcript whitespace is preserved verbatim. See
[VERIFICATION.json](VERIFICATION.json) for distribution checks.

Independent review found and fixed missing ExceptionGroup/multiline diagnostics,
parameter-ID delimiter ambiguity, mutable artifact metadata affecting recall,
nested-success compiler fallback, Git global-option source detection, and deep
snapshot JSON that could terminate the MCP server. Tests cover each regression.

The completeness guarantee applies to LeanContext's generated report and stored
artifacts. A host can still impose a smaller output limit; targeted recall is
the recovery path. Custom reporter formats or already-truncated producer output
can yield explicitly unknown/incomplete inventories.

## Historical 0.1.0 measurements

The following results and checks describe the previous release. They are kept
for comparison and are not the 0.2.0 test count or semantic retention guarantee.

## What was measured

The recorded benchmark uses `tiktoken 0.14.0`, encoding `o200k_base`, and a
6,000-character return budget. These are exact counts in that encoding. They do
not establish current Codex/Claude billed tokens, subscription allowance, or
autonomous task performance. Full machine-readable results and the runnable
benchmark are in `benchmarks/`.

| Case | Raw baseline tokens | Returned + recovery tokens | Reduction |
| --- | ---: | ---: | ---: |
| Actual package test suite, verbose (93 tests) | 3,129 | 1,232 | 60.63% |
| Generated, executed 350-test fixture with one failure | 6,061 | 1,349 | 77.74% |
| Synthetic warning-heavy compiler transcript | 24,616 | 1,162 | 95.28% |
| Real source: full file versus outline plus exact range | 1,655 | 572 | 65.44% |
| Short successful command, deliberately a bad fit | 9 | 60 | 51 additional tokens |

The two failure stress rows include one literal recovery response. Returned text
includes status, omissions and artifact references. Original command baselines
concatenate stdout then stderr without LeanContext's stream headings; original
stream interleaving is not claimed. The source row includes the actual rendering
used by CLI/MCP and has an explicit full-file baseline. An agent already reading
the exact relevant range may see little additional benefit on that task.

The single MCP tool's serialized definition measured 195 tokens in the same
encoding. Host framing, project instructions, tool-call arguments, provider
prompts, retries and future context replays are not part of per-row results.
Token counts can vary on rerun because timings and temporary paths occur in real
outputs. A mixed aggregate is recorded for reproducibility but should not be used
as a projected session-wide reduction.

Local processing of the synthetic 104,561-byte compiler transcript had a median
of 8.199 ms over five runs in this environment (range 7.789–9.164 ms). This times
the local transformation and archive operation, not an LLM or agent task. It does
not establish end-to-end speedup.

## Verification performed

- The full standard-library test suite passed: **93 tests**.
- Coverage includes process exit codes, single execution, exact binary stream
  recovery, timeouts, cap-triggered descendant termination, diagnostic priority,
  source path boundaries, ignore handling, exact source ranges, hook schemas,
  installer preservation, malformed credentials, worker quote validation,
  final output budgets, and usage-accounting edge cases.
- Two independent reviewers examined implementation, measurement and integration.
  All demonstrated release findings were corrected and covered by regressions.
- The standalone zipapp ran with isolated Python imports, from a fresh extracted
  directory. The wheel installed with `--no-index --no-deps` in a clean venv.
- Both project installers were exercised in a path containing spaces and were
  idempotent. The exact configured MCP launcher started outside the source tree.
- The official Python MCP client SDK negotiated protocol `2025-11-25`, listed
  the one read-only tool, retrieved exact source and confirmed path rejection.
- Every source module in the wheel and zipapp matched the reviewed source bytes.
- Release checksums were verified. See `VERIFICATION.json` and `SHA256SUMS`.

Qualification environment: Linux, Python 3.12.14. Python 3.11 is the declared
minimum; Windows native process-tree behavior has not been qualified.

## What remains unmeasured

No authenticated Codex/Claude task A/B was run. No real cheaper/local model was
used to benchmark the optional worker; its protocol and constraints were tested
with a local HTTP stub. No account usage, money saved, quota extension, global
correctness equivalence or end-to-end latency reduction is claimed.

The useful next measurement is a paired run on actual coding tasks. Keep the
model, reasoning settings, prompt, repository starting state and validation
criteria fixed; include unsuccessful runs, retries, subagents and worker usage.
Use `audit` and `compare` on explicit session exports, together with the protocol
in `RESEARCH.md`. Unknown counters remain unknown instead of becoming a claimed
saving.
