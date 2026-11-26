"""Command receipts distinguish invocations that share raw bytes."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from leancontext.runner import run_batch, run_command
from leancontext.store import Store


class CaptureProvenanceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.store = Store(root, root / 'state')

    def manifest(self, result):
        self.assertIsNotNone(result.capture_ref)
        self.assertIn('capture=' + result.capture_ref, result.text)
        raw = self.store.read_bytes(result.capture_ref)
        self.assertEqual(hashlib.sha256(raw).hexdigest()[:24], result.capture_ref)
        receipt = json.loads(raw)
        self.assertEqual(receipt['leancontext_capture'], 1)
        for role in ('stdout', 'stderr', 'result'):
            ref = getattr(result, role + '_ref')
            self.assertEqual(receipt[role + '_ref'], ref)
            self.assertEqual(receipt[role + '_sha256'],
                             hashlib.sha256(self.store.read_bytes(ref)).hexdigest())
        self.assertEqual(receipt['exit_code'], result.exit_code)
        self.assertEqual(receipt['diagnostic_ref'], result.diagnostic_ref)
        return receipt

    def run_code(self, code, **kwargs):
        return run_command([sys.executable, '-c', code], store=self.store,
                           strategy='generic', **kwargs)

    def test_same_bytes_different_invocations(self):
        first = self.run_code('print("same")')
        before = self.manifest(first)
        second = self.run_code('print("same"); raise SystemExit(7)')
        after = self.manifest(second)
        self.assertEqual(first.result_ref, second.result_ref)
        self.assertNotEqual(first.capture_ref, second.capture_ref)
        self.assertNotEqual(before['argv'], after['argv'])
        self.assertEqual((before['exit_code'], after['exit_code']), (0, 7))
        self.assertEqual(self.manifest(first), before)

    def test_empty_stream_roles(self):
        result = self.run_code('pass')
        self.assertEqual(result.stdout_ref, result.stderr_ref)
        receipt = self.manifest(result)
        self.assertEqual(receipt['stdout_ref'], receipt['stderr_ref'])
        self.assertEqual(self.store.read_bytes(receipt['stdout_ref']), b'')

    def test_parallel_identical_commands(self):
        cmd = [sys.executable, '-c', 'print("same")']
        results = run_batch([cmd] * 4, store=self.store, jobs=4, strategy='generic')
        receipts = [self.manifest(result) for result in results]
        self.assertEqual(len({result.result_ref for result in results}), 1)
        self.assertEqual(len({result.capture_ref for result in results}), 4)
        self.assertEqual(len({receipt['capture_id'] for receipt in receipts}), 4)
        self.assertEqual(self.store.stats()['events'], 4)

    def test_timeout_receipt_is_not_success(self):
        result = self.run_code('import time; time.sleep(10)', timeout=0.25)
        receipt = self.manifest(result)
        self.assertTrue(receipt['timed_out'])
        self.assertEqual(receipt['exit_code'], 124)
        self.assertIn('INCOMPLETE capture', result.text)

    def test_output_cap_receipt_is_not_success(self):
        result = self.run_code('print("x" * 10000)', max_output_bytes=32)
        receipt = self.manifest(result)
        self.assertTrue(receipt['output_limited'])
        self.assertEqual(receipt['exit_code'], 125)
        self.assertLessEqual(result.stdout_bytes + result.stderr_bytes, 32)

    def test_capture_reference_is_accounted_for(self):
        result = self.run_code('print("x" * 10000)', max_chars=1200)
        self.manifest(result)
        self.assertEqual(result.emitted_chars, len(result.text))
        self.assertEqual(self.store.stats()['emitted_chars'], len(result.text))
        self.assertEqual(result.budget_exceeded, len(result.text) > 1200)
