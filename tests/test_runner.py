from pathlib import Path
import os
import sys
import tempfile
import time
import unittest

from leancontext.store import Store
from leancontext.runner import run_command, run_batch


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root, self.root / 'state')

    def test_exit_status_streams_and_single_execution(self):
        code = "from pathlib import Path; import sys; p=Path('count'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1'); print('out'); print('error detail',file=sys.stderr); sys.exit(7)"
        r = run_command([sys.executable, '-c', code], store=self.store, cwd=self.root)
        self.assertEqual(r.exit_code, 7)
        self.assertEqual((self.root / 'count').read_text(), '1')
        self.assertEqual(self.store.read(r.stdout_ref), 'out\n')
        self.assertEqual(self.store.read(r.stderr_ref), 'error detail\n')
        self.assertIn('exit=7', r.text)
        self.assertIn('error detail', r.text)

    def test_timeout_is_not_success(self):
        start = time.monotonic()
        r = run_command([sys.executable, '-c', 'import time; print("begin",flush=True); time.sleep(9)'],
                        store=self.store, cwd=self.root, timeout=0.15)
        self.assertEqual(r.exit_code, 124)
        self.assertTrue(r.timed_out)
        self.assertLess(time.monotonic()-start, 3)
        self.assertIn('timeout', r.text.lower())
        self.assertIn('begin', self.store.read(r.stdout_ref))

    def test_binary_output_exactly_recoverable(self):
        r = run_command([sys.executable, '-c', 'import os; os.write(1,b"\\xff\\x00data")'], store=self.store)
        self.assertEqual(self.store.read_bytes(r.stdout_ref), b'\xff\x00data')

    def test_output_limit_explicitly_reports_incomplete_capture(self):
        r = run_command([sys.executable, '-c', 'import sys; sys.stdout.write("x"*1000000)'],
                        store=self.store, max_output_bytes=2000)
        self.assertTrue(r.output_limited)
        self.assertEqual(r.exit_code, 125)
        self.assertLessEqual(len(self.store.read_bytes(r.stdout_ref)), 2000)
        self.assertIn('INCOMPLETE', r.text)

    @unittest.skipUnless(hasattr(os, 'fork'), 'POSIX process-group regression')
    def test_output_cap_kills_descendant_when_parent_already_exited(self):
        code = ('import os,time\n'
                'pid=os.fork()\n'
                'if pid == 0:\n'
                ' os.close(1); os.close(2); time.sleep(.3)\n'
                ' open("survivor", "w").write("unexpected")\n'
                ' os._exit(0)\n'
                'os.write(1,b"x"*4096); os._exit(0)\n')
        result = run_command([sys.executable,'-c',code],store=self.store,cwd=self.root,max_output_bytes=100)
        self.assertEqual(result.exit_code,125)
        time.sleep(.4)
        self.assertFalse((self.root/'survivor').exists(), 'capture cap left a live descendant')

    def test_noise_budget_includes_status_and_failure(self):
        code = 'import sys; print("ok\\n"*9000); print("AssertionError: lost ACK at worker.py:61",file=sys.stderr); sys.exit(1)'
        r = run_command([sys.executable, '-c', code], store=self.store, max_chars=1600)
        self.assertLessEqual(len(r.text), 1600)
        self.assertIn('lost ACK', r.text)
        self.assertIn('exit=1', r.text)
        self.assertEqual(r.exit_code, 1)

    def test_batch_does_not_hide_individual_failure(self):
        r = run_batch([[sys.executable, '-c', 'print("ok")'],
                       [sys.executable, '-c', 'raise SystemExit(3)']],
                      store=self.store, jobs=2, max_chars=2400)
        self.assertEqual([x.exit_code for x in r], [0, 3])


if __name__ == '__main__':
    unittest.main()
