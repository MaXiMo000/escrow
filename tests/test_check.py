"""Run: python tests/test_check.py"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from escrow.check import NEVER_SEEN, OK, OVERDUE, check_jobs


def _job(name: str, interval: str, seconds: float) -> dict:
    return {"name": name, "interval": interval, "interval_seconds": seconds}


class TestCheckJobs(unittest.TestCase):
    def test_recently_pinged_job_is_ok(self):
        jobs = [_job("nightly-backup", "26h", 26 * 3600)]
        state = {"nightly-backup": {"last_seen": 1000.0}}
        results = check_jobs(jobs, state, now=1000.0 + 3600)  # 1h ago
        self.assertEqual(results[0]["status"], OK)

    def test_a_job_past_its_interval_is_overdue(self):
        jobs = [_job("nightly-backup", "26h", 26 * 3600)]
        state = {"nightly-backup": {"last_seen": 1000.0}}
        results = check_jobs(jobs, state, now=1000.0 + 30 * 3600)  # 30h ago
        self.assertEqual(results[0]["status"], OVERDUE)
        self.assertGreater(results[0]["overdue_by_seconds"], 0)

    def test_exactly_at_the_interval_boundary_is_still_ok(self):
        """The whole point of an interval is 'up to and including this
        long is fine' -- past it, not at it, is what makes something
        overdue."""
        jobs = [_job("x", "1h", 3600)]
        state = {"x": {"last_seen": 1000.0}}
        results = check_jobs(jobs, state, now=1000.0 + 3600)
        self.assertEqual(results[0]["status"], OK)

    def test_a_job_that_never_pinged_is_never_seen_not_overdue(self):
        """A different status on purpose: 'stopped running' and 'never
        started' are different facts an operator needs to act on
        differently, and collapsing them into one status would hide that."""
        jobs = [_job("weekly-report", "8d", 8 * 86400)]
        results = check_jobs(jobs, state={}, now=1000.0)
        self.assertEqual(results[0]["status"], NEVER_SEEN)
        self.assertIsNone(results[0]["last_seen"])

    def test_multiple_jobs_are_each_evaluated_independently(self):
        jobs = [
            _job("a", "1h", 3600),
            _job("b", "1h", 3600),
            _job("c", "1h", 3600),
        ]
        state = {
            "a": {"last_seen": 1000.0},           # will be ok
            "b": {"last_seen": 1000.0 - 10000},   # will be overdue
            # "c" never pinged
        }
        results = check_jobs(jobs, state, now=1000.0)
        statuses = {r["name"]: r["status"] for r in results}
        self.assertEqual(statuses, {"a": OK, "b": OVERDUE, "c": NEVER_SEEN})


if __name__ == "__main__":
    unittest.main()
