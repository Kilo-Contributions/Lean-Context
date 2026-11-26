"""Equal timestamps are not unique request IDs."""
import unittest
from leancontext.audit import METRICS, _Usage, _deduplicate


def row(path, line, timestamp=123.0, identity=None, input_tokens=10):
    metrics = dict.fromkeys(METRICS, 0)
    metrics.update(input_tokens=input_tokens, output_tokens=2, total_tokens=input_tokens+2)
    return _Usage('codex', 'codex_exec', 'session', 'provider', 'model', metrics,
                  identity, timestamp, path, line)


class AnonymousMultiplicityTests(unittest.TestCase):
    def test_same_timestamp_in_one_file_counts_both(self):
        rows = [row('a', 1), row('a', 2)]
        warnings = []
        kept, removed = _deduplicate(rows, warnings, 'session')
        self.assertEqual(kept, rows)
        self.assertEqual(removed, 0)

    def test_timestamped_overlap_preserves_max_multiplicity_and_warns(self):
        rows = [row('a', 1), row('a', 2), row('b', 1)]
        warnings = []
        kept, removed = _deduplicate(rows, warnings, 'session')
        self.assertEqual(kept, rows[:2])
        self.assertEqual(removed, 1)
        self.assertTrue(any('timestamped' in w and 'heuristically' in w for w in warnings))

    def test_larger_overlap_in_second_file_preserved(self):
        rows = [row('a', 1), row('b', 1), row('b', 2)]
        kept, removed = _deduplicate(rows, [], 'session')
        self.assertEqual(kept, [rows[0], rows[2]])
        self.assertEqual(removed, 1)

    def test_different_timestamps_are_not_collapsed(self):
        rows = [row('a', 1), row('b', 1, timestamp=124.0)]
        self.assertEqual(_deduplicate(rows, [], 'session'), (rows, 0))

    def test_untimed_overlap_behavior_unchanged(self):
        rows = [row('a', 1, None), row('a', 2, None), row('b', 1, None)]
        warnings = []
        self.assertEqual(_deduplicate(rows, warnings, 'session'), (rows[:2], 1))
        self.assertTrue(any('untimed' in w for w in warnings))

    def test_identified_correction_still_replaces_snapshot(self):
        rows = [row('a', 1, identity='id', input_tokens=12),
                row('b', 1, identity='id', input_tokens=7)]
        self.assertEqual(_deduplicate(rows, [], 'session'), ([rows[1]], 1))
