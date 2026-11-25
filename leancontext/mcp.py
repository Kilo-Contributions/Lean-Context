"""Small stdio MCP server. One tool; local read-only operations only."""
from __future__ import annotations

import json
import sys

from . import __version__
from .recall import recall
from .store import Store

PROTOCOLS = ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')
TOOL = {
    'name': 'context',
    'description': 'Read-only local context: find files, literal search, exact source ranges, outlines, archived output. Use narrow queries; read exact source before edits.',
    'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False},
    'inputSchema': {'type': 'object', 'required': ['op'], 'additionalProperties': False,
        'properties': {
            'op': {'type': 'string', 'enum': ['files', 'search', 'read', 'outline', 'recall', 'stats']},
            'path': {'type': 'string'}, 'query': {'type': 'string'}, 'glob': {'type': 'string'},
            'ref': {'type': 'string'}, 'start': {'type': 'integer', 'minimum': 1},
            'end': {'type': 'integer', 'minimum': 1}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 1000},
            'offset': {'type': 'integer', 'minimum': 0},
            'max_chars': {'type': 'integer', 'minimum': 512, 'maximum': 12000}
        }}
}


def render_result(result: dict, max_chars: int = 6000) -> str:
    if type(max_chars) is not int or not 512 <= max_chars <= 12000:
        raise ValueError('max_chars must be 512–12000')
    # Repository text already includes source/method/scope/continuation labels.
    # Do not duplicate structured metadata or append past the final budget.
    text = result['text'] if 'text' in result else json.dumps(result, ensure_ascii=False, separators=(',', ':'))
    if len(text) <= max_chars:
        return text
    notice = ('\n[Partial: final response budget reached. Narrow the query/range '
              'or increase max_chars; omitted content remains available.]\n')
    lines=[]
    used=0
    for line in text.splitlines(keepends=True):
        if used+len(line)+len(notice)>max_chars:
            break
        lines.append(line)
        used+=len(line)
    return ''.join(lines)+notice


def context_action(store: Store, args: dict) -> dict:
    if not isinstance(args, dict):
        raise ValueError('arguments must be an object')
    unknown = set(args) - set(TOOL['inputSchema']['properties'])
    if unknown:
        raise ValueError('Unknown arguments: ' + ', '.join(sorted(unknown)))
    op = args.get('op')
    if op not in TOOL['inputSchema']['properties']['op']['enum']:
        raise ValueError('Unsupported read-only operation')
    for field in ('path', 'query', 'glob', 'ref'):
        if field in args and (not isinstance(args[field], str) or len(args[field]) > 2000):
            raise ValueError(f'{field} must be a bounded string')
    for field in ('start', 'end', 'limit', 'offset', 'max_chars'):
        if field in args and (not isinstance(args[field], int) or isinstance(args[field], bool)):
            raise ValueError(f'{field} must be an integer')
    max_chars = args.get('max_chars', 6000)
    if not 512 <= max_chars <= 12000:
        raise ValueError('max_chars must be 512–12000')
    if op == 'stats':
        return store.stats()
    if op == 'recall':
        return recall(store, args.get('ref', ''), start=args.get('start', 1),
                      limit=args.get('limit', 80), query=args.get('query'),
                      max_chars=max_chars, offset=args.get('offset', 0))
    from .repository import Repository
    repo = Repository(store.root)
    if op == 'files':
        return repo.files(glob=args.get('glob', '*'), limit=args.get('limit', 100))
    if op == 'search':
        return repo.search(args.get('query', ''), glob=args.get('glob', '*'), limit=args.get('limit', 20))
    if op == 'read':
        return repo.read(args.get('path', ''), start=args.get('start', 1), end=args.get('end'), max_chars=max_chars)
    return repo.outline(args.get('path', ''))


def dispatch(message, store: Store):
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
        return {'jsonrpc': '2.0', 'id': message.get('id') if isinstance(message, dict) else None,
                'error': {'code': -32600, 'message': 'Invalid request'}}
    if 'id' not in message:
        return None
    request_id = message['id']
    if isinstance(request_id, (dict, list, bool)):
        return {'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid request id'}}
    method = message['method']
    params = message.get('params', {})
    response = {'jsonrpc': '2.0', 'id': request_id}
    if not isinstance(params, dict):
        return {**response, 'error': {'code': -32602, 'message': 'params must be an object'}}
    if method == 'initialize':
        requested = params.get('protocolVersion')
        result = {'protocolVersion': requested if requested in PROTOCOLS else PROTOCOLS[-1],
                  'capabilities': {'tools': {'listChanged': False}},
                  'serverInfo': {'name': 'leancontext', 'version': __version__}}
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = {'tools': [TOOL]}
    elif method == 'tools/call':
        try:
            if params.get('name') != 'context':
                raise ValueError('Unknown tool')
            arguments=params.get('arguments', {})
            value = context_action(store, arguments)
            returned=render_result(value,arguments.get('max_chars',6000))
            if arguments.get('op') not in ('recall','stats'):
                store.record({'operation':'context_'+arguments['op'], 'emitted_chars':len(returned),
                              'emitted_bytes':len(returned.encode('utf-8'))})
            result = {'content': [{'type': 'text', 'text': returned}]}
        except (ValueError, OSError, TypeError, KeyError) as exc:
            result = {'isError': True, 'content': [{'type': 'text', 'text': f'LeanContext: {exc}'}]}
    else:
        return {**response, 'error': {'code': -32601, 'message': 'Method not found'}}
    return {**response, 'result': result}


def serve(store: Store, source=None, sink=None) -> None:
    source = source or sys.stdin
    sink = sink or sys.stdout
    while True:
        line = source.readline(1024*1024+1)
        if not line:
            break
        if len(line) > 1024*1024:
            response = {'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Request exceeds 1 MiB'}}
            while line and not line.endswith('\n'):
                line = source.readline(1024*1024+1)
        else:
            try:
                message = json.loads(line)
                response = dispatch(message, store)
            except (ValueError, UnicodeError):
                response = {'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Parse error'}}
        if response is not None:
            sink.write(json.dumps(response, ensure_ascii=False, separators=(',', ':'))+'\n')
            sink.flush()
