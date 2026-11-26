"""Real pytest regressions; optional dev dependency, stdlib suite stays runnable."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

from benchmarks.pytest_fixture import execute_fixture, expected_ids, fixture_traceback
from leancontext.compact import compact
from leancontext.store import Store


@unittest.skipUnless(importlib.util.find_spec('pytest'), 'optional pytest fixture dependency is unavailable')
class SemanticFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='leancontext-semantic-test-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.raw = {}
        for mode in ('grouped', 'distinct', 'success'):
            result = execute_fixture(cls.root / mode, mode)
            expected_exit = 0 if mode == 'success' else 1
            if result.returncode != expected_exit:
                raise AssertionError(f'Fixture {mode} exited {result.returncode}: {result.stdout}\n{result.stderr}')
            cls.raw[mode] = result.stdout + result.stderr

    def setUp(self):
        self.store = Store(self.root, self.root / self.id().rsplit('.', 1)[-1])

    def test_all_25_ids_and_three_complete_tracebacks_survive(self):
        raw = self.raw['grouped']
        self.assertIn('25 failed, 202 passed', raw)
        view = compact(raw, store=self.store, max_chars=6000, kind='command-result',
                       metadata={'exit_code': 1, 'argv': ['pytest']})
        missing = [nodeid for nodeid in expected_ids() if nodeid not in view.text]
        self.assertEqual(missing, [], 'Every failing node ID must survive the compacted report')
        from leancontext.pytest_parser import parse_pytest
        report = parse_pytest(raw)
        self.assertIsNotNone(report)
        self.assertEqual(len(report.diagnostics), 25)
        self.assertTrue(report.inventory_complete)
        representatives = {}
        for diagnostic in report.diagnostics:
            self.assertTrue(diagnostic.signature, diagnostic.label)
            representatives.setdefault(diagnostic.signature, diagnostic.traceback)
        self.assertEqual(len(representatives), 3)
        for trace in representatives.values():
            self.assertTrue(trace)
            self.assertIn(trace, view.text, 'A representative traceback must remain whole')
        for nodeid in (expected_ids()[i] for i in (0, 10, 18)):
            expected_trace = fixture_traceback(raw, nodeid)
            self.assertIn(expected_trace, view.text,
                          'The complete raw fixture traceback, not just the parser selection, must survive')
        self.assertEqual(self.store.read(view.ref), raw)

    def test_distinct_messages_preserve_25_groups_and_exceed_target_honestly(self):
        raw = self.raw['distinct']
        view = compact(raw, store=self.store, max_chars=1200, kind='command-result',
                       metadata={'exit_code': 1, 'argv': ['pytest']})
        missing = [nodeid for nodeid in expected_ids('distinct') if nodeid not in view.text]
        self.assertEqual(missing, [])
        self.assertTrue(view.budget_exceeded)
        self.assertGreater(len(view.text), 1200)
        from leancontext.pytest_parser import parse_pytest
        report = parse_pytest(raw)
        self.assertEqual(len({d.signature for d in report.diagnostics}), 25)
        for diagnostic in report.diagnostics:
            self.assertIn(diagnostic.traceback, view.text)
        for nodeid in expected_ids('distinct'):
            self.assertIn(fixture_traceback(raw, nodeid), view.text)

    def test_batch_recall_returns_whole_selected_failure_details(self):
        from leancontext.pytest_parser import parse_pytest
        from leancontext.recall import recall_many
        raw = self.raw['grouped']
        view = compact(raw, store=self.store, max_chars=6000, kind='command-result',
                       metadata={'exit_code': 1, 'argv': ['pytest']})
        report = parse_pytest(raw)
        chosen = [report.diagnostics[i] for i in (0, 10, 18)]
        recalled = recall_many(self.store, view.diagnostic_ref, selectors=[d.id for d in chosen], max_chars=1200)
        for diagnostic in chosen:
            self.assertIn(diagnostic.detail, recalled['text'])
            self.assertIn(diagnostic.label, recalled['text'])
            self.assertIn(fixture_traceback(raw, diagnostic.label), recalled['text'])
        for message in ('Reading record fixture 0;', 'Loading account fixture 0;',
                        'Writing transaction fixture 0;'):
            self.assertIn(message, recalled['text'])
        self.assertTrue(recalled['budget_exceeded'])

    def test_success_strategy_retains_result_without_verbose_progress(self):
        raw = self.raw['success']
        view = compact(raw, store=self.store, max_chars=6000, kind='command-result',
                       metadata={'exit_code': 0, 'argv': ['pytest']})
        self.assertEqual(view.strategy, 'test-success')
        self.assertIn('202 passed', view.text)
        self.assertLess(len(view.text), len(raw) // 3)
        self.assertEqual(self.store.read(view.ref), raw)


if __name__ == '__main__':
    unittest.main()
