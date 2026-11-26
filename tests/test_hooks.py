"""Contract tests for the opt-in Claude output adapter."""

from copy import deepcopy
import importlib
import json
from pathlib import Path
import re
import tempfile
import unittest


class ClaudeHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        try:
            self.handle = importlib.import_module("leancontext.hooks").handle_claude_hook
        except ModuleNotFoundError:
            self.fail("The Claude result adapter has not been implemented")

    def payload(self, command="pytest -q"):
        return {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command, "timeout": 120000},
            "tool_use_id": "toolu_test",
            "session_id": "session-test",
            "cwd": str(self.root),
            "tool_response": {
                "stdout": ("progress: successful setup step\n" * 500)
                + "ERROR: important failure detail in test output\n"
                + ("progress: successful cleanup step\n" * 500),
                "stderr": "The shell directory was reset.",
                "interrupted": False,
                "isImage": False,
                "rawOutputPath": "/tmp/host-owned-output",
                "unknownFutureField": {"keep": [1, 2]},
            },
        }

    def test_large_success_is_bounded_preserves_metadata_and_archives_payload(self):
        payload = self.payload()
        original = deepcopy(payload)
        result = self.handle(payload, max_chars=1200, state_dir=self.state)
        specific = result["hookSpecificOutput"]
        self.assertEqual(specific["hookEventName"], "PostToolUse")
        self.assertNotIn("permissionDecision", specific)
        self.assertNotIn("updatedInput", specific)
        response = specific["updatedToolOutput"]
        self.assertLessEqual(len(response["stdout"]), 1200)
        self.assertIn("important failure detail", response["stdout"])
        for key, value in original["tool_response"].items():
            if key != "stdout":
                self.assertEqual(response[key], value, key)
        self.assertEqual(payload, original)
        raw_match = re.search(r"Raw Claude hook JSON: ([0-9a-f]{24})", response["stdout"])
        self.assertIsNotNone(raw_match)
        Store = importlib.import_module("leancontext.store").Store
        stored = Store(self.root, state_dir=self.state).read(raw_match.group(1))
        self.assertEqual(json.loads(stored), original)

    def test_noop_shapes_and_events_never_create_an_archive(self):
        variants = []
        for event in ("PreToolUse", "PostToolUseFailure", "Stop"):
            item = self.payload()
            item["hook_event_name"] = event
            variants.append(item)
        for response in (None, "Exit code 1\nFailed", [], {"stdout": "x" * 10000}):
            item = self.payload()
            item["tool_response"] = response
            variants.append(item)
        for key, value in (("isImage", True), ("interrupted", True),
                           ("backgroundTaskId", "task1"), ("exit_code", 1),
                           ("is_error", True), ("stdout", {"text": "wrong"})):
            item = self.payload()
            item["tool_response"][key] = value
            variants.append(item)
        item = self.payload()
        item["tool_name"] = "Read"
        variants.extend((item, {}, None, []))
        for payload in variants:
            with self.subTest(payload=repr(payload)[:100]):
                self.assertEqual(self.handle(payload, max_chars=1200, state_dir=self.state), {})
        self.assertFalse(self.state.exists())

    def test_small_results_are_not_wrapped_or_archived(self):
        payload = self.payload()
        payload["tool_response"]["stdout"] = "1 passed in 0.1s\n"
        self.assertEqual(self.handle(payload, state_dir=self.state), {})
        self.assertFalse(self.state.exists())

    def test_direct_source_diff_unknown_and_already_wrapped_commands_are_untouched(self):
        commands = (
            "cat src/app.py", "git diff", "git show HEAD:src/app.py",
            "git log --oneline --patch-with-stat", "git log --oneline -u",
            "rg --line-number def src", "python -c 'print(123)'",
            "npm run obscure", "pytest -q && cat src/app.py",
            "pytest -q | cat", "pytest -q; git diff", "pytest -q\ncat src/app.py",
            "leancontext run -- pytest -q", "python3 -m leancontext run -- pytest",
            "python3 /tmp/leancontext.pyz run -- pytest", "tail -100 src/app.py",
            "echo $(pytest -q)", "unknown-test-command",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.handle(self.payload(command), max_chars=1200,
                                             state_dir=self.state), {})
        self.assertFalse(self.state.exists())

    def test_known_noisy_command_classes_can_be_filtered(self):
        commands = (
            "pytest -q", "python3 -m pytest tests", "python -m unittest discover",
            "npm test", "npm run build", "pnpm test", "yarn build",
            "cargo test", "cargo build", "go test ./...", "go build ./...",
            "git status --short", "git log --oneline", "docker logs api",
            "tail -n 200 app.log", "journalctl -u app",
        )
        for command in commands:
            with self.subTest(command=command):
                result = self.handle(self.payload(command), max_chars=1200,
                                     state_dir=self.state)
                self.assertIn("updatedToolOutput", result.get("hookSpecificOutput", {}))

    def test_storage_failure_leaves_host_output_untouched(self):
        self.state.write_text("This is a file, so storage cannot initialize.")
        self.assertEqual(self.handle(self.payload(), max_chars=1200, state_dir=self.state), {})
        self.assertEqual(self.state.read_text(), "This is a file, so storage cannot initialize.")

    def test_invalid_budget_or_missing_working_directory_is_noop(self):
        for budget in (0, -1, 100, True, "1200"):
            self.assertEqual(self.handle(self.payload(), max_chars=budget,
                                         state_dir=self.state), {})
        payload = self.payload()
        payload.pop("cwd")
        self.assertEqual(self.handle(payload, state_dir=self.state), {})
        self.assertFalse(self.state.exists())

    def test_semantic_diagnostics_are_not_dropped_to_fit_hook_target(self):
        from tests.test_semantic import pytest_output
        payload = self.payload()
        payload['tool_response']['stdout'] = pytest_output()
        result = self.handle(payload, max_chars=1200, state_dir=self.state)
        text = result['hookSpecificOutput']['updatedToolOutput']['stdout']
        for i in range(25):
            self.assertIn(f'tests/test_bad.py::test_bad[{i}]', text)
        self.assertIn('target', text)
        self.assertLess(len(text), len(payload['tool_response']['stdout']))


if __name__ == "__main__":
    unittest.main()
