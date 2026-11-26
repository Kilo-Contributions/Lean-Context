"""Executable synthetic pytest workloads; never imported by the product.

The grouped workload has the same *counts* as the reported user failure, not
the user's unavailable source or transcript. Keep the expected IDs independent
of the parser so retention assertions cannot bless a parser omission.
"""
from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess
import sys
import textwrap


_COMMON = '''\
import pytest

@pytest.mark.parametrize("case", range(202), ids=lambda case: f"pass-{case:03d}")
def test_success(case):
    assert case + 1 > case
'''

_GROUPED = '''\

def _validate_record(record):
    raise ValueError("record missing required field: account_id")

def _decode_record(record):
    return _validate_record(record)

@pytest.mark.parametrize("case", range(10), ids=lambda case: f"record-{case:02d}")
def test_records(case):
    print(f"Reading record fixture {case}; original payload preserved in the captured log.")
    _decode_record({})

def _lookup_account(name):
    raise LookupError("account index is unavailable")

def _load_account(name):
    return _lookup_account(name)

@pytest.mark.parametrize("case", range(8), ids=lambda case: f"account-{case:02d}")
def test_accounts(case):
    print(f"Loading account fixture {case}; original lookup details preserved in the captured log.")
    _load_account("primary")

def _commit_transaction():
    raise RuntimeError("transaction journal is read-only")

def _write_transaction():
    return _commit_transaction()

@pytest.mark.parametrize("case", range(7), ids=lambda case: f"transaction-{case:02d}")
def test_transactions(case):
    print(f"Writing transaction fixture {case}; original journal details preserved in the captured log.")
    _write_transaction()
'''

_DISTINCT = '''\

def _lookup_distinct(case):
    raise ValueError(f"account {case:02d} has a distinct missing balance")

@pytest.mark.parametrize("case", range(25), ids=lambda case: f"distinct-{case:02d}")
def test_distinct(case):
    print(f"Inspecting account {case:02d}; this is a separate exception message.")
    _lookup_distinct(case)
'''


def fixture_source(mode: str = 'grouped') -> str:
    if mode not in ('grouped', 'distinct', 'success'):
        raise ValueError('mode must be grouped, distinct, or success')
    return textwrap.dedent(_COMMON + {'grouped': _GROUPED, 'distinct': _DISTINCT,
                                    'success': ''}[mode])


def expected_ids(mode: str = 'grouped', filename: str = 'test_regression.py') -> list[str]:
    if mode == 'success':
        return []
    if mode == 'distinct':
        return [f'{filename}::test_distinct[distinct-{i:02d}]' for i in range(25)]
    if mode != 'grouped':
        raise ValueError('mode must be grouped, distinct, or success')
    return ([f'{filename}::test_records[record-{i:02d}]' for i in range(10)] +
            [f'{filename}::test_accounts[account-{i:02d}]' for i in range(8)] +
            [f'{filename}::test_transactions[transaction-{i:02d}]' for i in range(7)])


def fixture_traceback(raw: str, nodeid: str) -> str:
    """Independently extract this controlled fixture's complete traceback body.

    This is deliberately fixture-specific: each generated failure prints exactly
    one Captured stdout call section after its traceback. Product parser output
    cannot define the expected evidence used to evaluate that parser.
    """
    name = nodeid.partition('::')[2]
    title = re.search(r'^_+ ' + re.escape(name) + r' _+\s*$', raw, re.MULTILINE)
    if title is None:
        raise AssertionError(f'Fixture traceback title is absent: {nodeid}')
    capture = re.search(r'^-+ Captured stdout call -+\s*$', raw[title.end():], re.MULTILINE)
    if capture is None:
        raise AssertionError(f'Fixture captured-output section is absent: {nodeid}')
    return raw[title.end():title.end() + capture.start()].strip('\n')


def execute_fixture(directory: Path, mode: str = 'grouped',
                    python: str = sys.executable) -> subprocess.CompletedProcess[str]:
    """Run one controlled pytest invocation; no project plugins/configuration."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'test_regression.py').write_text(fixture_source(mode), encoding='utf-8')
    config = directory / 'pytest.ini'
    config.write_text('[pytest]\n', encoding='utf-8')
    env = {k: v for k, v in os.environ.items()
           if k not in ('PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTHONPATH')}
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    env['COLUMNS'] = '100'
    return subprocess.run([python, '-m', 'pytest', '-c', str(config),
                           '-v', '--tb=long', '--color=no', 'test_regression.py'],
                          cwd=directory, env=env, text=True, capture_output=True,
                          timeout=60, check=False)
