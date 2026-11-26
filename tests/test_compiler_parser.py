"""Compiler evidence retention, not just output-length assertions."""
from __future__ import annotations

import unittest

try:
    from leancontext.compiler_parser import parse_compiler
except ImportError:
    parse_compiler = None


class CompilerParserTests(unittest.TestCase):
    def parse(self, text):
        self.assertTrue(callable(parse_compiler), 'compiler semantic parser is not implemented')
        return parse_compiler(text)

    def test_gcc_preserves_each_error_with_source_caret_and_notes(self):
        first = ("src/a.c:7:9: error: 'missing' undeclared\n"
                 "    7 | int x = missing;\n"
                 "      |         ^~~~~~~\n"
                 "src/a.h:2:1: note: 'missing' is declared here\n"
                 "    2 | extern int missing;\n"
                 "      | ^\n")
        second = ("src/b.c:19:2: fatal error: lost.h: No such file or directory\n"
                  "   19 | #include <lost.h>\n"
                  "      |          ^~~~~~~~\n"
                  "compilation terminated.\n")
        report = self.parse('[1/9] Building a.c\n' + first + second)
        self.assertEqual(report.strategy, 'compiler')
        self.assertEqual(len(report.diagnostics), 2)
        self.assertEqual([d.id for d in report.diagnostics], ['D1', 'D2'])
        self.assertEqual(report.diagnostics[0].traceback, first)
        self.assertEqual(report.diagnostics[1].detail, second)
        self.assertEqual(report.diagnostics[0].start_line, 2)
        self.assertEqual(report.diagnostics[0].end_line, 7)
        self.assertIsNone(report.inventory_complete)

    def test_exact_duplicate_blocks_share_signature(self):
        block = "src/a.cc:4:2: error: incompatible types\n  call();\n  ^\n"
        report = self.parse(block + '[2/9] Building next target\n' + block)
        self.assertEqual(len(report.diagnostics), 2)
        self.assertIsNotNone(report.diagnostics[0].signature)
        self.assertEqual(report.diagnostics[0].signature, report.diagnostics[1].signature)
        self.assertEqual(report.diagnostics[0].detail, block)

    def test_distinct_location_message_caret_or_note_never_share_signature(self):
        variants = [
            "src/a.c:4:2: error: expected 1\n  x;\n  ^\n",
            "src/a.c:5:2: error: expected 1\n  x;\n  ^\n",
            "src/a.c:4:2: error: expected 2\n  x;\n  ^\n",
            "src/a.c:4:2: error: expected 1\n  x;\n   ^\n",
            "src/a.c:4:2: error: expected 1\n  x;\n  ^\nsrc/a.h:8:1: note: different overload\n",
        ]
        signatures = [self.parse(v).diagnostics[0].signature for v in variants]
        self.assertEqual(len(set(signatures)), len(variants))

    def test_warnings_do_not_hide_errors_and_omission_is_explicit(self):
        warnings = ''.join(f'src/w{i}.c:3:1: warning: deprecated\n    old();\n    ^\n' for i in range(300))
        error = 'src/final.c:91:3: error: result is not declared\n'
        report = self.parse(warnings + error)
        self.assertEqual(len(report.diagnostics), 1)
        self.assertIn(error, report.diagnostics[0].detail)
        self.assertIn('300 warning', ' '.join(report.notices))
        self.assertIn('omitted', ' '.join(report.notices))

    def test_warning_only_output_uses_fallback(self):
        self.assertIsNone(self.parse('src/a.c:1:1: warning: unused variable\n  int x;\n      ^\n'))

    def test_source_search_and_prose_are_not_compiler_diagnostics(self):
        for raw in (
            'src/a.py:1: raise ValueError("error: missing")\n',
            'src/a.ts:9: const error: Error = new Error("broken");\n',
            'src/a.c:3: // error: identifier missing\n',
            'error: user supplied a bad password\n',
            'failed 3 tests\n',
            '{"level":"error","message":"not a compiler text report"}\n',
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(self.parse(raw))

    def test_typescript_plain_and_pretty_diagnostics_remain_complete(self):
        plain = "src/a.ts(12,5): error TS2322: Type 'string' is not assignable to type 'number'.\n"
        pretty = ("src/b.ts:20:7 - error TS2345: Argument is not assignable.\n\n"
                  "20   accept(value);\n"
                  "            ~~~~~\n\n"
                  "  src/types.ts:4:1\n"
                  "    4 declare function accept(x: number): void;\n"
                  "      ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n"
                  "    The expected type comes from this signature.\n")
        report = self.parse(plain + pretty + '\nFound 2 errors in 2 files.\n')
        self.assertEqual(len(report.diagnostics), 2)
        self.assertEqual(report.diagnostics[0].detail, plain)
        self.assertEqual(report.diagnostics[1].detail.rstrip(), pretty.rstrip())
        self.assertIn('TS2322', report.diagnostics[0].label)
        self.assertIn('Found 2 errors', report.summary)

    def test_typescript_global_error_has_a_recallable_record(self):
        line = "error TS18003: No inputs were found in config file 'tsconfig.json'.\n"
        report = self.parse(line)
        self.assertEqual(report.diagnostics[0].detail, line)
        self.assertIn('TS18003', report.diagnostics[0].label)

    def test_rust_errors_keep_help_and_secondary_spans(self):
        block = ('error[E0308]: mismatched types\n'
                 ' --> src/main.rs:4:13\n'
                 '  |\n'
                 '4 |     consume(value);\n'
                 '  |             ^^^^^ expected `u32`, found `String`\n'
                 '  |\n'
                 'note: function defined here\n'
                 ' --> src/main.rs:1:4\n'
                 '  |\n'
                 '1 | fn consume(value: u32) {}\n'
                 '  |    ^^^^^^^ ----------\n'
                 '  = help: parse the string first\n')
        report = self.parse('   Compiling demo v0.1.0\n' + block + '\nerror: could not compile `demo` due to 1 previous error\n')
        self.assertEqual(len(report.diagnostics), 1)
        self.assertEqual(report.diagnostics[0].detail.rstrip(), block.rstrip())
        self.assertIn('src/main.rs:4:13', report.diagnostics[0].label)
        self.assertIn('E0308', report.diagnostics[0].label)
        self.assertIn('could not compile', report.summary)

    def test_rust_uncoded_diagnostic_requires_compiler_context(self):
        block = 'error: expected `;`, found `}`\n --> src/main.rs:3:2\n  |\n3 | }\n  | ^ expected `;`\n'
        report = self.parse(block)
        self.assertEqual(report.diagnostics[0].detail, block)
        self.assertIsNone(self.parse('error: unexpected response\nplease try again\n'))

    def test_msvc_diagnostic_with_windows_path_is_preserved(self):
        block = 'C:\\work\\source.cpp(14,7): error C2065: undeclared identifier\n'
        report = self.parse(block)
        self.assertEqual(report.diagnostics[0].detail, block)
        self.assertIn('C2065', report.diagnostics[0].label)

    def test_ansi_is_only_normalized_for_recognition_and_grouping(self):
        plain = "main.c:2:3: error: undeclared\n  x;\n  ^\n"
        colored = '\x1b[1mmain.c:2:3: \x1b[31merror:\x1b[0m undeclared\n  x;\n  ^\n'
        report = self.parse(colored + plain)
        self.assertEqual(report.diagnostics[0].detail, colored)
        self.assertEqual(report.diagnostics[0].signature, report.diagnostics[1].signature)

    def test_include_and_template_context_stays_with_error(self):
        block = ('In file included from src/main.cc:1:\n'
                 '                 from src/all.h:2:\n'
                 'src/template.h: In instantiation of template `T<X>`:\n'
                 'src/main.cc:8: required from here\n'
                 'src/template.h:3:8: error: no member named `x`\n'
                 '    3 | T::x;\n'
                 '      |    ^\n')
        report = self.parse('[1/2] Building\n' + block)
        self.assertEqual(report.diagnostics[0].detail, block)
        self.assertEqual(report.diagnostics[0].start_line, 2)

    def test_compiler_limit_summary_does_not_imply_complete_inventory(self):
        raw = ('a.c:1:1: error: unknown name\n'
               'fatal error: too many errors emitted, stopping now [-ferror-limit=]\n'
               '20 errors generated.\n')
        report = self.parse(raw)
        self.assertIsNone(report.inventory_complete)
        self.assertIn('20 errors generated', report.summary)
        self.assertIn('too many errors', report.diagnostics[-1].detail)
        self.assertTrue(any('observed' in n.lower() for n in report.notices))

    def test_record_count_is_unbounded_by_display_budget(self):
        raw = ''.join(f'src/{i}.c:{i+1}:1: error: independent failure {i}\n' for i in range(125))
        report = self.parse(raw)
        self.assertEqual(len(report.diagnostics), 125)
        self.assertEqual(report.diagnostics[-1].id, 'D125')
        self.assertEqual(report.diagnostics[-1].message, 'independent failure 124')


if __name__ == '__main__':
    unittest.main()
