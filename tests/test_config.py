"""Run: python tests/test_config.py"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from escrow.config import ConfigError, load_config


class TestLoadConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "escrow.yaml"

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, text: str) -> str:
        self.path.write_text(text, encoding="utf-8")
        return str(self.path)

    def test_valid_config(self):
        p = self._write("jobs:\n  - name: nightly-backup\n    interval: 26h\n")
        jobs = load_config(p)
        self.assertEqual(jobs, [{
            "name": "nightly-backup", "interval": "26h", "interval_seconds": 26 * 3600,
        }])

    def test_multiple_jobs(self):
        p = self._write(
            "jobs:\n"
            "  - name: nightly-backup\n    interval: 26h\n"
            "  - name: weekly-report\n    interval: 8d\n"
        )
        jobs = load_config(p)
        self.assertEqual([j["name"] for j in jobs], ["nightly-backup", "weekly-report"])

    def test_missing_file_is_a_config_error_not_a_crash(self):
        with self.assertRaises(ConfigError) as ctx:
            load_config(str(self.path))  # never written
        self.assertIn(str(self.path), str(ctx.exception))

    def test_not_yaml_is_a_config_error(self):
        p = self._write("not: valid: yaml: at: all:::")
        with self.assertRaises(ConfigError):
            load_config(p)

    def test_missing_jobs_key_is_rejected(self):
        p = self._write("something_else: true\n")
        with self.assertRaises(ConfigError) as ctx:
            load_config(p)
        self.assertIn("jobs", str(ctx.exception))

    def test_empty_jobs_list_is_rejected(self):
        p = self._write("jobs: []\n")
        with self.assertRaises(ConfigError):
            load_config(p)

    def test_job_missing_name_is_rejected(self):
        p = self._write("jobs:\n  - interval: 1h\n")
        with self.assertRaises(ConfigError) as ctx:
            load_config(p)
        self.assertIn("name", str(ctx.exception))

    def test_job_missing_interval_is_rejected(self):
        p = self._write("jobs:\n  - name: x\n")
        with self.assertRaises(ConfigError) as ctx:
            load_config(p)
        self.assertIn("interval", str(ctx.exception))

    def test_duplicate_job_names_are_rejected(self):
        p = self._write("jobs:\n  - name: x\n    interval: 1h\n  - name: x\n    interval: 2h\n")
        with self.assertRaises(ConfigError) as ctx:
            load_config(p)
        self.assertIn("duplicate", str(ctx.exception))

    def test_invalid_interval_reports_which_job(self):
        p = self._write("jobs:\n  - name: x\n    interval: not-a-duration\n")
        with self.assertRaises(ConfigError) as ctx:
            load_config(p)
        self.assertIn("'x'", str(ctx.exception))

    def test_top_level_list_instead_of_mapping_is_rejected(self):
        p = self._write("- just\n- a\n- list\n")
        with self.assertRaises(ConfigError):
            load_config(p)


if __name__ == "__main__":
    unittest.main()
