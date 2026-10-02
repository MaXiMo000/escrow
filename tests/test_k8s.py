"""Run: python tests/test_k8s.py"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from escrow.check import FAILED, NEVER_SEEN, OK, OVERDUE
from escrow.gha import DISABLED
from escrow.k8s import check_cronjobs

NOW = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.timezone.utc)
H = dt.timedelta(hours=1)


def iso(delta):
    return (NOW - delta).isoformat().replace("+00:00", "Z")


def job(name, schedule, *, succeeded=None, scheduled=None, created=10 * 24 * H, suspend=False):
    status = {}
    if succeeded is not None:
        status["lastSuccessfulTime"] = iso(succeeded)
    if scheduled is not None:
        status["lastScheduleTime"] = iso(scheduled)
    return {"metadata": {"name": name, "namespace": "prod", "creationTimestamp": iso(created)},
            "spec": {"schedule": schedule, "suspend": suspend}, "status": status}


def statuses(*items):
    return [r["status"] for r in check_cronjobs({"items": list(items)}, NOW, H)]


class TestCronJobs(unittest.TestCase):
    def test_succeeding_on_schedule_is_ok(self):
        self.assertEqual(statuses(job("a", "0 3 * * *", succeeded=9 * H, scheduled=9 * H)), [OK])

    def test_still_scheduled_but_never_succeeding_is_failed(self):
        # The silent kind: the schedule fires, every Job fails.
        self.assertEqual(statuses(job("a", "0 3 * * *", succeeded=4 * 24 * H, scheduled=9 * H),
                                  job("b", "*/5 * * * *", scheduled=2 * H * 0.01)),
                         [FAILED, FAILED])

    def test_not_even_scheduled_any_more_is_overdue(self):
        self.assertEqual(statuses(job("a", "@daily", succeeded=3 * 24 * H, scheduled=3 * 24 * H)), [OVERDUE])

    def test_suspended_is_reported_as_deliberate(self):
        r = check_cronjobs({"items": [job("a", "@hourly", suspend=True)]}, NOW, H)[0]
        self.assertEqual((r["status"], r["intentional"]), (DISABLED, True))

    def test_new_and_never_run(self):
        self.assertEqual(statuses(job("new", "@yearly", created=2 * H),
                                  job("old", "@daily", created=30 * 24 * H)), [OK, NEVER_SEEN])


if __name__ == "__main__":
    unittest.main()
