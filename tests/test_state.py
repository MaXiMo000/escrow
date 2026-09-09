"""Run: python tests/test_state.py"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from escrow.state import load_state, record_ping, save_state


class TestState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(pathlib.Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_state_file_is_empty_not_a_crash(self):
        self.assertEqual(load_state(self.path), {})

    def test_record_ping_creates_the_file(self):
        record_ping(self.path, "nightly-backup", 1000.0)
        state = load_state(self.path)
        self.assertEqual(state["nightly-backup"]["last_seen"], 1000.0)

    def test_second_ping_updates_the_same_job(self):
        record_ping(self.path, "nightly-backup", 1000.0)
        record_ping(self.path, "nightly-backup", 2000.0)
        state = load_state(self.path)
        self.assertEqual(len(state), 1)
        self.assertEqual(state["nightly-backup"]["last_seen"], 2000.0)

    def test_pinging_different_jobs_keeps_both(self):
        record_ping(self.path, "a", 1000.0)
        record_ping(self.path, "b", 2000.0)
        state = load_state(self.path)
        self.assertEqual(set(state), {"a", "b"})

    def test_corrupted_state_file_reads_as_empty_not_a_crash(self):
        pathlib.Path(self.path).write_text("not valid json {{{", encoding="utf-8")
        self.assertEqual(load_state(self.path), {})

    def test_state_path_that_is_a_directory_reads_as_empty_not_a_crash(self):
        """Found by testing an actual misconfigured --state pointed at a
        directory: load_state used to only catch JSONDecodeError, and
        IsADirectoryError (also an OSError) escaped uncaught."""
        dir_path = str(pathlib.Path(self.tmp.name) / "a_directory")
        pathlib.Path(dir_path).mkdir()
        self.assertEqual(load_state(dir_path), {})

    def test_save_state_creates_parent_directories(self):
        nested = str(pathlib.Path(self.tmp.name) / "a" / "b" / "state.json")
        save_state(nested, {"x": {"last_seen": 1.0}})
        self.assertEqual(load_state(nested), {"x": {"last_seen": 1.0}})

    def test_no_leftover_temp_file_after_a_normal_save(self):
        save_state(self.path, {"x": {"last_seen": 1.0}})
        leftovers = list(pathlib.Path(self.tmp.name).glob(".escrow-*"))
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
