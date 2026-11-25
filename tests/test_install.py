"""Project-only installation, preservation, and dry-run behavior."""

import importlib
import json
from pathlib import Path
import shlex
import tempfile
import tomllib
import unittest


class ProjectInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project with spaces"
        self.root.mkdir()
        self.launcher = ["/opt/python 3/bin/python3", "/tools/lean context.pyz"]
        try:
            self.setup = importlib.import_module("leancontext.install").setup_project
        except ModuleNotFoundError:
            self.fail("The project installer has not been implemented")

    def test_dry_run_returns_reviewable_changes_without_creating_files(self):
        report = self.setup(self.root, "both", self.launcher, hooks=True, dry_run=True)
        self.assertTrue(report["changed"])
        self.assertTrue(report["dry_run"])
        paths = {Path(item["path"]).relative_to(self.root).as_posix()
                 for item in report["files"]}
        self.assertEqual(paths, {".codex/config.toml", "AGENTS.md", ".mcp.json",
                                 "CLAUDE.md", ".claude/settings.local.json"})
        self.assertTrue(all(item["content"] for item in report["files"]))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_codex_configuration_uses_exact_launcher_and_preserves_existing_config(self):
        target = self.root / ".codex/config.toml"
        target.parent.mkdir()
        prior = (b'# keep this comment\r\nmodel = "my-model"\r\n'
                 b'[mcp_servers.other]\r\ncommand = "existing"\r\n'
                 b'args = ["--old"]\r\n')
        target.write_bytes(prior)
        instructions = self.root / "AGENTS.md"
        instructions.write_bytes(b"# Existing instructions\nKeep my workflow.\n")
        report = self.setup(self.root, "codex", self.launcher)
        config = tomllib.loads(target.read_text())
        self.assertEqual(config["model"], "my-model")
        self.assertEqual(config["mcp_servers"]["other"],
                         {"command": "existing", "args": ["--old"]})
        server = config["mcp_servers"]["leancontext"]
        self.assertEqual(server["command"], self.launcher[0])
        self.assertEqual(server["args"], self.launcher[1:] + ["serve", "--root", str(self.root)])
        self.assertNotIn("approval_mode", server)
        self.assertIn(b"# keep this comment\r\n", target.read_bytes())
        self.assertTrue(instructions.read_text().startswith("# Existing instructions\nKeep my workflow.\n"))
        self.assertIn(shlex.join(self.launcher + ["run", "--root", str(self.root), "--"]),
                      instructions.read_text())
        entry = next(item for item in report["files"] if item["path"] == str(target))
        self.assertEqual(Path(entry["backup"]).read_bytes(), prior)
        self.assertFalse((self.root / ".claude").exists())
        self.assertFalse((self.root / ".mcp.json").exists())

    def test_claude_merges_servers_hooks_and_preserves_permissions(self):
        mcp = self.root / ".mcp.json"
        old_mcp = {"mcpServers": {"other": {"command": "other", "args": ["--keep"]}},
                   "extra": {"keep": True}}
        mcp.write_text(json.dumps(old_mcp))
        settings = self.root / ".claude/settings.local.json"
        settings.parent.mkdir()
        old_settings = {
            "permissions": {"deny": ["Bash(rm *)"], "ask": ["Bash(git push *)"]},
            "hooks": {"PreToolUse": [{"matcher": "Write", "hooks": [
                {"type": "command", "command": "check-write"}]}],
                "PostToolUse": [{"matcher": "Edit", "hooks": [
                    {"type": "command", "command": "format-file"}]}]},
            "customSetting": "keep",
        }
        settings.write_text(json.dumps(old_settings))
        self.setup(self.root, "claude", self.launcher, hooks=True)
        updated = json.loads(settings.read_text())
        self.assertEqual(updated["permissions"], old_settings["permissions"])
        self.assertEqual(updated["hooks"]["PreToolUse"], old_settings["hooks"]["PreToolUse"])
        self.assertEqual(updated["hooks"]["PostToolUse"][0],
                         old_settings["hooks"]["PostToolUse"][0])
        self.assertEqual(updated["customSetting"], "keep")
        hook = updated["hooks"]["PostToolUse"][-1]
        self.assertEqual(hook["matcher"], "^Bash$")
        self.assertEqual(shlex.split(hook["hooks"][0]["command"]),
                         self.launcher + ["hook", "claude"])
        updated_mcp = json.loads(mcp.read_text())
        self.assertEqual(updated_mcp["extra"], old_mcp["extra"])
        self.assertEqual(updated_mcp["mcpServers"]["other"], old_mcp["mcpServers"]["other"])
        self.assertEqual(updated_mcp["mcpServers"]["leancontext"]["command"], self.launcher[0])

    def test_reinstall_is_idempotent_and_does_not_create_more_backups(self):
        self.setup(self.root, "both", self.launcher, hooks=True)
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        report = self.setup(self.root, "both", self.launcher, hooks=True)
        after = {str(path.relative_to(self.root)): path.read_bytes()
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertFalse(report["changed"])
        self.assertEqual(before, after)

    def test_launcher_update_replaces_only_managed_settings_and_instruction_block(self):
        self.setup(self.root, "both", self.launcher, hooks=True)
        agents = self.root / "AGENTS.md"
        agents.write_text("before\n" + agents.read_text() + "\nafter\n")
        config = self.root / ".codex/config.toml"
        config.write_text(config.read_text() + '\n[mcp_servers.leancontext.env]\nKEEP = "yes"\n')
        new_launcher = ["/new/python", "/new/leancontext.pyz"]
        self.setup(self.root, "both", new_launcher, hooks=True)
        text = agents.read_text()
        self.assertTrue(text.startswith("before\n"))
        self.assertTrue(text.endswith("\nafter\n"))
        self.assertEqual(text.count("<!-- leancontext:begin -->"), 1)
        self.assertNotIn(self.launcher[1], text)
        parsed = tomllib.loads(config.read_text())
        self.assertEqual(parsed["mcp_servers"]["leancontext"]["env"], {"KEEP": "yes"})
        settings = json.loads((self.root / ".claude/settings.local.json").read_text())
        self.assertEqual(len(settings["hooks"]["PostToolUse"]), 1)
        self.assertEqual(shlex.split(settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]),
                         new_launcher + ["hook", "claude"])

    def test_existing_named_codex_server_keeps_unrelated_options(self):
        config = self.root / ".codex/config.toml"
        config.parent.mkdir()
        config.write_text('[mcp_servers.leancontext]\ncommand = "old"\nargs = [\n "one",\n "two",\n]\n'
                          'tool_timeout_sec = 45\nenabled = false\n'
                          '[mcp_servers.leancontext.env]\nCUSTOM = "kept"\n'
                          '[mcp_servers.other]\ncommand = "other"\n')
        self.setup(self.root, "codex", self.launcher)
        parsed = tomllib.loads(config.read_text())
        server = parsed["mcp_servers"]["leancontext"]
        self.assertEqual(server["tool_timeout_sec"], 45)
        self.assertFalse(server["enabled"])
        self.assertEqual(server["env"], {"CUSTOM": "kept"})
        self.assertEqual(parsed["mcp_servers"]["other"], {"command": "other"})

    def test_invalid_configuration_is_detected_before_any_files_change(self):
        target = self.root / ".mcp.json"
        target.write_bytes(b"{not valid json}")
        with self.assertRaises(ValueError):
            self.setup(self.root, "both", self.launcher, hooks=True)
        self.assertEqual([path.name for path in self.root.iterdir()], [".mcp.json"])
        self.assertEqual(target.read_bytes(), b"{not valid json}")

    def test_invalid_toml_or_instruction_markers_leave_existing_file_intact(self):
        target = self.root / ".codex/config.toml"
        target.parent.mkdir()
        target.write_bytes(b"invalid = [")
        with self.assertRaises(ValueError):
            self.setup(self.root, "codex", self.launcher)
        self.assertEqual(target.read_bytes(), b"invalid = [")
        target.unlink()
        agents = self.root / "AGENTS.md"
        agents.write_text("before\n<!-- leancontext:begin -->\nunfinished")
        with self.assertRaises(ValueError):
            self.setup(self.root, "codex", self.launcher)
        self.assertFalse(target.exists())
        self.assertTrue(agents.read_text().endswith("unfinished"))

    def test_symbolic_link_targets_are_not_followed(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (self.root / ".codex").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.setup(self.root, "codex", self.launcher)
        self.assertEqual(list(outside.iterdir()), [])

    def test_non_directory_config_parent_is_rejected_during_preflight(self):
        (self.root / ".claude").write_bytes(b"keep this ordinary file")
        with self.assertRaises(ValueError):
            self.setup(self.root, "both", self.launcher, hooks=True)
        self.assertEqual([path.name for path in self.root.iterdir()], [".claude"])
        self.assertEqual((self.root / ".claude").read_bytes(), b"keep this ordinary file")

    def test_empty_existing_server_header_without_final_newline_is_supported(self):
        config = self.root / ".codex/config.toml"
        config.parent.mkdir()
        config.write_bytes(b"[mcp_servers.leancontext]")
        self.setup(self.root, "codex", self.launcher)
        server = tomllib.loads(config.read_text())["mcp_servers"]["leancontext"]
        self.assertEqual(server["command"], self.launcher[0])
        self.assertEqual(server["args"], self.launcher[1:] + ["serve", "--root", str(self.root)])

    def test_invalid_launcher_and_codex_hooks_do_not_write(self):
        for launcher in ([], [""], ["python", 1], "python"):
            with self.subTest(launcher=launcher), self.assertRaises(ValueError):
                self.setup(self.root, "codex", launcher)
        with self.assertRaises(ValueError):
            self.setup(self.root, "codex", self.launcher, hooks=True)
        with self.assertRaises(ValueError):
            self.setup(self.root, "unknown", self.launcher)
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
