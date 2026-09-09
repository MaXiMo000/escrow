"""Run: python tests/test_duration.py"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from escrow.duration import DurationError, parse_duration


class TestParseDuration(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(parse_duration("30s"), 30)

    def test_minutes(self):
        self.assertEqual(parse_duration("45m"), 45 * 60)

    def test_hours(self):
        self.assertEqual(parse_duration("26h"), 26 * 3600)

    def test_days(self):
        self.assertEqual(parse_duration("8d"), 8 * 86400)

    def test_weeks(self):
        self.assertEqual(parse_duration("2w"), 2 * 604800)

    def test_decimal_value(self):
        self.assertEqual(parse_duration("1.5h"), 1.5 * 3600)

    def test_whitespace_is_stripped(self):
        self.assertEqual(parse_duration("  26h  "), 26 * 3600)

    def test_combined_units_are_rejected_with_a_clear_message(self):
        with self.assertRaises(DurationError) as ctx:
            parse_duration("1d12h")
        self.assertIn("not a duration", str(ctx.exception))
        self.assertIn("36h", str(ctx.exception))  # the suggested workaround

    def test_missing_unit_is_rejected(self):
        with self.assertRaises(DurationError):
            parse_duration("26")

    def test_unknown_unit_is_rejected(self):
        with self.assertRaises(DurationError):
            parse_duration("26x")

    def test_empty_string_is_rejected(self):
        with self.assertRaises(DurationError):
            parse_duration("")


if __name__ == "__main__":
    unittest.main()
