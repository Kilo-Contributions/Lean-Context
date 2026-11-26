#!/usr/bin/env python3
"""Evidence-aware semantic diagnostic benchmark; no model calls or task A/B.

Install dev dependencies: python -m pip install pytest tiktoken
Run: python benchmarks/semantic.py
Raw outputs, returned reports, and batch recall are retained beside the JSON.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.pytest_fixture import execute_fixture, expected_ids, fixture_traceback
from leancontext import __version__
from leancontext.compact import compact
from leancontext.mcp import TOOL
from leancontext.pytest_parser import parse_pytest
from leancontext.recall import recall_many
from leancontext.store import Store


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'benchmarks' / 'semantic-results.json')
    parser.add_argument('--artifacts-dir', type=Path,
                        help='Default: semantic-evidence next to --output')
    parser.add_argument('--encoding', default='o200k_base')
    args = parser.parse_args()
    try:
        import pytest
        import tiktoken
    except ImportError:
        raise SystemExit('Install the optional dev dependencies: python -m pip install pytest tiktoken')
    enc = tiktoken.get_encoding(args.encoding)
    count = lambda text: len(enc.encode(text, disallowed_special=()))
    artifact_dir = args.artifacts_dir or args.output.parent / 'semantic-evidence'
    artifact_dir.mkdir(parents=True, exist_ok=True)
    rows = []

    with tempfile.TemporaryDirectory(prefix='leancontext-semantic-bench-') as temp:
        work = Path(temp)
        for mode, expected_groups, target in (('grouped', 3, 6000),
                                              ('distinct', 25, 1200),
                                              ('success', 0, 6000)):
            started = time.perf_counter()
            executed = execute_fixture(work / mode, mode)
            command_ms = (time.perf_counter() - started) * 1000
            expected_exit = 0 if mode == 'success' else 1
            if executed.returncode != expected_exit:
                raise RuntimeError(f'{mode}: unexpected exit {executed.returncode}: '
                                   f'{executed.stdout}\n{executed.stderr}')
            raw = executed.stdout + executed.stderr
            (artifact_dir / f'{mode}.stdout.txt').write_text(executed.stdout, encoding='utf-8')
            (artifact_dir / f'{mode}.stderr.txt').write_text(executed.stderr, encoding='utf-8')
            store = Store(work / mode, work / f'state-{mode}')
            started = time.perf_counter()
            view = compact(raw, store=store, max_chars=target, kind='command-result',
                           metadata={'argv': [sys.executable, '-m', 'pytest'],
                                     'exit_code': executed.returncode})
            transform_ms = (time.perf_counter() - started) * 1000
            (artifact_dir / f'{mode}.returned.txt').write_text(view.text, encoding='utf-8')
            parsed = parse_pytest(raw)
            if parsed is None:
                raise AssertionError(f'{mode}: parser failed to recognize actual pytest output')
            expected = expected_ids(mode)
            indexed = [d.label for d in parsed.diagnostics]
            retained_ids = [nodeid for nodeid in expected if nodeid in view.text]
            if sorted(indexed) != sorted(expected):
                raise AssertionError(f'{mode}: parser inventory does not equal fixture IDs')
            if retained_ids != expected:
                raise AssertionError(f'{mode}: compressed output lost test identifiers')
            representatives = {}
            for diagnostic in parsed.diagnostics:
                representatives.setdefault(diagnostic.signature or diagnostic.id, diagnostic.traceback)
            if len(representatives) != expected_groups:
                raise AssertionError(f'{mode}: expected {expected_groups} groups; got {len(representatives)}')
            if not all(trace and trace in view.text for trace in representatives.values()):
                raise AssertionError(f'{mode}: representative traceback is missing or was changed')
            independent_representatives = ([expected[i] for i in (0, 10, 18)] if mode == 'grouped'
                                           else expected if mode == 'distinct' else [])
            if not all(fixture_traceback(raw, nodeid) in view.text for nodeid in independent_representatives):
                raise AssertionError(f'{mode}: complete raw fixture traceback body did not survive')
            if not view.ref or store.read(view.ref) != raw:
                raise AssertionError(f'{mode}: raw output recovery is not exact')
            if mode == 'distinct' and (not view.budget_exceeded or len(view.text) <= target):
                raise AssertionError('distinct: expected explicit soft-target overrun')
            if mode == 'success' and '202 passed' not in view.text:
                raise AssertionError('success: completion result was not retained')

            # One read-only call retrieves one full failure from each shared group.
            recovery, selectors = '', []
            if mode == 'grouped':
                chosen = [parsed.diagnostics[i] for i in (0, 10, 18)]
                selectors = [d.id for d in chosen]
                recalled = recall_many(store, view.diagnostic_ref, selectors=selectors, max_chars=6000)
                recovery = recalled['text']
                if not all(d.detail in recovery and d.label in recovery for d in chosen):
                    raise AssertionError('grouped: targeted batch recall changed or omitted a full detail')
                (artifact_dir / f'{mode}.batch-recall.txt').write_text(recovery, encoding='utf-8')

            baseline_tokens, returned_tokens, recovery_tokens = count(raw), count(view.text), count(recovery)
            total = returned_tokens + recovery_tokens
            rows.append({
                'name': {'grouped': '25 failures in 3 groups, 202 passes',
                         'distinct': '25 distinct failures, 202 passes, 1200-char target',
                         'success': '202 successful tests'}[mode],
                'mode': mode,
                'category': 'executed_generated_pytest_fixture',
                'strategy': view.strategy,
                'exit_code': executed.returncode,
                'target_chars': target,
                'budget_exceeded': view.budget_exceeded,
                'baseline_chars': len(raw),
                'returned_chars': len(view.text),
                'baseline_tokens': baseline_tokens,
                'returned_tokens': returned_tokens,
                'batch_recall_tokens': recovery_tokens,
                'returned_plus_recall_tokens': total,
                'preview_reduction_pct': round(100 * (1 - returned_tokens / baseline_tokens), 2),
                'reduction_including_recall_pct': round(100 * (1 - total / baseline_tokens), 2),
                'expected_failure_ids': len(expected),
                'indexed_failure_ids': len(indexed),
                'returned_failure_ids': len(retained_ids),
                'inventory_complete': parsed.inventory_complete,
                'expected_groups': expected_groups,
                'observed_groups': len(representatives),
                'whole_representative_tracebacks_returned': len(representatives),
                'independently_verified_complete_tracebacks': len(independent_representatives),
                'batch_recall_calls': 1 if selectors else 0,
                'batch_recall_selectors': selectors,
                'whole_batch_details_returned': len(selectors),
                'raw_recovery_exact': True,
                'command_elapsed_ms': round(command_ms, 3),
                'transform_elapsed_ms': round(transform_ms, 3),
                'baseline_sha256': _sha(raw),
                'returned_sha256': _sha(view.text),
                'batch_recall_sha256': _sha(recovery) if recovery else None,
                'evidence_files': {
                    'stdout': f'{mode}.stdout.txt',
                    'stderr': f'{mode}.stderr.txt',
                    'returned': f'{mode}.returned.txt',
                    'batch_recall': f'{mode}.batch-recall.txt' if selectors else None,
                },
            })

    report = {
        'benchmark': 'LeanContext semantic diagnostics: evidence and local payload',
        'schema_version': 1,
        'leancontext_version': __version__,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'runtime': {'python': platform.python_version(), 'platform': platform.system(),
                    'pytest': pytest.__version__},
        'tokenizer': {'library': 'tiktoken', 'version': tiktoken.__version__, 'encoding': enc.name,
                      'claim': 'Exact in this named encoding, not provider billing or model-specific quota.'},
        'methodology': [
            'Each fixture executes once under real pytest with -v --tb=long --color=no and plugin autoload disabled.',
            'The 25-failure/202-pass fixture mirrors the reported counts; it is not the user\'s unavailable original transcript.',
            'Baseline is actual stdout plus stderr, decoded by subprocess; original stream interleaving is not reconstructed.',
            'Treatment is compact() returned text including diagnostic inventory, whole representative traces, notices, and raw references.',
            'The run-command status/raw-stream wrapper and JSON/MCP protocol envelopes are excluded from these rows.',
            'The grouped row includes one targeted batch recall of three complete failure details in total treatment tokens.',
            'The distinct row intentionally exceeds a 1200-character target to preserve every failure and representative traceback.',
            'Failure IDs are compared with independently constructed fixture IDs, not only with parser output.',
            'Signature grouping is not proof of shared causal origin; assertions measure preservation, not diagnosis quality.',
            'MCP tool-schema tokens are reported separately; model inputs, reasoning, retries, prompts, future replays, and quotas are unmeasured.',
            'No model, autonomous coding agent, live-task equivalence test, or agent latency A/B was run.',
        ],
        'rows': rows,
        'mcp_tool_schema_tokens': count(json.dumps(TOOL, ensure_ascii=False, separators=(',', ':'))),
        'artifact_directory': str(artifact_dir.relative_to(args.output.parent))
                              if artifact_dir.is_relative_to(args.output.parent) else str(artifact_dir),
        'provider_tokens_saved': None,
        'subscription_quota_saved': None,
        'agent_task_latency_saved': None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'artifact_directory': str(artifact_dir),
                      'rows': rows, 'mcp_tool_schema_tokens': report['mcp_tool_schema_tokens']}, indent=2))


if __name__ == '__main__':
    main()
