# Release results — LeanContext 0.1.0

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
