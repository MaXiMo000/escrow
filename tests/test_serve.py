"""Run: python tests/test_serve.py

escrow serve over real HTTP (a real ThreadingHTTPServer on a free port),
alerts delivered to a real local webhook receiver, and `escrow ping --url`
/ `escrow run` driving it as a job would.
"""
from __future__ import annotations

import contextlib
import http.server
import io
import json
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from escrow.check import FAILED, NEVER_SEEN, OK, OVERDUE
from escrow.cli import main
from escrow.config import load_config
from escrow.server import Watcher, make_handler

JOBS_YAML = "jobs:\n  - name: backup\n    interval: 1h\n  - name: report\n    interval: 1d\n"


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@contextlib.contextmanager
def webhook_receiver():
    received = []

    class Hook(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}/hook", received
    finally:
        srv.shutdown()
        srv.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        (self.dir / "escrow.yaml").write_text(JOBS_YAML, encoding="utf-8")
        self.jobs = load_config(str(self.dir / "escrow.yaml"))
        self.state = str(self.dir / "state.json")

    def tearDown(self):
        self.tmp.cleanup()


class TestWatcher(Base):
    def test_alerts_fire_on_changes_only_and_reach_a_real_webhook(self):
        clock = Clock()
        with webhook_receiver() as (url, received):
            w = Watcher(self.jobs, self.state, url, clock=clock, log=lambda m: None)
            w.ping("backup")
            # First tick: 'report' never pinged -> alert; 'backup' ok -> silent.
            self.assertEqual([r["name"] for r in w.tick()], ["report"])
            self.assertEqual([r["name"] for r in w.tick()], [])  # nothing changed
            clock.now += 2 * 3600                                # backup goes quiet
            self.assertEqual([(r["name"], r["status"]) for r in w.tick()], [("backup", OVERDUE)])
            w.ping("backup", ok=False, exit_code=2)
            self.assertEqual([(r["name"], r["status"]) for r in w.tick()], [("backup", FAILED)])
            w.ping("backup")                                      # recovery is a change too
            self.assertEqual([(r["name"], r["status"]) for r in w.tick()], [("backup", OK)])
        self.assertEqual([(a["job"], a["status"]) for a in received],
                         [("report", NEVER_SEEN), ("backup", OVERDUE), ("backup", FAILED), ("backup", OK)])
        self.assertIn("exit 2", received[2]["text"])
        self.assertEqual(received[1]["text"], received[1]["content"])  # Slack and Discord both read it

    def test_a_dead_webhook_is_logged_not_fatal(self):
        logs = []
        w = Watcher(self.jobs, self.state, "http://127.0.0.1:9/nothing-listens-here", log=logs.append)
        w.tick()
        self.assertTrue(any("webhook failed" in m for m in logs))


class TestHttp(Base):
    def _server(self, token=None):
        self.watcher = Watcher(self.jobs, self.state, log=lambda m: None)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.watcher, token))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_port}"

    def _get(self, url, headers=None):
        req = urllib.request.Request(url, headers=headers or {}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_ping_fail_status_and_unknown_jobs(self):
        base = self._server()
        self.assertEqual(self._get(f"{base}/ping/backup")[0], 200)
        self.assertEqual(self._get(f"{base}/ping/report/fail?exit_code=7")[0], 200)
        self.assertEqual(self._get(f"{base}/ping/not-declared")[0], 404)
        self.assertEqual(self._get(f"{base}/ping/report/fail?exit_code=x")[0], 400)
        status = {r["name"]: r for r in json.loads(self._get(f"{base}/status")[1])}
        self.assertEqual(status["backup"]["status"], OK)
        self.assertEqual(status["report"]["status"], FAILED)
        self.assertIn("exit 7", status["report"]["detail"])
        self.assertNotIn("not-declared", json.loads(pathlib.Path(self.state).read_text()))

    def test_token_is_required_everywhere_but_health(self):
        base = self._server(token="s3cret")
        self.assertEqual(self._get(f"{base}/health"), (200, "ok"))
        self.assertEqual(self._get(f"{base}/ping/backup")[0], 401)
        self.assertEqual(self._get(f"{base}/ping/backup?token=wrong")[0], 401)
        self.assertEqual(self._get(f"{base}/ping/backup?token=s3cret")[0], 200)
        self.assertEqual(self._get(f"{base}/status", {"Authorization": "Bearer s3cret"})[0], 200)

    def test_cli_ping_and_run_against_the_server(self):
        base = self._server(token="t")
        quiet = contextlib.redirect_stdout(io.StringIO())
        with quiet:
            self.assertEqual(main(["ping", "backup", "--url", base, "--token", "t"]), 0)
            code = main(["run", "report", "--url", base, "--token", "t", "--",
                         sys.executable, "-c", "raise SystemExit(3)"])
        self.assertEqual(code, 3)  # escrow run passes the job's own exit code through
        status = {r["name"]: r for r in self.watcher.results()}
        self.assertEqual(status["backup"]["status"], OK)
        self.assertEqual(status["report"]["status"], FAILED)
        with self.assertRaises(SystemExit) as ctx:
            main(["ping", "backup", "--url", base, "--token", "wrong"])
        self.assertIn("401", str(ctx.exception))


class TestLocal(Base):
    def test_run_records_success_and_failure_locally(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["run", "backup", "--state", self.state, "--",
                                   sys.executable, "-c", "pass"]), 0)
            self.assertEqual(main(["run", "report", "--state", self.state, "--",
                                   sys.executable, "-c", "raise SystemExit(4)"]), 4)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["check", str(self.dir / "escrow.yaml"), "--state", self.state])
        self.assertEqual(code, 1)
        self.assertIn("[OK] 'backup'", out.getvalue())
        self.assertIn("[XX] 'report' last run failed (exit 4)", out.getvalue())

    def test_a_later_success_clears_a_failure(self):
        with contextlib.redirect_stdout(io.StringIO()):
            main(["ping", "backup", "--fail", "--exit-code", "1", "--state", self.state])
            main(["ping", "backup", "--state", self.state])
            code = main(["check", str(self.dir / "escrow.yaml"), "--state", self.state, "--json"])
        self.assertEqual(code, 1)  # 'report' still never_seen
        state = json.loads(pathlib.Path(self.state).read_text())
        self.assertEqual(state["backup"]["last_status"], "ok")

    def test_serve_refuses_a_public_bind_without_a_token(self):
        with self.assertRaises(SystemExit) as ctx:
            main(["serve", str(self.dir / "escrow.yaml"), "--host", "0.0.0.0"])
        self.assertIn("without a token", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
