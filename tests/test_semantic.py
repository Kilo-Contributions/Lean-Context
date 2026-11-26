"""Evidence-retention regressions across compression, recall, and host boundaries."""
from pathlib import Path
import sys
import tempfile
import unittest

from leancontext.compact import compact
from leancontext.diagnostic_types import Diagnostic, DiagnosticReport
from leancontext.store import Store


def pytest_output(count=25):
    lines = ['============================= test session starts ==============================',
             'platform linux -- Python 3.12, pytest-9.2.0, pluggy-1.6.0',
             f'collected {count + 202} items']
    lines.extend(f'tests/test_pass.py::test_pass[{i}] PASSED [ 80%]' for i in range(202))
    lines.extend(['=================================== FAILURES ==================================='])
    for i in range(count):
        lines.extend([f'__________________________ test_bad[{i}] __________________________',
                      '', '    def test_bad(case):', '>       explode()', '',
                      'tests/test_bad.py:8: in test_bad', '    explode()',
                      'helpers.py:4: in explode', '    raise ValueError("broken")',
                      'E   ValueError: broken', '',
                      '----------------------------- Captured stdout call -----------------------------',
                      f'case {i} ' + ('debug ' * 200)])
    lines.append('=========================== short test summary info ============================')
    lines.extend(f'FAILED tests/test_bad.py::test_bad[{i}] - ValueError: broken' for i in range(count))
    lines.append(f'======================= {count} failed, 202 passed in 0.12s =======================')
    return '\n'.join(lines) + '\n'


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root, self.root/'state')

    def test_twenty_five_failure_ids_survive_a_small_target(self):
        raw = pytest_output()
        view = compact(raw, store=self.store, max_chars=1200)
        missing = [i for i in range(25) if f'tests/test_bad.py::test_bad[{i}]' not in view.text]
        self.assertEqual(missing, [], 'semantic compression may not drop failure identities')
        self.assertEqual(view.strategy, 'pytest-failures')
        self.assertTrue(view.budget_exceeded)
        self.assertIn('target', view.text)
        self.assertEqual(self.store.read(view.ref), raw)

    def test_render_keeps_complete_representatives_and_no_captured_log_duplicates(self):
        from leancontext.diagnostics import render_report
        trace = '\n'.join(f'  full frame {i}' for i in range(60)) + '\nValueError: broken\n'
        records = tuple(Diagnostic(f'F{i}', f'tests/a.py::test[{i}]', 'FAILED',
                                  f'{trace}captured LOG {i}\n', trace, 'ValueError: broken',
                                  'call', 'same-signature') for i in range(1, 26))
        report = DiagnosticReport('pytest-failures', '25 failed', records,
                                  inventory_complete=True, expected_count=25)
        result = render_report(report, ref='a'*24, max_chars=512)
        self.assertEqual(result['group_count'], 1)
        self.assertEqual(result['diagnostic_count'], 25)
        self.assertIn(trace, result['text'])
        self.assertEqual(result['text'].count(trace), 1)
        self.assertNotIn('captured LOG', result['text'])
        self.assertTrue(result['budget_exceeded'])

    def test_signature_grouping_also_checks_phase_message_and_status(self):
        from leancontext.diagnostics import group_diagnostics
        items = (Diagnostic('F1', 'a', 'FAILED', 'trace', 'trace', 'one', 'call', 'same'),
                 Diagnostic('F2', 'b', 'FAILED', 'trace', 'trace', 'two', 'call', 'same'),
                 Diagnostic('F3', 'c', 'ERROR', 'trace', 'trace', 'one', 'setup', 'same'),
                 Diagnostic('F4', 'd', 'FAILED', '', signature=None),
                 Diagnostic('F5', 'e', 'FAILED', '', signature=None))
        self.assertEqual(len(group_diagnostics(items)), 5)

    def test_batch_recall_returns_each_requested_full_detail_once(self):
        from leancontext.recall import recall_many
        raw = pytest_output()
        view = compact(raw, store=self.store, max_chars=6000)
        result = recall_many(self.store, view.ref,
                             selectors=['F1', 'tests/test_bad.py::test_bad[24]', 'F1'],
                             max_chars=512)
        self.assertEqual(result['selected_ids'], ['F1', 'F25'])
        self.assertIn('case 0 ' + 'debug '*200, result['text'])
        self.assertIn('case 24 ' + 'debug '*200, result['text'])
        self.assertEqual(result['text'].count('case 0 '), 1)
        self.assertTrue(result['budget_exceeded'])
        missing = recall_many(self.store, view.ref, selectors=['F404', 'F2'])
        self.assertEqual(missing['missing'], ['F404'])
        self.assertIn('case 1 ', missing['text'])

    def test_mcp_does_not_retruncate_semantic_index_or_batch_details(self):
        from leancontext.mcp import dispatch
        raw = pytest_output()
        ref = self.store.save(raw, metadata={'argv': ['pytest'], 'exit_code': 1})
        for op, extra, expected in [('diagnostics', {}, 'tests/test_bad.py::test_bad[24]'),
                                    ('recall_many', {'selectors': ['F1', 'F25']}, 'case 24 ')]:
            response = dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                 'params': {'name': 'context', 'arguments':
                                            {'op': op, 'ref': ref, 'max_chars': 512, **extra}}}, self.store)
            self.assertNotIn('isError', response['result'])
            text = response['result']['content'][0]['text']
            self.assertIn(expected, text)
            self.assertIn('target', text)

    def test_source_hint_prevents_diagnostic_rewriting(self):
        raw = 'src/notes.py:10: error: example string\n' + 'src/notes.py:11: print("ok")\n'*500
        view = compact(raw, store=self.store, max_chars=1200,
                       metadata={'argv': ['rg', '-n', '.', 'src/notes.py']})
        self.assertEqual(view.strategy, 'source')
        self.assertLessEqual(len(view.text), 1200)
        self.assertIn('src/notes.py:10: error: example string', view.text)
        self.assertNotIn('signature groups', view.text)

    def test_unittest_success_keeps_completion_and_reports_omissions(self):
        raw = ''.join(f'test_{i} (suite.Tests.test_{i}) ... ok\n' for i in range(202))
        raw += '\n' + '-'*70 + '\nRan 202 tests in 0.25s\n\nOK\n'
        view = compact(raw, store=self.store, max_chars=1200,
                       metadata={'argv': [sys.executable, '-m', 'unittest'], 'exit_code': 0})
        self.assertEqual(view.strategy, 'test-success')
        self.assertIn('Ran 202 tests in 0.25s', view.text)
        self.assertIn('OK', view.text)
        self.assertIn('omitted', view.text)
        self.assertEqual(self.store.read(view.ref), raw)

    def test_worker_draft_and_explicit_generic_keep_a_hard_budget(self):
        raw = pytest_output()
        for options in ({'kind': 'worker-draft'}, {'strategy': 'generic'}):
            view = compact(raw, store=self.store, max_chars=1200, **options)
            self.assertLessEqual(len(view.text), 1200)
            self.assertEqual(view.strategy, 'generic')

    def test_incomplete_capture_never_claims_complete_inventory(self):
        from leancontext.diagnostics import diagnose
        ref = self.store.save(pytest_output(), metadata={'output_limited': True})
        result = diagnose(self.store, ref, max_chars=6000)
        self.assertIs(result['inventory_complete'], False)
        self.assertIn('INCOMPLETE', result['text'])

    def test_runner_keeps_status_and_semantic_metadata_and_can_force_generic(self):
        from leancontext.runner import run_command
        (self.root/'output.txt').write_text(pytest_output())
        command = [sys.executable, '-c',
                   'import pathlib,sys; print(pathlib.Path("output.txt").read_text()); sys.exit(1)']
        result = run_command(command, store=self.store, max_chars=1200)
        self.assertEqual(result.exit_code, 1)
        self.assertIn('tests/test_bad.py::test_bad[24]', result.text)
        self.assertIn('tests/test_bad.py::test_bad[0]', result.text)
        self.assertTrue(result.budget_exceeded)
        self.assertEqual(result.diagnostic_count, 25)
        self.assertIsNotNone(result.diagnostic_ref)
        generic = run_command(command, store=self.store, max_chars=1200, strategy='generic')
        self.assertLessEqual(len(generic.text), 1200)

    def test_cli_batch_recall_and_diagnostics_match_mcp(self):
        from contextlib import redirect_stdout
        import io
        from leancontext.cli import main
        ref = self.store.save(pytest_output())
        for action, extras in [('diagnostics', []), ('recall-many', ['F1', 'F25'])]:
            output = io.StringIO()
            with redirect_stdout(output):
                code = main([action, ref, *extras, '--root', str(self.root),
                             '--state-dir', str(self.store.base), '--max-chars', '512'])
            self.assertEqual(code, 0)
            self.assertIn('tests/test_bad.py::test_bad[24]', output.getvalue())
            self.assertIn('target', output.getvalue())

    def test_explicit_strategy_survives_archive_recall(self):
        from leancontext.recall import recall_many
        view = compact('unit.cc:8:2: error: incompatible value\n  int x = value;\n  ^\n',
                       store=self.store, strategy='compiler',
                       metadata={'argv': ['cat', 'saved-compiler-log.txt']})
        self.assertEqual(view.strategy, 'compiler')
        recalled = recall_many(self.store, view.ref, selectors=['D1'])
        self.assertIn('int x = value;', recalled['text'])

    def test_diagnostic_snapshot_survives_identical_output_from_another_command(self):
        from leancontext.recall import recall_many
        raw = 'unit.cc:8:2: error: incompatible value\n  int x = value;\n  ^\n'
        first = compact(raw, store=self.store, strategy='compiler',
                        metadata={'argv': ['compiler'], 'output_limited': True})
        self.store.save(raw, metadata={'argv': ['cat', 'output.txt'], 'output_limited': False})
        result = recall_many(self.store, first.diagnostic_ref, selectors=['D1'])
        self.assertIn('int x = value;', result['text'])
        self.assertIs(result['inventory_complete'], False)
        self.assertIn('INCOMPLETE', result['text'])

    def test_successful_nested_tests_do_not_hide_compiler_errors(self):
        raw = '\n'.join(f'unit_{i}.cc:8:2: error: incompatible value {i}\n  ^' for i in range(25))
        raw += '\n================ test session starts ================\n'
        raw += 'collected 1 item\nexample.py . [100%]\n================ 1 passed in 0.1s ================\n'
        view = compact(raw, store=self.store, max_chars=1200, metadata={'exit_code': 1})
        self.assertEqual(view.strategy, 'compiler')
        for i in range(25):
            self.assertIn(f'unit_{i}.cc:8:2', view.text)

    def test_git_source_intent_with_global_options_precedes_compiler_detection(self):
        raw = 'unit.cc:8:2: error: documentation example\n' * 100
        view = compact(raw, store=self.store, max_chars=1200,
                       metadata={'argv': ['git', '-C', '/tmp/project', '-c', 'color.ui=false', 'show', 'HEAD:doc.txt']})
        self.assertEqual(view.strategy, 'source')
        self.assertLessEqual(len(view.text), 1200)

    def test_malformed_nested_snapshot_is_tool_error_and_next_request_works(self):
        import io
        import json
        from leancontext.mcp import serve
        raw = '{"leancontext_diagnostics":1,"raw_ref":"' + 'a'*24 + '","report":' + '['*20000 + '0' + ']'*20000 + '}'
        ref = self.store.save(raw)
        messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                     'params': {'name': 'context', 'arguments': {'op': 'diagnostics', 'ref': ref}}},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'ping'}]
        output = io.StringIO()
        serve(self.store, io.StringIO('\n'.join(json.dumps(m) for m in messages) + '\n'), output)
        results = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(results[0]['result']['isError'])
        self.assertEqual(results[1]['result'], {})


if __name__ == '__main__':
    unittest.main()
