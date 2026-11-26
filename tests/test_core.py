import os
from pathlib import Path
import tempfile
import unittest

from leancontext.store import Store
from leancontext.compact import compact


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root, self.root / 'state')

    def test_small_output_unchanged(self):
        text = '3 passed in 0.12s\n'
        view = compact(text, store=self.store)
        self.assertEqual(text, view.text)
        self.assertFalse(view.changed)

    def test_error_in_middle_retained_and_every_byte_recoverable(self):
        lines = [f'test_{i:05d} PASSED' for i in range(1500)]
        lines[731:734] = ['FAIL tests/test_orders.py::test_roundtrip',
                          'E AssertionError: expected 7, received 8',
                          'src/orders.py:42: mismatch at rounding boundary']
        text = '\n'.join(lines) + '\n'
        view = compact(text, store=self.store, max_chars=2400)
        self.assertTrue(view.changed)
        self.assertLessEqual(len(view.text), 2400)
        self.assertIn('test_roundtrip', view.text)
        self.assertIn('expected 7, received 8', view.text)
        self.assertIn('src/orders.py:42', view.text)
        self.assertIn('omitted', view.text)
        self.assertEqual(text, self.store.read(view.ref))

    def test_unicode_long_line_is_explicitly_partial_and_recoverable(self):
        text = '警告 🚀 ' * 10000
        view = compact(text, store=self.store, max_chars=700)
        self.assertLessEqual(len(view.text), 700)
        self.assertIn('excerpt', view.text.lower())
        self.assertEqual(text, self.store.read(view.ref))

    def test_warnings_do_not_crowd_out_fatal_error(self):
        text = '\n'.join(f'WARNING: dependency {i} uses a deprecated API' for i in range(800))
        text += '\nFATAL: linker undefined reference storage_commit at writer.cc:73\n'
        view = compact(text, store=self.store, max_chars=1800)
        self.assertIn('undefined reference storage_commit', view.text)
        self.assertIn('diagnostic lines omitted', view.text)

    def test_storage_is_private_and_references_cannot_escape(self):
        ref = self.store.save('private log', metadata={'exit_code': 1})
        self.assertEqual(len(ref), 24)
        self.assertEqual(self.store.info(ref)['metadata']['exit_code'], 1)
        if os.name == 'posix':
            self.assertEqual(self.store.base.stat().st_mode & 0o777, 0o700)
            for f in self.store.base.rglob('*'):
                if f.is_file():
                    self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            self.store.read('../../secret')

    def test_bytes_roundtrip_and_events(self):
        raw = b'\xff\xfe\x00\r\nraw\x1b[31m'
        ref = self.store.save_bytes(raw, kind='stdout')
        self.assertEqual(self.store.read_bytes(ref), raw)
        self.store.record({'original_chars': 100, 'emitted_chars': 20})
        self.assertEqual(self.store.stats()['events'], 1)

    def test_batch_diagnostic_recall_counts_as_one_recovery_call(self):
        from leancontext.recall import recall, recall_many
        ref = self.store.save('a.c:1:2: error: missing a\n'
                              'b.c:3:4: error: missing b\n')
        recalled = recall_many(self.store, ref, selectors=['D1', 'D2'], strategy='compiler')
        self.assertEqual(recalled['selected_ids'], ['D1', 'D2'])
        self.assertEqual(self.store.stats()['recovery_calls'], 1)
        recall(self.store, ref)
        self.assertEqual(self.store.stats()['recovery_calls'], 2)

    def test_summary_never_expands_input_and_bad_budget_rejected(self):
        for size in (0, 30, 511, 700, 1200):
            text = 'x\n' * size
            v = compact(text, store=self.store, max_chars=600)
            self.assertLessEqual(len(v.text), len(text))
            self.assertLessEqual(len(v.text), 600)
        with self.assertRaises(ValueError):
            compact('x', store=self.store, max_chars=10)


if __name__ == '__main__':
    unittest.main()
