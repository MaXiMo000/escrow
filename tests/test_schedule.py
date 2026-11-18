"""Run: python tests/test_schedule.py

Jobs declared with a cron `schedule:` instead of an `interval:`.
2026-11-13 is a Friday; 2026-11-16 the Monday after.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import tempfile
import unittest
import zoneinfo

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from escrow.check import OK, OVERDUE, check_jobs
from escrow.config import ConfigError, load_config
from escrow.cron import previous_fire

UTC = dt.timezone.utc
WEEKDAYS_2AM = "0 2 * * 1-5"


def at(day: int, hour: int, minute: int = 0, tz=UTC) -> dt.datetime:
    return dt.datetime(2026, 11, day, hour, minute, tzinfo=tz)


class TestPreviousFire(unittest.TestCase):
    def test_a_weekday_schedule_on_a_sunday_points_at_friday(self):
        self.assertEqual(previous_fire(WEEKDAYS_2AM, at(15, 12)), at(13, 2))

    def test_the_exact_fire_time_counts(self):
        self.assertEqual(previous_fire(WEEKDAYS_2AM, at(16, 2)), at(16, 2))
        self.assertEqual(previous_fire(WEEKDAYS_2AM, at(16, 1, 59)), at(13, 2))

    def test_wall_time_is_read_in_the_jobs_timezone(self):
        berlin = zoneinfo.ZoneInfo("Europe/Berlin")  # UTC+1 in November
        fired = previous_fire(WEEKDAYS_2AM, at(16, 3), berlin)
        self.assertEqual(fired.astimezone(UTC), at(16, 1))

    def test_a_schedule_that_never_fires_is_none(self):
        self.assertIsNone(previous_fire("0 0 31 2 *", at(16, 0)))


class TestScheduledJobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, body: str) -> list[dict]:
        path = pathlib.Path(self.tmp.name) / "escrow.yaml"
        path.write_text("jobs:\n  - name: backup\n" + body)
        return load_config(str(path))

    def status(self, jobs, last_seen: dt.datetime, now: dt.datetime) -> dict:
        state = {"backup": {"last_seen": last_seen.timestamp(), "last_status": "ok"}}
        return check_jobs(jobs, state, now.timestamp())[0]

    def test_quiet_over_a_weekend_is_not_late(self):
        jobs = self.load(f"    schedule: '{WEEKDAYS_2AM}'\n")
        self.assertEqual(self.status(jobs, at(13, 2, 10), at(15, 23))["status"], OK)

    def test_a_missed_monday_run_is_overdue_once_grace_passes(self):
        jobs = self.load(f"    schedule: '{WEEKDAYS_2AM}'\n")
        self.assertEqual(self.status(jobs, at(13, 2, 10), at(16, 2, 59))["status"], OK)  # within 1h grace
        result = self.status(jobs, at(13, 2, 10), at(16, 3, 1))
        self.assertEqual(result["status"], OVERDUE)
        self.assertIn("missed its run due 2026-11-16 02:00 UTC", result["detail"])
        self.assertAlmostEqual(result["overdue_by_seconds"], 60)

    def test_a_ping_after_the_run_is_on_schedule(self):
        jobs = self.load(f"    schedule: '{WEEKDAYS_2AM}'\n")
        self.assertEqual(self.status(jobs, at(16, 2, 5), at(16, 9))["status"], OK)

    def test_grace_and_timezone_are_honoured(self):
        jobs = self.load(f"    schedule: '{WEEKDAYS_2AM}'\n    grace: 30m\n    timezone: Europe/Berlin\n")
        # 02:00 Berlin is 01:00 UTC; 30m grace ends 01:30 UTC.
        self.assertEqual(self.status(jobs, at(13, 1, 5), at(16, 1, 29))["status"], OK)
        result = self.status(jobs, at(13, 1, 5), at(16, 1, 31))
        self.assertEqual(result["status"], OVERDUE)
        self.assertIn("02:00 Europe/Berlin", result["detail"])

    def test_bad_declarations_are_config_errors(self):
        for body, message in [
            ("    interval: 26h\n    schedule: '0 2 * * *'\n", "exactly one"),
            ("    grace: 1h\n", "exactly one"),
            ("    schedule: '0 2 * *'\n", "five fields"),
            ("    schedule: '0 25 * * *'\n", "out of range"),
            ("    schedule: '0 0 31 2 *'\n", "never fires"),
            ("    schedule: '0 2 * * *'\n    timezone: Mars/Olympus\n", "unknown timezone"),
            ("    schedule: '0 2 * * *'\n    grace: soon\n", "backup"),
        ]:
            with self.subTest(body=body), self.assertRaises(ConfigError) as caught:
                self.load(body)
            self.assertIn(message, str(caught.exception))

    def test_interval_jobs_are_unchanged(self):
        [job] = self.load("    interval: 26h\n")
        self.assertEqual(job, {"name": "backup", "interval": "26h", "interval_seconds": 26 * 3600})


if __name__ == "__main__":
    unittest.main()
