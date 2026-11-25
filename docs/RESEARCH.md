# Evidence, prior art, and usage measurement

Research checked 2026-11-25. LeanContext reduces the evidence an agent must read by selecting useful output and retrieving exact source on demand. It does not claim to compress arbitrary conversations without loss, or to have demonstrated an end-to-end coding-agent saving. No authenticated frontier-model A/B run was performed to build this package.

## What existing work establishes

| Method | Primary source | What it supports, and its limit |
| --- | --- | --- |
| Deterministic command-output filtering | [RTK](https://github.com/rtk-ai/rtk), [audit guide](https://github.com/rtk-ai/rtk/blob/develop/docs/usage/AUDIT_GUIDE.md), [recall configuration](https://www.rtk-ai.app/docs/getting-started/configuration/) | RTK already has command-specific filters, bypass, fail-open behavior, and lossless output recall. Its `rtk gain` guide explicitly describes a `bytes / 4` estimator: the displayed saving is an output-byte ratio, not a provider bill. LeanContext does not claim novelty for recoverable filtering. |
| Code execution and output indexing | [Context Mode](https://github.com/mksglu/context-mode), [Anthropic Code Mode article](https://www.anthropic.com/research/code-execution-with-mcp) | Keeping intermediate data outside model context can greatly reduce returned payloads. Context Mode's November 2026 headline is a median 99.4% byte reduction on 36 benchmark rows with at least 10 KB raw output, not an autonomous-agent correctness or cost comparison. |
| Semantic repository retrieval | [Serena](https://github.com/oraios/serena), [Aider repository maps](https://aider.chat/docs/repomap.html) | Serena supplies symbol/reference tools. Aider ranks a dependency graph into a token-budgeted map. These are strong alternatives to sending entire files or repositories; their usefulness depends on language support and task. |
| Inexpensive worker models | [Claude Code subagents](https://code.claude.com/docs/en/sub-agents), [RTK pipeline directory](https://www.rtk-ai.app/pipelines/) | Bounded exploration can run on a different model. RTK's directory labels its advertised pipeline savings as author benchmarks. Worker input, handoffs, retries and verification must all be counted; fewer frontier tokens need not mean fewer total tokens. |
| Learned prompt compression | [LLMLingua repository](https://github.com/microsoft/LLMLingua), [LLMLingua-2 paper](https://aclanthology.org/2024.findings-acl.57/) | The paper evaluates token selection on MeetingBank, LongBench, ZeroScrolls, GSM8K and BBH. That is not proof that deleting tokens from arbitrary source code, diffs or specifications preserves patch correctness. LeanContext's default uses no compression model. |
| Provider prompt caching | [OpenAI](https://developers.openai.com/api/docs/guides/prompt-caching), [Anthropic](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) | Cache reuse changes pricing and latency while keeping the logical context. Rewriting earlier context can change reusable prefixes. Measure cache classes and total cost separately. |

An instructive historical experiment is [JetBrains' July 2026 RTK study](https://blog.jetbrains.com/ai/2026/07/rtk-claude-code-token-savings/). It compared RTK v0.43.0 with stock Claude Code on 86 SkillsBench tasks, with pinned configurations and 425 billed trials across its experimental ladder. The low-effort comparison reported a median 7.6% cost increase; the high-effort comparison was approximately unchanged, with quality reported unchanged. This result applies to that version and workload. It demonstrates why output reduction alone is insufficient evidence for whole-session savings.

## Three different measurements

1. **Payload bytes:** how much text the local tool returns. This is useful for storage and transport, but not a token count.
2. **Tokens under a declared BPE encoding:** count the complete returned text, including headers and retrieval hints, using a real tokenizer such as [tiktoken](https://github.com/openai/tiktoken). A result labeled `o200k_base` is exact for that encoding and text. It does not establish the tokenizer or hidden message overhead of every current Codex or Claude model. A `bytes / 4` estimate must never be relabeled as a BPE measurement.
3. **Whole-session reported usage:** sum the provider's actual usage over the complete task, accounting for cumulative counters, duplicate snapshots, subagents, failed attempts, compaction and retrieval. This includes much more than the tool output. Even these counts do not reveal a subscription's weekly quota rules or directly establish a monetary saving.

An illustrative local result of “90% fewer returned tokens” must therefore remain a local payload result. Include follow-up retrieval in that experiment. A smaller first reply that forces several more reads may have a higher total cost.

## Supported usage-log inputs

`audit` reads only paths explicitly passed to it. It never searches a home directory, discovers sessions, follows references to other logs, or reads credentials. Supply the relevant subagent logs yourself. Examples below are illustrative usage-only records, not recordings from a paid benchmark. Model names are placeholders.

### Codex cumulative rollout records

The supported shape is `event_msg` → `payload.type == token_count` → `info.total_token_usage`:

```jsonl
{"type":"session_meta","payload":{"id":"session-a","model_provider":"openai"}}
{"type":"turn_context","payload":{"model":"example-model"}}
{"timestamp":"2026-01-01T00:00:01Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":100,"cached_input_tokens":60,"output_tokens":20,"reasoning_output_tokens":8,"total_tokens":120},"last_token_usage":{"input_tokens":100,"output_tokens":20}}}}
{"timestamp":"2026-01-01T00:00:02Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":180,"cached_input_tokens":110,"output_tokens":40,"reasoning_output_tokens":15,"total_tokens":220}}}}
```

These records represent **180 input and 40 output tokens**, not 280 and 60. `last_token_usage` is not added to cumulative usage. Cached input is already included in input; reasoning output is already included in output. Their sum is 220, not 345.

Repeated totals add nothing. Within a single export, or a merge with established timestamp order, a decreasing input/output counter starts a new inferred epoch; the previous epoch's last snapshot and the new epoch are added, with an explicit reset warning. The cause of a reset cannot be proven from the numbers alone. Per-model groups use the active `turn_context` metadata and cumulative increments, including cache/reasoning-only updates. A subset correction that decreases without a full reset makes that subset's model attribution unknown rather than retaining a stale value. Attribution can also be incomplete when early metadata or optional counters are missing.

### Codex `exec --json` completed turns

```jsonl
{"type":"thread.started","thread_id":"session-b"}
{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":60,"output_tokens":20}}
{"type":"turn.completed","usage":{"input_tokens":50,"cached_input_tokens":20,"output_tokens":10}}
```

These are per-turn counters: **150 input, 30 output, 80 cached input**. Model/provider identity stays unknown when the export does not report it. Optional `input_tokens_details.cached_tokens`, `input_tokens_details.cache_write_tokens`, and `output_tokens_details.reasoning_tokens` fields are understood when present. An explicit `turn_id`, if supplied by an exporter, supports deduplication. Identical counters on two unidentified turns in one file are still two turns.

### Claude assistant-message usage

```jsonl
{"type":"assistant","sessionId":"session-c","message":{"id":"message-1","role":"assistant","model":"example-claude-model","usage":{"input_tokens":80,"cache_read_input_tokens":30,"cache_creation_input_tokens":5,"output_tokens":5}}}
{"type":"assistant","sessionId":"session-c","message":{"id":"message-1","role":"assistant","model":"example-claude-model","usage":{"input_tokens":80,"cache_read_input_tokens":30,"cache_creation_input_tokens":5,"output_tokens":15}}}
```

The final snapshot replaces the earlier snapshot by `message.id`: **115 input and 15 output**, not twice the input. Claude's input categories are disjoint:

```text
total input = input_tokens + cache_read_input_tokens + cache_creation_input_tokens
```

Optional absent Claude cache counters are interpreted as zero in a recognized Messages usage object; invalid explicit counters are unknown. A separate thinking-token count is not inferred. Claude's [token-counting endpoint](https://platform.claude.com/docs/en/build-with-claude/token-counting) can estimate a request under a named model, but the auditor does not call it.

### Coverage and ambiguity

- Exact duplicate paths/content are counted once. Without session IDs, byte-identical independent exports cannot be distinguished from copies; the report says so.
- Overlapping records are merged by session and identified message/turn. Usage timestamps are used when all relevant records have parseable timezone-aware timestamps. For a cumulative session assembled from multiple nonduplicate files with missing timestamps, chronology is unproven: all of that session's totals and model-group counters are null, in either file order. A stale earlier snapshot must not be mistaken for a reset. Supply one canonical export or fully timestamped records; comparisons cannot calculate a percentage from these unknown totals.
- Other untimed, unidentified overlaps are deduplicated by equal occurrence counts across files and use supplied file order for snapshot replacement. This is explicitly labeled heuristic. Supply one canonical export per session where possible. Claude records without message IDs also receive an ambiguity warning.
- If both cumulative and per-turn representations of one Codex session are supplied, cumulative usage is selected and the alternative view is disclosed rather than added twice. If exports cover disjoint intervals, choose a single complete source instead.
- Unknown usage formats, malformed records, missing counters and unreadable files are reported. Null means unknown, not zero. A later cumulative snapshot can provide a known session total even when optional per-model attribution remains unknown.
- `PostModelRequest` per-attempt hooks and arbitrary raw API/stream exports are not parsed in this release. Failed requests and retries may be absent from supported session exports. Do not treat them as a full invoice ledger.
- `complete` is always false: the formats cannot prove that all relevant sessions, subagents or billable attempts were supplied. Reports describe observed supported counters, not guaranteed account totals.

Run the installed command, or use the module form from the source directory:

```bash
python -m leancontext audit baseline-session.jsonl baseline-worker.jsonl > before.json
python -m leancontext audit treatment-session.jsonl treatment-worker.jsonl > after.json
python -m leancontext compare before.json after.json
```

Comparison reports input and output separately, plus available subsets. `delta` is after minus before; a positive `reduction_pct` means fewer observed tokens. A missing counter or zero baseline has no defined percentage. Comparing two exports does not establish equal task difficulty or correctness. No price or quota is inferred.

## Controlled end-to-end A/B protocol

1. **Freeze the task and verifier.** Use identical starting commits, dependencies, instructions and hidden functional tests. Include realistic investigation, editing, debugging and noisy-output tasks. Decide beforehand what constitutes a correct solution.
2. **Pin the configuration.** Record the agent/extension versions, exact model, effort, limits, enabled tools and worker configuration. Keep the frontier model unchanged when evaluating the default local mechanism. Evaluate worker delegation as a separate arm.
3. **Use isolated runs.** Begin each trial in a clean checkout and a new agent session. Randomize baseline/treatment order. Keep warm/cold cache policy and timeouts consistent. Do not give a later trial a previous solution.
4. **Repeat and retain every result.** A pilot can reveal integration failures, but one run does not establish a saving. Replicate task pairs and report task-level ratios and uncertainty. Include failures, timeouts and retries rather than selecting only successes.
5. **Check treatment exposure.** Record which reads/commands used LeanContext, which bypassed it, and how often raw retrieval was needed. Count installer instructions/tool schemas and all worker/controller calls. An unused integration cannot establish its effect.
6. **Collect complete usage evidence.** Explicitly gather session and subagent exports. Audit each trial, investigate reset/unknown/overlap warnings, and reconcile with a provider ledger when actual cost matters. Keep uncached input, cache reads/writes, output and reasoning subsets distinct. Retain raw transcripts privately.
7. **Grade before interpreting savings.** Run the frozen verifier and review behavior that tests may miss. Report pass rate, total observed input/output, frontier-only and worker usage, wall time, tool/recovery calls, and actual cost when known. Cost per correct solution is total spend across all trials divided by the number of correct solutions, so failed trials are not free.
8. **Publish the scope of the finding.** Report per-task results and confidence intervals. “No regression observed in these tests” is supported by a passing trial; universal “zero correctness loss” is not. Avoid multiplying a local payload reduction by an entire session or subscription bill.

[optexai/token-bench](https://github.com/optexai/token-bench) provides an existing Harbor-based comparison harness with pass-rate and separate input/cache/output metrics. Its availability is a useful starting point, not validation of this package's end-to-end results.
