# LeanContext progress

Design and plan written on 2026-11-25. User authorized implementation and delivery; routine design and implementation choices are builder decisions.

Research: current Codex/Claude hooks and prior art checked against primary sources. Chosen default is native-shell capture + read-only MCP retrieval. Claude successful Bash filtering is optional. No Codex permission-rewriting hooks.

Interface scan: Store/compact are shared by capture/hooks; Repository feeds CLI/MCP/worker; audit independent. Parallel workers own disjoint files. Root integrates.

Tasks 1–5 complete. Source, tests, CLI/MCP integrations, usage auditing, optional worker, benchmark and release formats implemented and independently reviewed.

Core regressions verified: exact stream recovery including binary bytes, command execution once, timeout status, output cap, source ranges, conservative hook shapes, installer preservation, literal evidence validation, and provider accounting.

Independent review fixes: output-cap termination now kills remaining descendants even after the group leader exits; malformed API credentials are rejected without disclosure; final MCP/worker outputs honor budgets; provider usage metadata is restricted to numeric counters; archive recovery hints work with a configured zipapp launcher. The benchmark now counts actual rendered source responses and original process streams without added headers.

Accounting fixes: ambiguous multi-file cumulative sessions with missing chronology produce unknown totals instead of an invented counter reset; cache/reasoning-only updates reconcile model groups, with unassignable corrections marked unknown.

Scope decisions: no automatic Codex result hook; Claude successful Bash filtering is optional; optional worker has no model chosen/configured by default; no live authenticated agent A/B or subscription-quota claim. Package source has zero runtime dependencies and is qualified on Linux/Python 3.12, with Python 3.11 the declared minimum.

Final verification: 93 tests pass; Python compilation and git whitespace checks pass. Isolated zipapp, offline clean-venv wheel install, project setup idempotence in a path containing spaces, official MCP SDK handshake/read/boundary checks, exact binary recovery and wheel/zipapp/source identity checks pass. See docs/VERIFICATION.json and docs/RESULTS.md.
