"""Command-line interface shared by the package, zipapp, and host integrations."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import __version__
from .store import Store


def _location(parser):
    parser.add_argument('--root', type=Path, default=Path.cwd(), help='Project directory (default: current directory)')
    parser.add_argument('--state-dir', type=Path, help='Dedicated artifact directory; default is private per-project cache')


def _runner_options(parser):
    _location(parser)
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--max-chars', type=int, default=6000,
                        help='Returned text target (minimum 1200); semantic diagnostics keep all IDs/whole representative traces even when larger')
    parser.add_argument('--strategy', choices=['auto', 'pytest', 'compiler', 'source', 'generic'], default='auto',
                        help='Adaptive output format; generic restores the hard-bounded excerpt')
    parser.add_argument('--max-output-mb', type=float, default=64, help='Kill on raw capture cap; report incomplete output')
    parser.add_argument('--json', action='store_true', help='Machine-readable result including usage and archive IDs')


def make_parser():
    p = argparse.ArgumentParser(prog='leancontext', description='Local evidence budgets for Codex and Claude Code. Zero model calls by default.')
    p.add_argument('--version', action='version', version=f'LeanContext {__version__}')
    sub = p.add_subparsers(dest='action', required=True)
    run = sub.add_parser('run', help='Capture a verbose command once; keep its exit code and full raw streams')
    _runner_options(run)
    run.add_argument('argv', nargs=argparse.REMAINDER, help='-- executable arg ...; use bash -lc explicitly for shell syntax')
    batch = sub.add_parser('batch', help='Run a JSON array of argv arrays and return bounded results')
    _runner_options(batch)
    batch.add_argument('manifest', type=Path)
    batch.add_argument('--jobs', type=int, default=1, help='1–8; parallel jobs must be independent')
    recall = sub.add_parser('recall', help='Retrieve archived output without rerunning the command')
    _location(recall)
    recall.add_argument('ref')
    recall.add_argument('--start', type=int, default=1)
    recall.add_argument('--lines', type=int, default=80)
    recall.add_argument('--offset', type=int, default=0, help='Character offset within --start line')
    recall.add_argument('--query', help='Literal text to find inside the artifact')
    recall.add_argument('--max-chars', type=int, default=6000)
    recall.add_argument('--raw', action='store_true', help='Emit exact original bytes with no limit; intended for file redirection')
    diagnostics = sub.add_parser('diagnostics', help='Index archived diagnostic output without rerunning it')
    many = sub.add_parser('recall-many', help='Recall whole failure details in one call by F/D IDs, G representatives, or exact test identifiers')
    for diagnostic_parser in (diagnostics, many):
        _location(diagnostic_parser)
        diagnostic_parser.add_argument('ref')
        diagnostic_parser.add_argument('--max-chars', type=int, default=6000, help='Soft target; complete requested evidence takes priority')
        diagnostic_parser.add_argument('--strategy', choices=['auto', 'pytest', 'compiler'], default='auto')
        diagnostic_parser.add_argument('--json', action='store_true')
    many.add_argument('selectors', nargs='+')
    context = sub.add_parser('context', help='Read-only exact source retrieval')
    _location(context)
    context.add_argument('--json', action='store_true')
    ops = context.add_subparsers(dest='operation', required=True)
    files = ops.add_parser('files')
    files.add_argument('--glob', default='*')
    files.add_argument('--limit', type=int, default=100)
    search = ops.add_parser('search')
    search.add_argument('query')
    search.add_argument('--glob', default='*')
    search.add_argument('--limit', type=int, default=20)
    read = ops.add_parser('read')
    read.add_argument('path')
    read.add_argument('--start', type=int, default=1)
    read.add_argument('--end', type=int)
    read.add_argument('--max-chars', type=int, default=6000)
    outline = ops.add_parser('outline')
    outline.add_argument('path')
    server = sub.add_parser('serve', help='Run the read-only MCP server over stdio')
    _location(server)
    setup = sub.add_parser('setup', help='Configure one project; preserve existing settings and make backups')
    setup.add_argument('--root', type=Path, default=Path.cwd())
    setup.add_argument('--agent', choices=['codex','claude','both'], default='both')
    setup.add_argument('--hooks', action='store_true', help='Opt in to supported successful Claude Bash output filtering')
    setup.add_argument('--dry-run', action='store_true')
    hook = sub.add_parser('hook', help='Host adapter; expects hook JSON on stdin')
    hook.add_argument('host', choices=['claude'])
    hook.add_argument('--max-chars', type=int, default=6000)
    hook.add_argument('--state-dir', type=Path)
    stats = sub.add_parser('stats', help='Local payload accounting; not provider cost or quota')
    _location(stats)
    audit = sub.add_parser('audit', help='Read explicit Codex/Claude session JSONL paths')
    audit.add_argument('paths', nargs='+', type=Path)
    compare = sub.add_parser('compare', help='Compare two audit JSON reports; never infer quota or money')
    compare.add_argument('before', type=Path)
    compare.add_argument('after', type=Path)
    worker = sub.add_parser('worker', help='Opt-in, bounded read-only request to an explicitly configured cheaper/local model')
    _location(worker)
    worker.add_argument('task')
    worker.add_argument('--config', type=Path, required=True)
    worker.add_argument('--file', action='append', dest='files', required=True)
    worker.add_argument('--max-input-chars', type=int)
    worker.add_argument('--json', action='store_true')
    doctor = sub.add_parser('doctor', help='Inspect compatibility without making any model calls')
    doctor.add_argument('--json', action='store_true')
    prune = sub.add_parser('prune', help='Explicitly delete old local archives (dry-run unless --apply)')
    _location(prune)
    prune.add_argument('--days', type=float, default=30)
    prune.add_argument('--apply', action='store_true')
    prune.add_argument('--quiescent', action='store_true', help='Confirm all store readers and writers are stopped; required with --apply')
    return p


def _json(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _launcher() -> list[str]:
    current = Path(sys.argv[0])
    if current.suffix == '.pyz':
        return [sys.executable, str(current.resolve())]
    # Package installation uses its own interpreter. Source use is supported by
    # an absolute launcher so starting an MCP process in a different cwd works.
    source_launcher = Path(__file__).resolve().parents[1] / 'leancontext_launcher.py'
    if source_launcher.is_file():
        return [sys.executable, str(source_launcher)]
    return [sys.executable, '-m', 'leancontext']


def _doctor() -> dict:
    results = {'leancontext':__version__, 'python':sys.version.split()[0],
               'stdlib_runtime':True, 'models_called':0, 'platform':sys.platform}
    for name in ('codex','claude','git'):
        exe = shutil.which(name)
        if not exe:
            results[name] = {'installed':False}
            continue
        try:
            r = subprocess.run([exe,'--version'], capture_output=True, text=True, timeout=5)
            results[name] = {'installed':True,'version':(r.stdout or r.stderr).strip()[:300], 'exit_code':r.returncode}
        except (OSError, subprocess.TimeoutExpired) as exc:
            results[name] = {'installed':True,'error':str(exc)}
    results['notes'] = ['Codex uses project MCP plus instructions; no universal result interception.',
                        'Claude optional Bash hook requires updatedToolOutput support (documented from 2.1.121).',
                        'A host/version check does not substitute for a live compatibility canary.',
                        'Subprocess capture is intended for noninteractive foreground commands.']
    return results


def main(argv=None) -> int:
    args = make_parser().parse_args(argv)
    try:
        if args.action == 'hook':
            # Hooks always fail open. Never emit a permission decision or noisy traceback.
            try:
                from .hooks import handle_claude_hook
                raw = sys.stdin.read(8*1024*1024+1)
                if len(raw)>8*1024*1024:
                    raise ValueError('Hook payload too large')
                result = handle_claude_hook(json.loads(raw), max_chars=args.max_chars, state_dir=args.state_dir)
            except Exception:
                result = {}
            _json(result)
            return 0
        if args.action == 'doctor':
            _json(_doctor())
            return 0
        if args.action == 'setup':
            from .install import setup_project
            result = setup_project(args.root, args.agent, _launcher(), hooks=args.hooks, dry_run=args.dry_run)
            if args.dry_run:
                _json(result)
            else:
                # Do not repeat every installed instruction in the model context.
                printable = dict(result)
                printable['files'] = [{k:v for k,v in f.items() if k != 'content'} for f in result.get('files',[])]
                _json(printable)
            return 0
        if args.action == 'audit':
            from .audit import audit_paths
            _json(audit_paths(args.paths))
            return 0
        if args.action == 'compare':
            from .audit import compare_reports
            _json(compare_reports(json.loads(args.before.read_text()), json.loads(args.after.read_text())))
            return 0
        store = Store(args.root, args.state_dir)
        if args.action in ('run', 'batch'):
            from .runner import run_batch, run_command
            kw = {'store':store, 'cwd':args.root, 'timeout':args.timeout,
                  'max_chars':args.max_chars, 'max_output_bytes':int(args.max_output_mb*1024*1024),
                  'strategy':args.strategy}
            if args.action == 'run':
                command = args.argv[1:] if args.argv and args.argv[0] == '--' else args.argv
                result = run_command(command, **kw)
                if args.json:
                    _json(asdict(result))
                else:
                    print(result.text, end='' if result.text.endswith('\n') else '\n')
                return result.exit_code
            commands = json.loads(args.manifest.read_text(encoding='utf-8'))
            if not isinstance(commands,list) or any(not isinstance(c,list) for c in commands):
                raise ValueError('Batch manifest must be a JSON array of argv arrays')
            results = run_batch(commands, jobs=args.jobs, **kw)
            if args.json:
                _json([asdict(r) for r in results])
            else:
                for i,r in enumerate(results,1):
                    print(f'Check {i}/{len(results)}\n{r.text}')
            return 1 if any(r.exit_code for r in results) else 0
        if args.action == 'recall':
            if args.raw:
                raw = store.read_bytes(args.ref)
                sys.stdout.buffer.write(raw)
                store.record({'operation':'recall','ref':args.ref,'emitted_bytes':len(raw)})
            else:
                from .recall import recall
                print(recall(store,args.ref,start=args.start,limit=args.lines,offset=args.offset,
                             query=args.query,max_chars=args.max_chars)['text'],end='')
            return 0
        if args.action in ('diagnostics', 'recall-many'):
            if args.action == 'diagnostics':
                from .diagnostics import diagnose
                result = diagnose(store, args.ref, max_chars=args.max_chars, strategy=args.strategy)
            else:
                from .recall import recall_many
                result = recall_many(store, args.ref, selectors=args.selectors,
                                     max_chars=args.max_chars, strategy=args.strategy)
            _json(result) if args.json else print(result['text'], end='')
            return 0
        if args.action == 'context':
            from .mcp import context_action, render_result
            parameters = {k:v for k,v in vars(args).items()
                          if k in ('path','query','glob','start','end','limit','max_chars') and v is not None}
            parameters['op'] = args.operation
            result = context_action(store,parameters)
            returned = render_result(result,parameters.get('max_chars',6000))
            store.record({'operation':'context_'+args.operation,'emitted_chars':len(returned),
                          'emitted_bytes':len(returned.encode('utf-8'))})
            _json(result) if args.json else print(returned)
            return 0
        if args.action == 'serve':
            from .mcp import serve
            serve(store)
            return 0
        if args.action == 'stats':
            _json(store.stats())
            return 0
        if args.action == 'worker':
            from .worker import ask_worker
            result = ask_worker(args.task,args.files,config_path=args.config,store=store,max_input_chars=args.max_input_chars)
            _json(result) if args.json else print(result['text'])
            return 0
        if args.action == 'prune':
            from .retention import prune
            _json(prune(store, args.days, apply=args.apply, quiescent=args.quiescent))
            return 0
        return 0
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        print('LeanContext interrupted.',file=sys.stderr)
        return 130
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        print(f'LeanContext: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
