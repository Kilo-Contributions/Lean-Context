import io
import json
from pathlib import Path
import tempfile
import unittest

from leancontext.mcp import dispatch, serve
from leancontext.store import Store


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root, self.root/'state')

    def request(self, method, params=None):
        return dispatch({'jsonrpc':'2.0','id':1,'method':method,'params':params or {}}, self.store)

    def test_one_small_read_only_tool_and_no_execution(self):
        tools = self.request('tools/list')['result']['tools']
        self.assertEqual(len(tools), 1)
        self.assertTrue(tools[0]['annotations']['readOnlyHint'])
        r = self.request('tools/call', {'name':'context','arguments':{'op':'run','command':'echo bad'}})
        self.assertTrue(r['result']['isError'])

    def test_exact_recall_and_bad_ref(self):
        ref = self.store.save('first\nsecond\nthird\n')
        result = self.request('tools/call', {'name':'context','arguments':{'op':'recall','ref':ref,'start':2,'limit':1}})
        text = result['result']['content'][0]['text']
        self.assertIn('L2: second', text)
        self.assertNotIn('L1: first', text)
        bad = self.request('tools/call', {'name':'context','arguments':{'op':'recall','ref':'../bad'}})
        self.assertTrue(bad['result']['isError'])

    def test_protocol_and_notifications(self):
        r = self.request('initialize', {'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}})
        self.assertEqual(r['result']['protocolVersion'], '2025-11-25')
        self.assertIsNone(dispatch({'jsonrpc':'2.0','method':'notifications/initialized'}, self.store))
        self.assertEqual(self.request('unknown')['error']['code'], -32601)

    def test_malformed_input_does_not_break_next_request(self):
        source = io.StringIO('bad json\n'+json.dumps({'jsonrpc':'2.0','id':9,'method':'ping'})+'\n')
        sink = io.StringIO()
        serve(self.store, source, sink)
        out = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(out[0]['error']['code'], -32700)
        self.assertEqual(out[1], {'jsonrpc':'2.0','id':9,'result':{}})

    def test_final_budget_covers_all_ops_and_metadata(self):
        (self.root/'many.py').write_text(''.join(f'def func_{i}(value):\n    return value+{i}\n' for i in range(300)))
        for op,args in [('outline',{'path':'many.py'}), ('search',{'query':'def func'}),
                        ('read',{'path':'many.py'}), ('files',{})]:
            with self.subTest(op=op):
                r=self.request('tools/call',{'name':'context','arguments':{'op':op,'max_chars':512,**args}})
                text=r['result']['content'][0]['text']
                self.assertLessEqual(len(text),512)
                self.assertFalse(r['result'].get('isError',False),text)


if __name__ == '__main__':
    unittest.main()
