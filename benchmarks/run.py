#!/usr/bin/env python3
"""Reproducible local payload benchmark, explicitly NOT an autonomous agent A/B.

Run from the release root: python benchmarks/run.py --output benchmarks/results.json
Optional measurement dependency: pip install 'tiktoken>=0.9,<1'
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from leancontext.compact import compact
from leancontext.mcp import TOOL, render_result
from leancontext.recall import recall
from leancontext.repository import Repository
from leancontext.runner import run_command
from leancontext.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'benchmarks'/'results.json')
    parser.add_argument('--encoding', default='o200k_base')
    args = parser.parse_args()
    try:
        import tiktoken
    except ImportError:
        raise SystemExit('Install the optional benchmark dependency: python -m pip install tiktoken')
    enc = tiktoken.get_encoding(args.encoding)
    count = lambda text: len(enc.encode(text, disallowed_special=()))
    rows = []

    def raw_process_text(result,store):
        # Count original streams, without LeanContext's presentation headings.
        # Concatenation is stdout then stderr; original interleaving is unknown.
        return (store.read_bytes(result.stdout_ref)+store.read_bytes(result.stderr_ref)).decode('utf-8',errors='backslashreplace')

    def add(name, category, baseline, reduced, *, assertions, elapsed_ms, recovery=''):
        raw_tokens, reduced_tokens = count(baseline), count(reduced)
        total_tokens = reduced_tokens + count(recovery)
        rows.append({'name':name,'category':category,'baseline_tokens':raw_tokens,
                     'returned_tokens':reduced_tokens,'recovery_tokens':count(recovery),
                     'returned_plus_recovery_tokens':total_tokens,
                     'reduction_pct':round(100*(1-total_tokens/raw_tokens),2) if raw_tokens else None,
                     'baseline_bytes':len(baseline.encode()),'returned_bytes':len(reduced.encode()),
                     'local_elapsed_ms':round(elapsed_ms,3), 'checks':assertions,
                     'baseline_sha256':hashlib.sha256(baseline.encode()).hexdigest(),
                     'returned_sha256':hashlib.sha256(reduced.encode()).hexdigest()})

    with tempfile.TemporaryDirectory(prefix='leancontext-bench-') as tmp:
        work = Path(tmp)
        store = Store(ROOT, work/'state')

        # A real execution of this package's complete tests; no baseline rerun.
        r = run_command([sys.executable,'-m','unittest','discover','-v'],
                        store=store,cwd=ROOT,timeout=120,max_chars=6000)
        if r.exit_code:
            raise RuntimeError('Package tests failed; refusing to publish benchmark success')
        raw = raw_process_text(r,store)
        assert 'OK' in r.text and store.read_bytes(r.stderr_ref)
        add('Package test suite (verbose)', 'executed_real_project_command', raw, r.text,
            assertions=['exit_code=0','test completion retained','raw streams recoverable'],
            elapsed_ms=r.elapsed_seconds*1000)

        # Generated executable tests are a stress fixture, not representative user tasks.
        fixture = work/'suite.py'
        fixture.write_text('import unittest\nclass Workload(unittest.TestCase):\n' +
                           ''.join(f'    def test_{i:04d}(self): self.assertEqual({i}, {i if i!=147 else 999})\n'
                                   for i in range(350)) +
                           '\nif __name__ == "__main__": unittest.main(verbosity=2)\n')
        r = run_command([sys.executable,str(fixture)],store=store,cwd=work,timeout=30,max_chars=6000)
        raw = raw_process_text(r,store)
        assert r.exit_code == 1 and 'test_0147' in r.text and '147 != 999' in r.text
        recovery = recall(store,r.stderr_ref,query='AssertionError',limit=3)['text']
        add('350 executed tests, one failure', 'executed_generated_stress_fixture', raw, r.text,
            assertions=['exit_code=1','failing test retained','assertion values retained','raw recovery exact'],
            elapsed_ms=r.elapsed_seconds*1000,recovery=recovery)

        # Deliberately include a bad-fit small command; no hidden filtering of results.
        r = run_command([sys.executable,'-c','print("3 passed in 0.01s")'],
                        store=store,cwd=work,max_chars=6000)
        add('Short successful output (bad fit)', 'executed_small_command_counterexample',
            raw_process_text(r,store),r.text,assertions=['small output unchanged inside wrapper'],
            elapsed_ms=r.elapsed_seconds*1000)

        logs = ('\n'.join(f'WARNING build/unit_{i}.cc:12: deprecated API; continue compiling' for i in range(1600))+
                '\nFATAL: undefined reference storage_commit at writer.cc:73\nBUILD FAILED\n')
        start = time.perf_counter()
        view = compact(logs,store=store,max_chars=6000)
        duration = (time.perf_counter()-start)*1000
        assert 'undefined reference storage_commit' in view.text and store.read(view.ref) == logs
        recovery = recall(store,view.ref,query='FATAL',limit=2)['text']
        add('Warning-heavy compiler transcript', 'synthetic_stress_transcript', logs, view.text,
            assertions=['fatal error prioritized','omission notice present','full artifact exact'],
            elapsed_ms=duration,recovery=recovery)

        # Real source task: locate run_command and inspect its opening validation.
        source = (ROOT/'leancontext'/'runner.py').read_text()
        tree=ast.parse(source)
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_command')
        repo=Repository(ROOT)
        start=time.perf_counter()
        outline=render_result(repo.outline('leancontext/runner.py'))
        exact=render_result(repo.read('leancontext/runner.py',start=fn.lineno,end=fn.lineno+17))
        reduced=outline+'\n'+exact
        assert 'def run_command' in exact and 'timeout' in exact
        add('Locate command limits in real source', 'full_file_vs_outline_and_exact_range', source, reduced,
            assertions=['source lines exact','outline + range overhead included', 'baseline is an explicit full-file read'],
            elapsed_ms=(time.perf_counter()-start)*1000)

        # Time only local transformation, no model or subprocess time conflation.
        timings=[]
        for _ in range(5):
            started=time.perf_counter()
            compact(logs,store=store,max_chars=6000)
            timings.append((time.perf_counter()-started)*1000)
        artifact_stats=store.stats()

    schema=json.dumps(TOOL,ensure_ascii=False,separators=(',',':'))
    positive=[r for r in rows if r['category']!='executed_small_command_counterexample']
    total_base=sum(r['baseline_tokens'] for r in rows)
    total_return=sum(r['returned_plus_recovery_tokens'] for r in rows)
    report={
        'benchmark':'LeanContext local payload microbenchmark', 'version':1,
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'tokenizer':{'library':'tiktoken','version':tiktoken.__version__,'encoding':enc.name,
                     'claim':'Exact counts in this named encoding; not provider billing or model-specific quota.'},
        'default_output_budget_chars':6000,
        'methodology':['Commands execute once; original stdout then stderr bytes supply the baseline (no added stream headings; interleaving unknown).',
                       'Returned text includes status, omission markers and artifact references.',
                       'Failure stress rows include one literal recovery response in treatment tokens.',
                       'Real source comparison explicitly uses full file vs outline plus selected source range.',
                       'No autonomous agent, frontier model, local LLM, task success A/B, or latency A/B was run.',
                       'Original protocol envelopes and tool call argument tokens are excluded from per-row counts.',
                       'MCP schema is reported separately; prompts/instructions, retries and future context replays are not measured.',
                       'Aggregate is descriptive of this artificial mix; it must not be extrapolated to real sessions.'],
        'rows':rows,
        'aggregate_all_rows':{'baseline_tokens':total_base,'returned_plus_recovery_tokens':total_return,
                              'reduction_pct':round(100*(1-total_return/total_base),2)},
        'mcp_tool_schema_tokens':count(schema),
        'local_compaction_ms':{'samples':len(timings),'median':round(statistics.median(timings),3),
                              'min':round(min(timings),3),'max':round(max(timings),3)},
        'provider_tokens_saved':None,'subscription_quota_saved':None,'agent_task_latency_saved':None,
        'quality_scope':'Local extraction/recovery assertions and package tests only; no end-to-end equivalence claim.'}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'output':str(args.output),'rows':rows,
                      'schema_tokens':report['mcp_tool_schema_tokens'],
                      'local_compaction_ms':report['local_compaction_ms']},indent=2))


if __name__=='__main__':
    main()
