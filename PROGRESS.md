# LeanContext progress

## 0.2.0 semantic diagnostics

Implemented the user's five requested changes: failure indexing, conservative
signature grouping, whole representatives, targeted batch recall, and adaptive
pytest/success/compiler/source strategies. Diagnostic targets are explicitly
soft; source, generic, worker, and ordinary line recall budgets remain hard.

Added immutable diagnostic snapshots after reproducing a metadata-collision
regression with two commands producing identical bytes. Independently reviewed
and fixed nested-success compiler fallback, git global-option source intent,
ExceptionGroup/multiline body retention, parameter delimiter ambiguity, and
deeply nested snapshot handling. Batch recall is included in local accounting.

Verification is recorded in docs/VERIFICATION.json. The controlled real-pytest
benchmark and complete evidence are in benchmarks/semantic-results.json and
benchmarks/semantic-evidence/. Complete IDs and complete raw representative
bodies are independently checked. No real user-suite reproduction, live coding
agent A/B, quota savings, or end-to-end speed claim is made.

The branch is based on the user's public CPAL-1.0 repository; license and author
attribution are preserved. No merge or public release publication is part of
this change.

## 0.1.0 history

Design and plan written on 2026-11-25. User authorized implementation and delivery; routine design and implementation choices are builder decisions.

Research: current Codex/Claude hooks and prior art checked against primary sources. Chosen default is native-shell capture + read-only MCP retrieval. Claude successful Bash filtering is optional. No Codex permission-rewriting hooks.

Interface scan: Store/compact are shared by capture/hooks; Repository feeds CLI/MCP/worker; audit independent. Parallel workers own disjoint files. Root integrates.

Tasks 1–5 complete. Source, tests, CLI/MCP integrations, usage auditing, optional worker, benchmark and release formats implemented and independently reviewed.

Core regressions verified: exact stream recovery including binary bytes, command execution once, timeout status, output cap, source ranges, conservative hook shapes, installer preservation, literal evidence validation, and provider accounting.

Independent review fixes: output-cap termination now kills remaining descendants even after the group leader exits; malformed API credentials are rejected without disclosure; final MCP/worker outputs honor budgets; provider usage metadata is restricted to numeric counters; archive recovery hints work with a configured zipapp launcher. The benchmark now counts actual rendered source responses and original process streams without added headers.

Accounting fixes: ambiguous multi-file cumulative sessions with missing chronology produce unknown totals instead of an invented counter reset; cache/reasoning-only updates reconcile model groups, with unassignable corrections marked unknown.

Scope decisions: no automatic Codex result hook; Claude successful Bash filtering is optional; optional worker has no model chosen/configured by default; no live authenticated agent A/B or subscription-quota claim. Package source has zero runtime dependencies and is qualified on Linux/Python 3.12, with Python 3.11 the declared minimum.

Final verification: 93 tests pass; Python compilation and git whitespace checks pass. Isolated zipapp, offline clean-venv wheel install, project setup idempotence in a path containing spaces, official MCP SDK handshake/read/boundary checks, exact binary recovery and wheel/zipapp/source identity checks pass. See docs/VERIFICATION.json and docs/RESULTS.md.
