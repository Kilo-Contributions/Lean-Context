"""Behavioral tests for usage accounting; every fixture is local and explicit."""

import importlib
import json
from pathlib import Path
import tempfile
import unittest


def codex_meta(session, provider="openai"):
    return {"type": "session_meta", "payload": {"id": session, "model_provider": provider}}


def codex_model(model):
    return {"type": "turn_context", "payload": {"model": model}}


def cumulative(input_tokens, output_tokens, cached=0, reasoning=0, *, timestamp=None):
    row = {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": reasoning,
                    "total_tokens": input_tokens + output_tokens,
                },
                # This is an alternative view of usage, not extra usage to add.
                "last_token_usage": {"input_tokens": 9, "output_tokens": 3},
            },
        },
    }
    if timestamp is not None:
        row["timestamp"] = timestamp
    return row


def claude_message(message_id, input_tokens, output_tokens, cached=0, created=0, **extra):
    return {
        "type": "assistant",
        "sessionId": "claude-session",
        "message": {
            "id": message_id,
            "role": "assistant",
            "model": "claude-test-model",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cached,
                "cache_creation_input_tokens": created,
            },
        },
        **extra,
    }


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, rows):
        path = self.root / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return path

    def module(self):
        try:
            return importlib.import_module("leancontext.audit")
        except ModuleNotFoundError as exc:
            if exc.name != "leancontext.audit":
                raise
            self.fail("The usage auditor has not been implemented yet")

    def audit(self, *paths):
        return self.module().audit_paths(list(paths))

    def test_codex_cumulative_snapshots_are_not_added_together(self):
        path = self.write("rollout.jsonl", [
            codex_meta("s1"), codex_model("gpt-test"),
            cumulative(100, 20, 60, 8),
            cumulative(180, 40, 110, 15),
            cumulative(180, 40, 110, 15),
        ])
        report = self.audit(path)
        self.assertEqual(report["totals"]["input_tokens"], 180)
        self.assertEqual(report["totals"]["output_tokens"], 40)
        self.assertEqual(report["totals"]["cached_input_tokens"], 110)
        self.assertEqual(report["totals"]["reasoning_output_tokens"], 15)
        self.assertEqual(report["totals"]["total_tokens"], 220)

    def test_codex_resets_start_a_new_counter_epoch(self):
        path = self.write("reset.jsonl", [codex_meta("s1"),
            cumulative(100, 10), cumulative(150, 20),
            cumulative(20, 5), cumulative(40, 7)])
        report = self.audit(path)
        self.assertEqual(report["totals"]["input_tokens"], 190)
        self.assertEqual(report["totals"]["output_tokens"], 27)
        self.assertTrue(any("reset" in warning.lower() for warning in report["warnings"]))

    def test_overlapping_files_are_ordered_and_deduplicated_by_session(self):
        early = cumulative(100, 10, timestamp="2026-01-01T00:00:01Z")
        middle = cumulative(150, 20, timestamp="2026-01-01T00:00:02Z")
        late = cumulative(180, 30, timestamp="2026-01-01T00:00:03Z")
        first = self.write("first.jsonl", [codex_meta("s1"), early, middle])
        second = self.write("second.jsonl", [codex_meta("s1"), middle, late])
        report = self.audit(second, first)
        self.assertEqual(report["totals"]["input_tokens"], 180)
        self.assertEqual(report["totals"]["output_tokens"], 30)
        self.assertEqual(len(report["sessions"]), 1)
        self.assertFalse(any("reset" in warning.lower() for warning in report["warnings"]))

    def test_untimed_cumulative_overlaps_never_invent_a_reset_or_comparable_total(self):
        first = self.write("early.jsonl", [codex_meta("s1"), codex_model("gpt-test"),
            cumulative(100, 10), cumulative(150, 20)])
        second = self.write("late.jsonl", [codex_meta("s1"), codex_model("gpt-test"),
            cumulative(150, 20), cumulative(180, 30)])
        before = self.audit(first)
        for paths in ((first, second), (second, first)):
            with self.subTest(paths=paths):
                report = self.audit(*paths)
                self.assertIsNone(report["totals"]["input_tokens"])
                self.assertIsNone(report["totals"]["output_tokens"])
                self.assertIsNone(report["totals"]["total_tokens"])
                self.assertIsNone(report["groups"][0]["input_tokens"])
                self.assertEqual(report["coverage"]["counter_resets"], 0)
                self.assertTrue(any("unproven" in warning.lower() for warning in report["warnings"]))
                comparison = self.module().compare_reports(before, report)
                self.assertIsNone(comparison["metrics"]["input_tokens"]["delta"])
                self.assertIsNone(comparison["metrics"]["input_tokens"]["reduction_pct"])

    def test_model_change_attributes_only_new_cumulative_usage(self):
        path = self.write("models.jsonl", [codex_meta("s1"),
            codex_model("first-model"), cumulative(100, 20),
            codex_model("second-model"), cumulative(150, 30)])
        groups = {row["model"]: row for row in self.audit(path)["groups"]}
        self.assertEqual(groups["first-model"]["input_tokens"], 100)
        self.assertEqual(groups["second-model"]["input_tokens"], 50)
        self.assertEqual(groups["second-model"]["output_tokens"], 10)

    def test_subset_only_cumulative_updates_reach_model_groups(self):
        path = self.write("subsets.jsonl", [codex_meta("s1"), codex_model("gpt-test"),
            cumulative(100, 20, 0, 0), cumulative(100, 20, 60, 0),
            cumulative(100, 20, 60, 8)])
        report = self.audit(path)
        group = report["groups"][0]
        self.assertEqual(group["input_tokens"], 100)
        self.assertEqual(group["output_tokens"], 20)
        self.assertEqual(group["total_tokens"], 120)
        self.assertEqual(report["totals"]["cached_input_tokens"], 60)
        self.assertEqual(group["cached_input_tokens"], 60)
        self.assertEqual(group["reasoning_output_tokens"], 8)

    def test_subset_only_decrease_makes_model_attribution_unknown_not_stale(self):
        path = self.write("corrected-subset.jsonl", [codex_meta("s1"), codex_model("gpt-test"),
            cumulative(100, 20, 60), cumulative(100, 20, 40)])
        report = self.audit(path)
        self.assertEqual(report["totals"]["cached_input_tokens"], 40)
        self.assertEqual(report["groups"][0]["input_tokens"], 100)
        self.assertIsNone(report["groups"][0]["cached_input_tokens"])

    def test_codex_provider_metadata_is_not_relabelled_openai(self):
        path = self.write("provider.jsonl", [codex_meta("s1", "local-provider"),
            codex_model("local-model"), cumulative(100, 20)])
        self.assertEqual(self.audit(path)["groups"][0]["provider"], "local-provider")

    def test_claude_streamed_message_uses_final_snapshot_not_sum_or_max(self):
        path = self.write("claude.jsonl", [
            claude_message("m1", 100, 5, 20, 10),
            claude_message("m1", 80, 15, 30, 5),
        ])
        report = self.audit(path)
        self.assertEqual(report["totals"]["input_tokens"], 115)
        self.assertEqual(report["totals"]["output_tokens"], 15)
        self.assertEqual(report["totals"]["cached_input_tokens"], 30)
        self.assertEqual(report["totals"]["cache_creation_input_tokens"], 5)
        self.assertEqual(report["totals"]["total_tokens"], 130)
        self.assertIsNone(report["totals"]["reasoning_output_tokens"])

    def test_claude_message_snapshots_deduplicate_across_files(self):
        first = self.write("a.jsonl", [claude_message("m1", 10, 2, 20, 5,
            timestamp="2026-01-01T00:00:01Z")])
        second = self.write("b.jsonl", [
            claude_message("m1", 10, 8, 20, 5, timestamp="2026-01-01T00:00:02Z"),
            claude_message("m2", 40, 7, 60, 10, timestamp="2026-01-01T00:00:03Z"),
        ])
        report = self.audit(second, first)
        self.assertEqual(report["totals"]["input_tokens"], 145)
        self.assertEqual(report["totals"]["output_tokens"], 15)

    def test_codex_exec_usage_counts_each_completed_turn_and_cache_as_subset(self):
        path = self.write("exec.jsonl", [
            {"type": "thread.started", "thread_id": "t1"},
            {"type": "turn.completed", "turn_id": "one", "model": "gpt-test",
             "usage": {"input_tokens": 100, "cached_input_tokens": 60,
                       "output_tokens": 20, "output_tokens_details": {"reasoning_tokens": 10}}},
            {"type": "turn.completed", "turn_id": "two", "model": "gpt-test",
             "usage": {"input_tokens": 50, "cached_input_tokens": 20,
                       "output_tokens": 10, "output_tokens_details": {"reasoning_tokens": 5}}},
        ])
        report = self.audit(path)
        self.assertEqual(report["totals"]["input_tokens"], 150)
        self.assertEqual(report["totals"]["output_tokens"], 30)
        self.assertEqual(report["totals"]["cached_input_tokens"], 80)
        self.assertEqual(report["totals"]["reasoning_output_tokens"], 15)
        self.assertEqual(report["totals"]["total_tokens"], 180)

    def test_identical_usage_on_two_unidentified_turns_is_still_two_turns(self):
        turn = {"type": "turn.completed", "usage": {"input_tokens": 50, "output_tokens": 10}}
        path = self.write("equal-turns.jsonl", [{"type": "thread.started", "thread_id": "t1"}, turn, turn])
        report = self.audit(path)
        self.assertEqual(report["totals"]["input_tokens"], 100)
        self.assertEqual(report["totals"]["output_tokens"], 20)
        self.assertIsNone(report["totals"]["cached_input_tokens"])

    def test_duplicate_file_without_session_id_is_not_counted_twice(self):
        rows = [{"type": "turn.completed", "usage": {"input_tokens": 50, "output_tokens": 10}}]
        first = self.write("a.jsonl", rows)
        duplicate = self.write("copied.jsonl", rows)
        report = self.audit(first, first, duplicate)
        self.assertEqual(report["totals"]["input_tokens"], 50)

    def test_exec_turn_identity_deduplicates_overlapping_files(self):
        turn = {"type": "turn.completed", "turn_id": "one", "usage": {"input_tokens": 50, "output_tokens": 10}}
        first = self.write("a.jsonl", [{"type": "thread.started", "thread_id": "t1"}, turn])
        second = self.write("b.jsonl", [{"type": "thread.started", "thread_id": "t1"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "ignored"}}, turn])
        self.assertEqual(self.audit(first, second)["totals"]["input_tokens"], 50)

    def test_cumulative_and_turn_views_of_same_session_are_not_added(self):
        rollout = self.write("rollout.jsonl", [codex_meta("s1"), cumulative(100, 20)])
        export = self.write("export.jsonl", [{"type": "thread.started", "thread_id": "s1"},
            {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}])
        report = self.audit(rollout, export)
        self.assertEqual(report["totals"]["input_tokens"], 100)
        self.assertTrue(any("alternative" in warning.lower() for warning in report["warnings"]))

    def test_unknown_and_malformed_usage_are_reported_without_inventing_totals(self):
        path = self.write("unknown.jsonl", [
            {"type": "new-format", "usage": {"input_tokens": 900, "output_tokens": 30}},
            {"type": "turn.completed", "usage": {"input_tokens": True, "output_tokens": -2}},
        ])
        with path.open("a", encoding="utf-8") as stream:
            stream.write("this is not json and must not appear in warnings\n")
        report = self.audit(path)
        self.assertIsNone(report["totals"]["input_tokens"])
        self.assertIsNone(report["totals"]["output_tokens"])
        self.assertFalse(report["complete"])
        warnings = " ".join(report["warnings"]).lower()
        self.assertIn("unknown", warnings)
        self.assertIn("invalid", warnings)
        self.assertNotIn("must not appear", warnings)

    def test_only_explicit_regular_files_are_read_not_a_directory(self):
        self.write("unrequested.jsonl", [codex_meta("s1"), cumulative(500, 40)])
        report = self.audit(self.root, self.root / "missing.jsonl")
        self.assertIsNone(report["totals"]["input_tokens"])
        self.assertTrue(report["warnings"])

    def test_unresolvable_symlink_is_reported_without_aborting_other_files(self):
        loop = self.root / "loop.jsonl"
        loop.symlink_to(loop.name)
        valid = self.write("valid.jsonl", [codex_meta("s1"), cumulative(100, 20)])
        report = self.audit(loop, valid)
        self.assertEqual(report["totals"]["input_tokens"], 100)
        self.assertTrue(any("loop.jsonl" in warning for warning in report["warnings"]))

    def test_identical_unidentified_files_disclose_deduplication_assumption(self):
        rows = [{"type": "turn.completed", "usage": {"input_tokens": 50, "output_tokens": 10}}]
        first, second = self.write("a.jsonl", rows), self.write("b.jsonl", rows)
        report = self.audit(first, second)
        self.assertEqual(report["totals"]["input_tokens"], 50)
        self.assertTrue(any("identical" in warning.lower() for warning in report["warnings"]))

    def test_partial_records_do_not_hide_other_valid_usage(self):
        path = self.write("partial.jsonl", [codex_meta("s1"), cumulative(100, 20),
            {"type": "event_msg", "payload": {"type": "token_count", "info": None}}])
        report = self.audit(path)
        self.assertEqual(report["totals"]["input_tokens"], 100)
        self.assertFalse(report["complete"])

    def test_invalid_cache_subset_is_unknown_instead_of_double_counted(self):
        path = self.write("invalid-cache.jsonl", [{"type": "turn.completed",
            "usage": {"input_tokens": 50, "output_tokens": 10, "cached_input_tokens": 80}}])
        report = self.audit(path)
        self.assertEqual(report["totals"]["input_tokens"], 50)
        self.assertEqual(report["totals"]["total_tokens"], 60)
        self.assertIsNone(report["totals"]["cached_input_tokens"])
        self.assertTrue(any("cached" in warning.lower() for warning in report["warnings"]))

    def test_compare_reports_keeps_input_output_and_subsets_separate(self):
        before = {"totals": {"input_tokens": 1000, "output_tokens": 100,
                             "cached_input_tokens": 800, "total_tokens": 1100}}
        after = {"totals": {"input_tokens": 600, "output_tokens": 120,
                            "cached_input_tokens": 400, "total_tokens": 720}}
        result = self.module().compare_reports(before, after)
        self.assertEqual(result["metrics"]["input_tokens"]["delta"], -400)
        self.assertEqual(result["metrics"]["input_tokens"]["reduction_pct"], 40.0)
        self.assertEqual(result["metrics"]["output_tokens"]["reduction_pct"], -20.0)
        self.assertNotIn("cost", result)
        self.assertNotIn("quota", result)

    def test_compare_unknown_or_zero_baselines_does_not_claim_a_percentage(self):
        result = self.module().compare_reports(
            {"totals": {"input_tokens": 0, "output_tokens": None}},
            {"totals": {"input_tokens": 20, "output_tokens": 5}})
        self.assertIsNone(result["metrics"]["input_tokens"]["reduction_pct"])
        self.assertEqual(result["metrics"]["input_tokens"]["delta"], 20)
        self.assertIsNone(result["metrics"]["output_tokens"]["delta"])
        self.assertIsNone(result["metrics"]["output_tokens"]["reduction_pct"])

    def test_compare_changed_model_warns_that_workloads_may_not_be_comparable(self):
        before = {"totals": {"input_tokens": 100, "output_tokens": 20},
                  "groups": [{"provider": "openai", "model": "model-a"}]}
        after = {"totals": {"input_tokens": 80, "output_tokens": 20},
                 "groups": [{"provider": "openai", "model": "model-b"}]}
        result = self.module().compare_reports(before, after)
        self.assertTrue(any("model" in warning.lower() for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
