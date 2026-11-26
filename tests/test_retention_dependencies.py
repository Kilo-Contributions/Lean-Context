"""Retention tests use real local archives and command captures."""
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from leancontext.diagnostic_types import DiagnosticReport
from leancontext.diagnostics import archive_report
from leancontext.retention import prune
from leancontext.runner import run_command
from leancontext.store import Store


class RetentionTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = Store(self.root, self.root / 'state')

    def age(self, *refs):
        old = time.time() - 40 * 86400
        for ref in refs:
            for ext in ('.gz', '.json'):
                os.utime(self.store._path(ref, ext), (old, old))

    def capture(self):
        return run_command([sys.executable, '-c', "print('actual diagnostic output')"],
                           store=self.store, strategy='generic', max_chars=1200)

    def test_live_capture_protects_old_streams(self):
        r = self.capture()
        self.age(r.stdout_ref, r.stderr_ref, r.result_ref)
        garbage = self.store.save('unreferenced')
        self.age(garbage)
        original = self.store.read_bytes(r.stdout_ref)
        dry = prune(self.store, 30)
        self.assertEqual(dry['artifacts'], 1)
        self.assertEqual(dry['protected_dependencies'], 3)
        self.assertEqual(self.store.read(garbage), 'unreferenced')
        actual = prune(self.store, 30, apply=True, quiescent=True)
        self.assertEqual(actual['artifacts'], 1)
        self.assertFalse(self.store._path(garbage, '.gz').exists())
        self.assertEqual(self.store.read_bytes(r.stdout_ref), original)
        self.assertEqual(json.loads(self.store.read(r.capture_ref))['stdout_sha256'], hashlib.sha256(original).hexdigest())

    def test_transitive_snapshot_and_mutable_metadata(self):
        raw = self.store.save('test log')
        snapshot = archive_report(self.store, DiagnosticReport('test-success', 'OK', ()), raw_ref=raw)
        # A byte-identical later save overwrites metadata kind and references.
        self.store.save_bytes(self.store.read_bytes(snapshot), kind='output')
        self.age(raw)
        self.assertEqual(prune(self.store, 30)['protected_dependencies'], 1)
        self.assertEqual(prune(self.store, 30, apply=True, quiescent=True)['artifacts'], 0)
        self.assertEqual(self.store.read(raw), 'test log')

    def test_transitive_legacy_dependencies(self):
        leaf = self.store.save('leaf')
        mid = self.store.save('middle', metadata={'raw_ref': leaf})
        self.store.save('parent', metadata={'result_ref': mid})
        self.age(leaf, mid)
        self.assertEqual(prune(self.store, 30)['protected_dependencies'], 2)

    def test_expired_capture_and_dependencies_can_be_removed(self):
        r = self.capture()
        refs = (r.capture_ref, r.stdout_ref, r.stderr_ref, r.result_ref)
        self.age(*refs)
        original_unlink = Path.unlink
        deleted = []
        def track(path, *args, **kwargs):
            if path.suffix == '.gz':
                deleted.append(path.stem)
            return original_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', track):
            report = prune(self.store, 30, apply=True, quiescent=True)
        self.assertEqual(report['artifacts'], 4)
        self.assertEqual(deleted[0], r.capture_ref)
        self.assertLess(deleted.index(r.result_ref), deleted.index(r.stdout_ref))
        self.assertEqual(list((self.store.base / 'artifacts').iterdir()), [])

    def test_apply_requires_offline_confirmation(self):
        ref = self.store.save('old')
        self.age(ref)
        with self.assertRaisesRegex(ValueError, 'quiescent'):
            prune(self.store, 30, apply=True)
        self.assertEqual(self.store.read(ref), 'old')

    def test_missing_referenced_evidence_holds_all_deletion(self):
        self.store.save('parent', metadata={'raw_ref': 'a'*24})
        ref = self.store.save('old')
        self.age(ref)
        with self.assertRaisesRegex(ValueError, 'missing evidence'):
            prune(self.store, 30, apply=True, quiescent=True)
        self.assertEqual(self.store.read(ref), 'old')

    def test_orphan_entry_holds(self):
        ref = self.store.save('old')
        self.age(ref)
        self.store._path(ref, '.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Unpaired'):
            prune(self.store, 30, apply=True, quiescent=True)
        self.assertEqual(self.store.read(ref), 'old')

    def test_corrupt_manifest_holds(self):
        r = self.capture()
        self.store._path(r.capture_ref, '.gz').write_bytes(b'not gzip')
        with self.assertRaises(OSError):
            prune(self.store, 30, apply=True, quiescent=True)
        self.assertTrue(self.store._path(r.stdout_ref, '.gz').exists())

    def test_live_capture_directory_holds(self):
        (self.store.base / 'capture-active').mkdir()
        with self.assertRaisesRegex(ValueError, 'Capture directory'):
            prune(self.store, 30, apply=True, quiescent=True)

    def test_cycle_holds(self):
        a = self.store.save('a')
        b = self.store.save('b', metadata={'raw_ref': a})
        self.store.save('a', metadata={'raw_ref': b})
        self.age(a, b)
        with self.assertRaisesRegex(ValueError, 'cycle'):
            prune(self.store, 30, apply=True, quiescent=True)

    def test_inventory_drift_holds(self):
        from leancontext import retention
        ref = self.store.save('old')
        self.age(ref)
        original = retention._dependencies
        def changed(*args):
            result = original(*args)
            self.store.save('newly added')
            return result
        with patch.object(retention, '_dependencies', changed):
            with self.assertRaisesRegex(ValueError, 'inventory changed'):
                prune(self.store, 30, apply=True, quiescent=True)
        self.assertEqual(self.store.read(ref), 'old')

    def test_symlink_holds(self):
        ref = self.store.save('old')
        path = self.store._path(ref, '.gz')
        path.unlink()
        outside = self.root / 'external'
        outside.write_bytes(b'private')
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest('symlink unavailable')
        with self.assertRaisesRegex(ValueError, 'linked'):
            prune(self.store, 30, apply=True, quiescent=True)
        self.assertEqual(outside.read_bytes(), b'private')
