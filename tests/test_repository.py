"""Behavioral tests for bounded, source-exact repository retrieval."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        try:
            module = importlib.import_module("leancontext.repository")
        except ModuleNotFoundError:
            self.fail("Repository retrieval is not implemented")
        self.repo = module.Repository(self.root)

    def write(self, path, text):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text, encoding="utf-8")
        return p

    def test_read_returns_exact_range_and_real_line_numbers(self):
        self.write("src/main.py", "# first\nα = 1\n\nprint(α)\n# last\n")
        result = self.repo.read("src/main.py", start=2, end=4)
        self.assertIn("src/main.py:2: α = 1\nsrc/main.py:3: \nsrc/main.py:4: print(α)", result["text"])
        self.assertNotIn("# first", result["text"])
        self.assertNotIn("# last", result["text"])
        self.assertEqual(result["start"], 2)
        self.assertEqual(result["end"], 4)
        self.assertEqual(result["total_lines"], 5)
        self.assertFalse(result["truncated"])

    def test_read_budget_preserves_complete_lines_and_reports_continuation(self):
        self.write("many.txt", "".join(f"line-{n:04d} with a little extra text\n" for n in range(1, 201)))
        result = self.repo.read("many.txt", start=10, end=100, max_chars=400)
        self.assertLessEqual(len(result["text"]), 400)
        self.assertTrue(result["truncated"])
        self.assertGreater(result["next_start"], 10)
        self.assertLessEqual(result["next_start"], 100)
        resumed = self.repo.read("many.txt", start=result["next_start"], end=result["next_start"])
        self.assertIn(f"many.txt:{result['next_start']}: line-{result['next_start']:04d}", resumed["text"])

    def test_long_line_is_not_presented_as_an_exact_complete_line(self):
        self.write("long.txt", "A" * 3000 + "\nshort\n")
        result = self.repo.read("long.txt", max_chars=200)
        self.assertLessEqual(len(result["text"]), 200)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["next_start"], 1)
        self.assertGreater(result["required_chars_for_next_line"], 3000)
        self.assertNotIn("long.txt:1: AAA", result["text"])

    def test_small_single_line_that_fits_budget_is_not_omitted(self):
        self.write("a.txt", "yes\n")
        result = self.repo.read("a.txt", max_chars=100)
        self.assertFalse(result["truncated"])
        self.assertIn("a.txt:1: yes", result["text"])
        self.assertLessEqual(len(result["text"]), 100)

    def test_search_is_literal_and_context_uses_original_line_numbers(self):
        self.write("notes.txt", "one\nbefore\nneedle [a-z]+\nafter\nfive\n")
        self.write("decoy.txt", "needle azzz\n")
        result = self.repo.search("needle [a-z]+", context=1)
        self.assertEqual([(m["path"], m["line"]) for m in result["matches"]], [("notes.txt", 3)])
        self.assertIn("notes.txt:2: before\nnotes.txt:3: needle [a-z]+\nnotes.txt:4: after", result["text"])
        self.assertNotIn("decoy.txt:", result["text"])

    def test_regex_shaped_query_does_not_run_as_a_regular_expression(self):
        self.write("bad.txt", "a" * 80000 + "!\n")
        start = time.monotonic()
        result = self.repo.search("(a+)+$", context=0)
        self.assertEqual(result["matches"], [])
        self.assertLess(time.monotonic() - start, 3)

    def test_search_output_budget_does_not_hide_large_strings_in_matches(self):
        self.write("verbose.txt", "".join(f"needle {n} " + "x" * 3000 + "\n" for n in range(50)))
        result = self.repo.search("needle", limit=50, context=2)
        self.assertLessEqual(len(result["text"]), result["output_budget_chars"])
        self.assertLessEqual(result["output_budget_chars"], 12000)
        self.assertTrue(result["truncated"])
        self.assertGreater(result["omitted_lines"], 0)
        self.assertTrue(all("text" not in match for match in result["matches"]))

    def test_query_budget_includes_long_glob_and_exclusion_metadata(self):
        self.write(".env", "hidden")
        self.write("binary", b"\x00payload")
        self.write("huge", b"x" * (2 * 1024 * 1024 + 1))
        self.write("source.txt", "needle " + "\nneedle ".join("x" * 40 for _ in range(700)))
        result = self.repo.search("needle", glob="*" * 512, limit=700, context=0)
        self.assertLessEqual(len(result["text"]), result["output_budget_chars"])

    def test_total_content_scan_limit_is_reported_as_partial(self):
        for n in range(9):
            self.write(f"f{n}.txt", b"x" * (2 * 1024 * 1024))
        result = self.repo.search("missing")
        self.assertLessEqual(result["scanned_bytes"], result["scan_byte_limit"])
        self.assertTrue(result["scan_limited"])
        self.assertFalse(result["complete"])
        self.assertIn("byte limit", result["limit_reason"])

    @unittest.skipUnless(shutil.which("git"), "git is needed for ignore integration")
    def test_gitignore_hides_untracked_files_but_keeps_tracked_sources(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.write(".gitignore", "ignored/\n*.log\n")
        self.write("ignored/private.py", "needle hidden\n")
        self.write("noise.log", "needle hidden\n")
        self.write("keep.py", "needle public\n")
        self.write("tracked.log", "needle tracked\n")
        subprocess.run(["git", "-C", str(self.root), "add", "-f", "tracked.log"], check=True)
        listing = self.repo.files()
        self.assertEqual(listing["source"], "git")
        self.assertIn("keep.py", listing["paths"])
        self.assertIn("tracked.log", listing["paths"])
        self.assertNotIn("ignored/private.py", listing["paths"])
        self.assertNotIn("noise.log", listing["paths"])
        self.assertEqual({m["path"] for m in self.repo.search("needle")["matches"]}, {"keep.py", "tracked.log"})
        with self.assertRaises(ValueError):
            self.repo.read("noise.log")

    def test_fallback_walk_respects_common_ignore_rules_and_labels_scope(self):
        self.write(".gitignore", "cache/\n*.tmp\n")
        self.write("cache/log.txt", "needle hidden\n")
        self.write("work.tmp", "needle hidden\n")
        self.write("ok.txt", "needle public\n")
        result = self.repo.files()
        self.assertEqual(result["source"], "walk")
        self.assertEqual(result["paths"], [".gitignore", "ok.txt"])
        self.assertIn("fallback", result["ignore_mode"].lower())

    def test_unreadable_fallback_ignore_rules_do_not_expose_ignored_content(self):
        self.write(".gitignore", "# padding\n" * 7000 + "hidden.txt\n")
        self.write("hidden.txt", "needle confidential\n")
        result = self.repo.search("needle")
        self.assertEqual(result["matches"], [])
        self.assertFalse(result["complete"])
        self.assertIn("ignore", result["limit_reason"])
        with self.assertRaises(ValueError):
            self.repo.read("hidden.txt")

    def test_unknown_binary_and_oversize_inputs_are_skipped_and_reported(self):
        self.write("unknown", b"needle\x00not-text")
        self.write("undecodable", b"needle\xff\xfe")
        self.write("large.txt", b"needle" + b"x" * (2 * 1024 * 1024))
        self.write("small.txt", "needle yes\n")
        result = self.repo.search("needle")
        self.assertEqual([m["path"] for m in result["matches"]], ["small.txt"])
        self.assertGreaterEqual(result["excluded"].get("binary", 0), 2)
        self.assertGreaterEqual(result["excluded"].get("oversize", 0), 1)
        for path in ("unknown", "undecodable", "large.txt"):
            with self.assertRaises(ValueError):
                self.repo.read(path)

    def test_secrets_and_generated_paths_are_excluded_even_when_named(self):
        self.write(".env", "needle SECRET\n")
        self.write("keys/service.pem", "needle SECRET\n")
        self.write("node_modules/pkg/index.js", "needle dependency\n")
        self.write("dist/app.js", "needle generated\n")
        self.write("src/app.py", "needle application\n")
        result = self.repo.search("needle")
        self.assertEqual([m["path"] for m in result["matches"]], ["src/app.py"])
        for path in (".env", "keys/service.pem", "node_modules/pkg/index.js", "dist/app.js"):
            with self.assertRaises(ValueError):
                self.repo.read(path)

    def test_common_package_manager_credentials_are_excluded(self):
        for name in (".npmrc", ".netrc", ".pypirc", ".envrc", "terraform.tfstate"):
            self.write(name, "needle SECRET\n")
        result = self.repo.search("needle")
        self.assertEqual(result["matches"], [])
        for name in (".npmrc", ".netrc", ".pypirc", ".envrc", "terraform.tfstate"):
            with self.assertRaises(ValueError):
                self.repo.read(name)

    def test_traversal_absolute_paths_and_symlink_escapes_are_rejected(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("needle outside secret\n")
        self.write("inside.txt", "inside\n")
        for path in ("../outside.txt", str(outside), "a/../../outside.txt", "C:\\outside.txt"):
            with self.assertRaises(ValueError):
                self.repo.read(path)
        if hasattr(os, "symlink"):
            (self.root / "escape.txt").symlink_to(outside)
            (self.root / "escape-dir").symlink_to(outside.parent, target_is_directory=True)
            for path in ("escape.txt", "escape-dir/outside.txt"):
                with self.assertRaises(ValueError):
                    self.repo.read(path)
            result = self.repo.search("needle")
            self.assertEqual(result["matches"], [])
            self.assertNotIn(str(outside), result["text"])

    def test_python_ast_outline_has_nested_symbols_and_exact_source_labels(self):
        self.write("module.py", "# note\nclass Service:\n    async def run(self):\n        return 1\n\ndef helper(value):\n    return value\n")
        result = self.repo.outline("module.py")
        self.assertEqual(result["method"], "python_ast")
        self.assertEqual([(s["name"], s["line"]) for s in result["symbols"]], [("Service", 2), ("Service.run", 3), ("helper", 6)])
        self.assertIn("module.py:3:     async def run(self):", result["text"])

    def test_nonpython_and_invalid_python_outlines_are_labeled_heuristic(self):
        self.write("app.ts", "// note\nexport class App {\n}\nexport function start() {\n}\n")
        self.write("broken.py", "def useful():\n    return (\n")
        for path, marker in (("app.ts", "app.ts:2:"), ("broken.py", "broken.py:1:")):
            result = self.repo.outline(path)
            self.assertEqual(result["method"], "regex_heuristic")
            self.assertIn("heuristic", result["text"].lower())
            self.assertIn(marker, result["text"])

    def test_glob_and_result_limits_report_partial_scope(self):
        for name in ("src/a.py", "src/b.py", "src/c.py", "notes.txt"):
            self.write(name, "needle\n")
        result = self.repo.files(glob="**/*.py", limit=2)
        self.assertEqual(result["paths"], ["src/a.py", "src/b.py"])
        self.assertTrue(result["truncated"])
        self.assertTrue(result["has_more"])
        result = self.repo.search("needle", glob="**/*.py", limit=1)
        self.assertEqual(len(result["matches"]), 1)
        self.assertTrue(result["truncated"])
        self.assertTrue(result["has_more"])

    def test_large_repository_reports_scan_limit_without_claiming_no_matches(self):
        for n in range(5002):
            self.write(f"f{n:05d}.txt", "nothing here\n")
        result = self.repo.search("absent")
        self.assertLessEqual(result["scanned_files"], 5000)
        self.assertTrue(result["scan_limited"])
        self.assertFalse(result["complete"])
        self.assertIn("limit", result["text"].lower())
        self.assertIn("partial", result["text"].lower())

    def test_bad_query_and_ranges_fail_without_scanning_or_reinterpreting_input(self):
        self.write("a.txt", "alpha\n")
        for invoke in (
            lambda: self.repo.search(""),
            lambda: self.repo.search("alpha", limit=0),
            lambda: self.repo.search("alpha", context=-1),
            lambda: self.repo.files(glob="../*"),
            lambda: self.repo.read("a.txt", start=0),
            lambda: self.repo.read("a.txt", start=3, end=2),
            lambda: self.repo.read("a.txt", max_chars=0),
        ):
            with self.assertRaises(ValueError):
                invoke()


if __name__ == "__main__":
    unittest.main()
