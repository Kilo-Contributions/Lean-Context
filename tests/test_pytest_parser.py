"""Semantic retention tests; fixtures mirror real pytest terminal structures."""
from __future__ import annotations

import unittest
from pathlib import Path

from leancontext.pytest_parser import parse_pytest


def failure(name, *, file='test_cases.py', line=8, message='ValueError: shared cause', captured=''):
    return (f'________________________ {name} ________________________\n'
            '\ncase = 1\n\n    def test_repeat(case):\n>       cause()\n\n'
            f'{file}:{line}: \n'
            '_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _\n\n'
            '    def cause():\n>       raise ValueError("shared cause")\n'
            f'E       {message}\n\nhelpers.py:4: {message.split(":", 1)[0]}\n'
            + (f'------------------- Captured stdout call -------------------\n{captured}\n' if captured else ''))


def transcript(blocks, rows, total, *, header=True):
    return (('================ test session starts ================\nplatform linux -- pytest-9.2.0\n' if header else '')
            + '================ FAILURES ================\n' + ''.join(blocks)
            + '================ short test summary info ================\n'
            + '\n'.join(rows) + '\n'
            + f'================ {total} failed, 202 passed in 0.12s ================\n')


class PytestParserTests(unittest.TestCase):
    def test_indexes_twenty_five_failures_and_groups_only_equal_signatures(self):
        names = [f'test_repeat[case-{i}]' for i in range(25)]
        blocks = [failure(name, line=8 + i % 3, captured=f'log case {i}') for i, name in enumerate(names)]
        rows = [f'FAILED test_cases.py::{name} - ValueError: shared cause' for name in names]
        result = parse_pytest(transcript(blocks, rows, 25))
        self.assertEqual([item.label for item in result.diagnostics], [f'test_cases.py::{name}' for name in names])
        self.assertEqual([item.id for item in result.diagnostics], [f'F{i}' for i in range(1, 26)])
        self.assertEqual(len({item.signature for item in result.diagnostics}), 3)
        self.assertTrue(all(item.signature for item in result.diagnostics))
        self.assertTrue(result.inventory_complete)
        self.assertEqual(result.expected_count, 25)

    def test_keeps_whole_traceback_and_separately_keeps_captured_output(self):
        block = failure('test_repeat[x]', captured='arbitrary diagnostic context\nsecond line')
        result = parse_pytest(transcript([block], ['FAILED test_cases.py::test_repeat[x] - ValueError: shared cause'], 1))
        item = result.diagnostics[0]
        self.assertEqual(item.detail, block)
        self.assertEqual(item.traceback, block.split('\n', 1)[1].split('------------------- Captured')[0])
        self.assertIn('second line', item.detail)
        self.assertNotIn('Captured stdout', item.traceback)
        self.assertIsNotNone(item.start_line)

    def test_exception_values_and_all_frame_locations_are_grouping_evidence(self):
        blocks = [failure('test_repeat[a]', message='ValueError: expected 2'),
                  failure('test_repeat[b]', message='ValueError: expected 3'),
                  failure('test_repeat[c]', line=99, message='ValueError: expected 2')]
        rows = [f'FAILED test_cases.py::test_repeat[{c}] - ValueError: expected 2' for c in 'abc']
        result = parse_pytest(transcript(blocks, rows, 3))
        self.assertEqual(len({item.signature for item in result.diagnostics}), 3)

    def test_parameter_nodeids_keep_spaces_delimiters_and_colons(self):
        name = 'TestThing.test_repeat[sp ace - dash::colon]'
        node = 'tests/test_cases.py::TestThing::test_repeat[sp ace - dash::colon]'
        text = transcript([failure(name, file='tests/test_cases.py')], [f'FAILED {node} - ValueError: shared cause - extra'], 1)
        item = parse_pytest(text).diagnostics[0]
        self.assertEqual(item.label, node)
        self.assertTrue(item.traceback)

    def test_setup_and_teardown_for_same_node_remain_separate(self):
        blocks = [failure('ERROR at setup of test_repeat', message='RuntimeError: same'),
                  failure('ERROR at teardown of test_repeat', message='RuntimeError: same')]
        text = ('================ ERRORS ================\n' + ''.join(blocks)
                + '================ short test summary info ================\n'
                + 'ERROR test_cases.py::test_repeat - RuntimeError: same\n' * 2
                + '================ 2 errors in 0.12s ================\n')
        result = parse_pytest(text)
        self.assertEqual([x.phase for x in result.diagnostics], ['setup', 'teardown'])
        self.assertNotEqual(result.diagnostics[0].signature, result.diagnostics[1].signature)
        self.assertTrue(result.inventory_complete)

    def test_collection_error_is_indexed_by_file(self):
        text = ('================ ERRORS ================\n'
                '________________ ERROR collecting tests/test_import.py ________________\n'
                'ImportError while importing test module tests/test_import.py.\n'
                'Traceback:\n'
                'tests/test_import.py:2: in <module>\n'
                '    import package_missing\n'
                "E   ModuleNotFoundError: No module named 'package_missing'\n"
                '================ short test summary info ================\n'
                "ERROR tests/test_import.py - ModuleNotFoundError: No module named 'package_missing'\n"
                '!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!\n'
                '================ 1 error in 0.06s ================\n')
        result = parse_pytest(text)
        item = result.diagnostics[0]
        self.assertEqual(item.label, 'tests/test_import.py')
        self.assertEqual(item.phase, 'collect')
        self.assertIn('import package_missing', item.traceback)
        self.assertTrue(result.inventory_complete)

    def test_summary_only_never_invents_tracebacks(self):
        text = ('================ short test summary info ================\n'
                'FAILED test_cases.py::test_a - ValueError: a\n'
                'FAILED test_cases.py::test_b - ValueError: a\n'
                '================ 2 failed in 0.01s ================\n')
        result = parse_pytest(text)
        self.assertEqual(len(result.diagnostics), 2)
        self.assertTrue(all(not x.traceback and x.signature is None for x in result.diagnostics))
        self.assertIn('traceback', ' '.join(result.notices).lower())

    def test_missing_summary_count_is_explicitly_unknown(self):
        text = ('================ FAILURES ================\n'
                + failure('test_repeat')
                + '================ short test summary info ================\n'
                + 'FAILED test_cases.py::test_repeat - ValueError: shared cause\n')
        result = parse_pytest(text)
        self.assertIsNone(result.inventory_complete)
        self.assertIsNone(result.expected_count)
        self.assertTrue(result.notices)

    def test_count_mismatch_never_claims_full_inventory(self):
        result = parse_pytest(transcript([failure('test_repeat')], ['FAILED test_cases.py::test_repeat - ValueError: shared cause'], 2))
        self.assertFalse(result.inventory_complete)
        self.assertIn('2', ' '.join(result.notices))

    def test_ansi_recognition_preserves_raw_detail(self):
        text = transcript([failure('test_repeat')], ['FAILED test_cases.py::test_repeat - ValueError: shared cause'], 1)
        colored = '\n'.join('\x1b[31m' + line + '\x1b[0m' for line in text.split('\n'))
        result = parse_pytest(colored)
        self.assertEqual(result.diagnostics[0].label, 'test_cases.py::test_repeat')
        self.assertIn('\x1b[31m', result.diagnostics[0].detail)
        self.assertTrue(result.diagnostics[0].signature)

    def test_same_headings_resolve_using_file_frames(self):
        result = parse_pytest(transcript(
            [failure('test_repeat', file='a/test_cases.py'), failure('test_repeat', file='b/test_cases.py')],
            ['FAILED a/test_cases.py::test_repeat - ValueError: shared cause',
             'FAILED b/test_cases.py::test_repeat - ValueError: shared cause'], 2))
        self.assertIn('a/test_cases.py:', result.diagnostics[0].traceback)
        self.assertNotIn('b/test_cases.py:', result.diagnostics[0].traceback)
        self.assertIn('b/test_cases.py:', result.diagnostics[1].traceback)

    def test_progress_identifiers_survive_suppressed_summary(self):
        text = ('================ test session starts ================\n'
                'test_cases.py::test_repeat FAILED [100%]\n'
                '================ FAILURES ================\n'
                + failure('test_repeat')
                + '================ 1 failed in 0.01s ================\n')
        result = parse_pytest(text)
        self.assertEqual(result.diagnostics[0].label, 'test_cases.py::test_repeat')
        self.assertTrue(result.inventory_complete)

    def test_unresolved_headings_are_preserved_and_labeled(self):
        result = parse_pytest('================ FAILURES ================\n' + failure('test_repeat') + '================ 1 failed in 0.01s ================\n')
        self.assertEqual(len(result.diagnostics), 1)
        self.assertIn('test_repeat', result.diagnostics[0].label)
        self.assertTrue(result.diagnostics[0].traceback)
        self.assertFalse(result.inventory_complete)
        self.assertIn('identifier', ' '.join(result.notices).lower())

    def test_xpass_strict_is_failure_without_fabricated_stack(self):
        block = '________________ test_xpass ________________\n[XPASS(strict)] must fail\n'
        result = parse_pytest(transcript([block], ['FAILED test_cases.py::test_xpass - [XPASS(strict)] must fail'], 1))
        self.assertEqual(result.diagnostics[0].status, 'FAILED')
        self.assertIsNone(result.diagnostics[0].signature)
        self.assertIn('[XPASS(strict)]', result.diagnostics[0].detail)

    def test_success_requires_pytest_evidence_and_retains_actual_summary(self):
        self.assertIsNone(parse_pytest('3 passed in 0.01s\n'))
        result = parse_pytest('================ test session starts ================\ncollected 2 items\ntests/test_ok.py .. [100%]\n================ 2 passed in 0.01s ================\n')
        self.assertEqual(result.strategy, 'test-success')
        self.assertEqual(result.diagnostics, ())
        self.assertIn('2 passed in 0.01s', result.summary)

    def test_python_traceback_native_supports_complete_body(self):
        block = ('________________ test_repeat[a] ________________\n'
                 'Traceback (most recent call last):\n'
                 '  File "test_cases.py", line 9, in test_repeat\n'
                 '    cause()\n'
                 '  File "helpers.py", line 4, in cause\n'
                 '    raise ValueError("same")\n'
                 'ValueError: same\n')
        result = parse_pytest(transcript([block], ['FAILED test_cases.py::test_repeat[a] - ValueError: same'], 1))
        self.assertTrue(result.diagnostics[0].signature)
        self.assertEqual(result.diagnostics[0].traceback, block.split('\n', 1)[1])

    def test_failure_summary_not_found_inside_captured_stdout(self):
        result = parse_pytest(transcript([failure('test_repeat', captured='FAILED secret.py::spoof - ValueError: fake')], ['FAILED test_cases.py::test_repeat - ValueError: shared cause'], 1))
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual(result.diagnostics[0].label, 'test_cases.py::test_repeat')

    def test_captured_progress_lines_do_not_add_fake_failures(self):
        result = parse_pytest(transcript([failure('test_repeat', captured='secret.py::spoof FAILED [100%]')], ['FAILED test_cases.py::test_repeat - ValueError: shared cause'], 1))
        self.assertEqual(len(result.diagnostics), 1)
        self.assertTrue(result.inventory_complete)

    def test_line_style_preserves_printed_exception_locations_and_captures(self):
        text = ('================ FAILURES ================\n'
                'E   ValueError: same\n'
                '---------------- Captured stdout call ----------------\nfirst log\n'
                '/project/helper.py:42: ValueError: same\n'
                'E   ValueError: same\n'
                '---------------- Captured stdout call ----------------\nsecond log\n'
                '/project/helper.py:42: ValueError: same\n'
                '================ short test summary info ================\n'
                'FAILED test_cases.py::test_a - ValueError: same\n'
                'FAILED test_cases.py::test_b - ValueError: same\n'
                '================ 2 failed in 0.01s ================\n')
        result = parse_pytest(text)
        self.assertEqual(len(result.diagnostics), 2)
        self.assertTrue(result.inventory_complete)
        self.assertIn('first log', result.diagnostics[0].detail)
        self.assertIn('second log', result.diagnostics[1].detail)
        self.assertNotIn('first log', result.diagnostics[0].traceback)
        self.assertTrue(all('helper.py:42:' in item.traceback for item in result.diagnostics))
        self.assertTrue(all(item.signature is None for item in result.diagnostics))
        self.assertIn('full stack', ' '.join(result.notices).lower())

    def test_ambiguous_headingless_failures_are_retained_separately(self):
        text = ('================ FAILURES ================\n'
                'custom plugin traceback without standard markers\nuseful evidence\n'
                '================ short test summary info ================\n'
                'FAILED test_cases.py::test_a - ValueError: same\n'
                '================ 1 failed in 0.01s ================\n')
        result = parse_pytest(text)
        self.assertIn('useful evidence', ''.join(x.detail for x in result.diagnostics))
        self.assertFalse(result.inventory_complete)

    def test_literal_unbalanced_brackets_in_parameter_ids(self):
        name = 'test_repeat[literal[open]'
        node = 'test_cases.py::' + name
        result = parse_pytest(transcript([failure(name)], [f'FAILED {node} - ValueError: shared cause'], 1))
        self.assertEqual(result.diagnostics[0].label, node)
        self.assertTrue(result.diagnostics[0].traceback)

    def test_exception_chain_relationship_is_part_of_signature(self):
        first = failure('test_repeat[a]').replace('    def cause():',
            'The above exception was the direct cause of the following exception:\n\n    def cause():')
        second = failure('test_repeat[b]').replace('    def cause():',
            'During handling of the above exception, another exception occurred:\n\n    def cause():')
        result = parse_pytest(transcript([first, second], [
            'FAILED test_cases.py::test_repeat[a] - ValueError: shared cause',
            'FAILED test_cases.py::test_repeat[b] - ValueError: shared cause'], 2))
        self.assertNotEqual(result.diagnostics[0].signature, result.diagnostics[1].signature)

    def test_exception_group_tree_stays_singleton_when_frames_are_nonstandard(self):
        block = failure('test_repeat').replace('E       ValueError: shared cause',
            'E       ExceptionGroup: multiple errors\n  + Exception Group Traceback (most recent call last):\n  | ValueError: nested')
        result = parse_pytest(transcript([block], [
            'FAILED test_cases.py::test_repeat - ExceptionGroup: multiple errors'], 1))
        self.assertIsNone(result.diagnostics[0].signature)
        self.assertIn('| ValueError: nested', result.diagnostics[0].traceback)

    def test_actual_pytest_exception_group_body_preserves_inner_errors(self):
        # Captured from an executed pytest 9.2.0 run, including its tree format.
        text = (Path(__file__).parent / 'fixtures' / 'pytest_9_2_review.txt').read_text()
        result = parse_pytest(text)
        item = next(x for x in result.diagnostics if x.label.endswith('::test_group'))
        self.assertIn('ValueError: inner1', item.traceback)
        self.assertIn('TypeError: inner2', item.traceback)
        self.assertIsNone(item.signature)

    def test_actual_pytest_pytrace_false_preserves_multiline_failure(self):
        text = (Path(__file__).parent / 'fixtures' / 'pytest_9_2_review.txt').read_text()
        result = parse_pytest(text)
        item = next(x for x in result.diagnostics if x.label.endswith('::test_failure'))
        self.assertEqual(item.traceback, 'first\nSECOND\nTHIRD\n')
        self.assertIsNone(item.signature)

    def test_actual_pytest_literal_closing_bracket_id_matches_whole_heading(self):
        text = (Path(__file__).parent / 'fixtures' / 'pytest_9_2_review.txt').read_text()
        result = parse_pytest(text)
        self.assertEqual(len(result.diagnostics), 4)
        self.assertTrue(result.inventory_complete)
        item = next(x for x in result.diagnostics if x.label == 'test_review_cases.py::test_bad[left] - key]')
        self.assertIn('E       assert 1 == 0', item.traceback)


if __name__ == '__main__':
    unittest.main()
