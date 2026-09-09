"""Run: python tests/test_cli.py

Exercises the real CLI entry point end to end -- real temp files, real
argv, real stdout capture.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from escrow.cli import main


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.config = self.dir / "escrow.yaml"
        self.state = self.dir / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_ping_then_check_reads_ok(self):
        self.config.write_text("jobs:\n  - name: nightly-backup\n    interval: 26h\n")
        code = main(["ping", "nightly-backup", "--state", str(self.state)])
        self.assertEqual(code, 0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["check", str(self.config), "--state", str(self.state)])
        self.assertEqual(code, 0)
        self.assertIn("[OK]", buf.getvalue())
        self.assertIn("1/1 ok", buf.getvalue())

    def test_never_pinged_job_fails_check(self):
        self.config.write_text("jobs:\n  - name: weekly-report\n    interval: 8d\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["check", str(self.config), "--state", str(self.state)])
        self.assertEqual(code, 1)
        self.assertIn("[??]", buf.getvalue())
        self.assertIn("need attention", buf.getvalue())

    def test_overdue_job_fails_check(self):
        self.config.write_text("jobs:\n  - name: nightly-backup\n    interval: 1h\n")
        self.state.write_text(json.dumps({"nightly-backup": {"last_seen": time.time() - 7200}}))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["check", str(self.config), "--state", str(self.state)])
        self.assertEqual(code, 1)
        self.assertIn("[!!]", buf.getvalue())

    def test_state_path_that_is_a_directory_is_a_clean_error_on_ping(self):
        """Found by testing an actual misconfigured --state: recording a
        ping into a path that's a directory used to raise IsADirectoryError
        straight out of main() as a traceback."""
        state_dir = self.dir / "a_directory"
        state_dir.mkdir()
        with self.assertRaises(SystemExit) as ctx:
            main(["ping", "x", "--state", str(state_dir)])
        self.assertIn("escrow:", str(ctx.exception))

    def test_bad_config_is_a_clean_error_not_a_traceback(self):
        self.config.write_text("not: a valid escrow config\n")
        with self.assertRaises(SystemExit) as ctx:
            main(["check", str(self.config), "--state", str(self.state)])
        self.assertIn("escrow:", str(ctx.exception))

    def test_json_flag_prints_the_full_report(self):
        self.config.write_text("jobs:\n  - name: x\n    interval: 1h\n")
        main(["ping", "x", "--state", str(self.state)])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["check", str(self.config), "--state", str(self.state), "--json"])
        report = json.loads(buf.getvalue())
        self.assertEqual(report[0]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
