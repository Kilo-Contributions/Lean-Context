# Changelog

## 0.2.0 — 2026-11-25

- Index every identified pytest failure/error, including setup, teardown, and collection records.
- Group matching exception/traceback signatures conservatively and retain a whole captured representative for each group.
- Treat diagnostic character budgets as soft targets; visibly report excess instead of clipping identities or tracebacks.
- Add `diagnostics` and `recall-many` CLI commands, plus `diagnostics`/`recall_many` operations in the existing read-only MCP tool.
- Preserve immutable diagnostic snapshots so later captures of identical text cannot change failure IDs or completeness notices.
- Select strategies for pytest, successful pytest/unittest runs, compiler errors, exact source/search output, and generic fallback; add `--strategy` override.
- Preserve complete compiler error blocks and notes across common GCC/Clang, TypeScript, MSVC, and Rust formats.
- Count batch recovery in local accounting; retain existing process/permission/hook boundaries and zero-dependency runtime.
- Add real executed 25-failure/202-pass fixtures, independent whole-trace checks, counterexample measurements, and adversarial parser/integration regressions.

## 0.1.0 — 2026-11-25

- Add bounded command capture with exact raw archives and exit preservation.
- Add source-exact retrieval, lazy discovery, outlines and one read-only MCP tool.
- Add project Codex/Claude setup and optional successful Claude Bash filtering.
- Add one-request, evidence-checked optional worker and actual-session audit.
- Include adversarial behavior tests, protocol checks, local BPE benchmark and honest limits.
