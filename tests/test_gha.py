"""Run: python tests/test_gha.py"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from escrow.check import FAILED, NEVER_SEEN, OK, OVERDUE
from escrow.gha import DISABLED, CronError, check_repo, longest_gap

NOW = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.timezone.utc)
H = dt.timedelta(hours=1)


class TestLongestGap(unittest.TestCase):
    def test_common_schedules(self):
        self.assertEqual(longest_gap(["*/5 * * * *"]), dt.timedelta(minutes=5))
        self.assertEqual(longest_gap(["0 3 * * *"]), dt.timedelta(days=1))
        self.assertEqual(longest_gap(["30 5 * * 1-5"]), dt.timedelta(days=3))  # Fri -> Mon
        self.assertEqual(longest_gap(["0 0 1 * *"]), dt.timedelta(days=31))
        self.assertEqual(longest_gap(["0 0 1 1 *"]), dt.timedelta(days=365))

    def test_several_lines_are_one_schedule(self):
        self.assertEqual(longest_gap(["0 */6 * * *", "30 1 * * *"]), 6 * H)

    def test_day_of_month_or_day_of_week(self):
        # Standard cron: with both restricted, either one fires it.
        self.assertEqual(longest_gap(["0 0 13 * 5"]), dt.timedelta(days=7))

    def test_names_and_sunday_as_seven(self):
        self.assertEqual(longest_gap(["0 0 * * SUN"]), longest_gap(["0 0 * * 7"]))

    def test_nonsense_is_an_error_not_a_pass(self):
        for bad in ("* * *", "61 * * * *", "0 0 31 2 *"):
            with self.assertRaises(CronError):
                longest_gap([bad])


def fake_api(runs, state="active", cron="0 3 * * *", unfiltered=None, created=None,
             repos=None):
    def get(path, raw=False):
        if raw:
            return f"on:\n  schedule:\n    - cron: '{cron}'\n"
        if path.endswith("/workflows?per_page=100"):
            return {"workflows": [{"id": 1, "name": "Nightly", "state": state,
                                   "path": ".github/workflows/nightly.yml",
                                   "created_at": created or "2020-01-01T00:00:00Z"}]}
        if "event=schedule" in path:
            return {"workflow_runs": runs}
        return {"workflow_runs": unfiltered if unfiltered is not None else runs}
    return get


def run(ago, conclusion="success"):
    return {"created_at": (NOW - ago).isoformat().replace("+00:00", "Z"),
            "conclusion": conclusion, "event": "schedule"}


class TestCheckRepo(unittest.TestCase):
    def status(self, **kw):
        return check_repo("o/r", NOW, H, get=fake_api(**kw))[0]["status"]

    def test_statuses(self):
        self.assertEqual(self.status(runs=[run(20 * H)]), OK)
        self.assertEqual(self.status(runs=[run(3 * 24 * H)]), OVERDUE)
        self.assertEqual(self.status(runs=[run(2 * H, "failure")]), FAILED)
        self.assertEqual(self.status(runs=[]), NEVER_SEEN)

    def test_a_workflow_newer_than_its_schedule_is_waiting_not_missing(self):
        # Seen live: a weekly workflow added three days ago read as "never run".
        fresh = (NOW - 3 * 24 * H).isoformat().replace("+00:00", "Z")
        r = check_repo("o/r", NOW, H, get=fake_api(runs=[], cron="0 0 * * 1", created=fresh))[0]
        self.assertEqual(r["status"], OK)
        self.assertIn("first scheduled run is due", r["detail"])
        stale = (NOW - 30 * 24 * H).isoformat().replace("+00:00", "Z")
        r = check_repo("o/r", NOW, H, get=fake_api(runs=[], cron="0 0 * * 1", created=stale))[0]
        self.assertEqual(r["status"], NEVER_SEEN)

    def test_an_org_lists_live_repos_only(self):
        from escrow.gha import owner_repos
        pages = {1: [{"full_name": f"o/r{i}"} for i in range(100)],
                 2: [{"full_name": "o/old", "archived": True}, {"full_name": "o/copy", "fork": True},
                     {"full_name": "o/last"}]}

        def get(path, raw=False):
            if path == "/orgs/o":
                return {}
            return pages[int(path.rsplit("page=", 1)[1])]
        names = owner_repos("o", get)
        self.assertEqual((len(names), names[-1]), (101, "o/last"))

    def test_github_disabling_the_schedule_is_reported(self):
        result = check_repo("o/r", NOW, H, get=fake_api(runs=[], state="disabled_inactivity"))[0]
        self.assertEqual(result["status"], DISABLED)
        self.assertNotIn("intentional", result)
        manual = check_repo("o/r", NOW, H, get=fake_api(runs=[], state="disabled_manually"))[0]
        self.assertTrue(manual["intentional"])

    def test_a_stale_filtered_page_is_rechecked_before_calling_it_silent(self):
        # Seen live: the event=schedule list returned a week-old run for
        # django while its newest scheduled run was hours old.
        self.assertEqual(self.status(runs=[run(7 * 24 * H)],
                                     unfiltered=[run(10 * H), run(7 * 24 * H)]), OK)


if __name__ == "__main__":
    unittest.main()
