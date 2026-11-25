import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from leancontext.store import Store
from leancontext.worker import ask_worker, validate_endpoint


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root, self.root/'state')
        (self.root/'calc.py').write_text('def add(a, b):\n    return a + b\n')
        self.config = self.root/'worker.json'
        self.requests = []

    def server(self, response):
        requests = self.requests
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                data = json.dumps(response).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.config.write_text(json.dumps({'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                                           'model': 'test-local', 'max_output_tokens': 300}))

    def test_one_bounded_request_and_grounded_quote(self):
        answer = {'answer': 'add returns the sum.', 'evidence': [{'id':'E1', 'quote':'return a + b'}], 'uncertainties': []}
        self.server({'choices':[{'message':{'content':json.dumps(answer)}}],
                     'usage':{'prompt_tokens':123,'completion_tokens':37}})
        result = ask_worker('What does add do?', ['calc.py'], config_path=self.config,
                            store=self.store)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]['max_tokens'], 300)
        self.assertNotIn('tools', self.requests[0])
        self.assertIn('UNTRUSTED', result['text'])
        self.assertTrue(result['citations_validated'])
        self.assertEqual(result['usage']['prompt_tokens'], 123)
        self.assertEqual((self.root/'calc.py').read_text(), 'def add(a, b):\n    return a + b\n')

    def test_fabricated_quote_is_rejected_without_retry(self):
        answer = {'answer':'wrong', 'evidence':[{'id':'E1','quote':'delete all files'}]}
        self.server({'choices':[{'message':{'content':json.dumps(answer)}}]})
        with self.assertRaisesRegex(ValueError, 'quote'):
            ask_worker('What?', ['calc.py'], config_path=self.config, store=self.store)
        self.assertEqual(len(self.requests), 1)

    def test_remote_requires_explicit_choice_and_https(self):
        with self.assertRaises(ValueError):
            validate_endpoint('https://example.com/v1', allow_remote=False)
        self.assertEqual(validate_endpoint('https://example.com/v1', allow_remote=True),
                         'https://example.com/v1/chat/completions')
        with self.assertRaises(ValueError):
            validate_endpoint('http://example.com/v1', allow_remote=True)
        with self.assertRaises(ValueError):
            validate_endpoint('http://token@localhost:8080/v1', allow_remote=False)

    def test_missing_config_makes_no_call(self):
        with self.assertRaises(FileNotFoundError):
            ask_worker('What?', ['calc.py'], config_path=self.config, store=self.store)

    def test_bad_header_credential_never_appears_in_error(self):
        self.config.write_text(json.dumps({'base_url':'http://127.0.0.1:1/v1','model':'test',
                                           'api_key_env':'LEANCONTEXT_TEST_KEY'}))
        with patch.dict(os.environ, {'LEANCONTEXT_TEST_KEY':'FAKE_SECRET_DO_NOT_LEAK\n'}):
            with self.assertRaises(ValueError) as caught:
                ask_worker('What?', ['calc.py'], config_path=self.config, store=self.store)
        self.assertNotIn('FAKE_SECRET_DO_NOT_LEAK', str(caught.exception))
        self.assertIn('credential', str(caught.exception).lower())

    def test_untrusted_usage_cannot_flood_frontier_output(self):
        answer = {'answer':'It adds.', 'evidence':[{'id':'E1','quote':'return a + b'}]}
        self.server({'choices':[{'message':{'content':json.dumps(answer)}}],
                     'usage':{'prompt_tokens':123,'unvalidated':'X'*50000}})
        result=ask_worker('What?', ['calc.py'], config_path=self.config, store=self.store)
        self.assertLessEqual(len(result['text']),6000)
        self.assertEqual(result['usage'], {'prompt_tokens':123})


if __name__ == '__main__':
    unittest.main()
