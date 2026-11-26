"""Regression for malformed and non-object event records."""
import tempfile
import unittest
from pathlib import Path

from leancontext.store import Store


class LedgerShapeTests(unittest.TestCase):
    def test_non_object_records_do_not_destroy_valid_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root, root / 'state')
            store.record({'operation': 'recall', 'original_chars': 40,
                          'emitted_chars': 10})
            for index, payload in enumerate(('[]', 'null', '42', '"text"', '{')):
                (store.base / 'events' / f'bad-{index}.json').write_text(
                    payload, encoding='utf-8')
            result = store.stats()
            self.assertEqual(result['events'], 1)
            self.assertEqual(result['original_chars'], 40)
            self.assertEqual(result['emitted_chars'], 10)
            self.assertEqual(result['recovery_calls'], 1)
            self.assertEqual(len(result['warnings']), 5)
            self.assertFalse(result['event_scan_complete'])

    def test_valid_ledger_remains_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root, root / 'state')
            store.record({'operation': 'run', 'original_chars': 15,
                          'emitted_chars': 12})
            result = store.stats()
            self.assertEqual(result['events'], 1)
            self.assertEqual(result['warnings'], [])
            self.assertTrue(result['event_scan_complete'])

    def test_symlink_is_reported_without_following_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root, root / 'state')
            outside = root / 'outside.json'
            outside.write_text('{"original_chars": 999}', encoding='utf-8')
            try:
                (store.base / 'events' / 'link.json').symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest('Symlink creation unavailable')
            result = store.stats()
            self.assertEqual(result['events'], 0)
            self.assertEqual(result['original_chars'], 0)
            self.assertEqual(len(result['warnings']), 1)
            self.assertFalse(result['event_scan_complete'])
