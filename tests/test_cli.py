import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_cli(self, *args, input=None):
        return subprocess.run([sys.executable, '-m', 'leancontext', *map(str,args)],
                              cwd=PROJECT, input=input, capture_output=True, text=True, timeout=15)

    def test_run_exit_propagation_and_exact_recall(self):
        r = self.run_cli('run','--root',self.root,'--state-dir',self.root/'state','--json','--',
                         sys.executable,'-c','import sys; print("sample"); sys.exit(9)')
        self.assertEqual(r.returncode,9,r.stderr)
        data=json.loads(r.stdout)
        r=self.run_cli('recall',data['stdout_ref'],'--root',self.root,'--state-dir',self.root/'state','--raw')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(r.stdout,'sample\n')

    def test_bad_hook_input_fails_open(self):
        r=self.run_cli('hook','claude',input='not json')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(json.loads(r.stdout),{})

    def test_batch_overall_failure_and_individual_details(self):
        path=self.root/'jobs.json'
        path.write_text(json.dumps([[sys.executable,'-c','print("fine")'],
                                    [sys.executable,'-c','raise SystemExit(4)']]))
        r=self.run_cli('batch',path,'--root',self.root,'--state-dir',self.root/'state')
        self.assertEqual(r.returncode,1,r.stderr)
        self.assertIn('exit=4',r.stdout)

    def test_help_and_version(self):
        r=self.run_cli('--help')
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('serve',r.stdout)
        r=self.run_cli('--version')
        self.assertEqual(r.returncode,0)
        self.assertIn('0.1.0',r.stdout)


if __name__ == '__main__':
    unittest.main()
