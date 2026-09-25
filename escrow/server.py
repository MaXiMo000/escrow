"""escrow serve: a self-hosted dead-man's-switch in one process, stdlib only.

Jobs ping it over HTTP from wherever they run -- another machine, a
container, a scheduled GitHub Actions workflow on a runner that won't
exist tomorrow -- and it watches the clock itself, alerting a webhook the
moment a job's status changes. The same state file and the same checks as
`escrow ping` / `escrow check`, just reachable over a network.

    GET|POST /ping/<job>                 the job ran and succeeded
    GET|POST /ping/<job>/fail?exit_code=N the job ran and failed
    GET      /status                     every job's current status, as JSON
    GET      /health                     "ok" (no token needed)

Only declared jobs are accepted (anything else is 404), so a stray or
hostile ping can't grow the state file. With a token set, every route but
/health needs `Authorization: Bearer <token>` or `?token=<token>`.
"""
from __future__ import annotations

import hmac
import http.server
import json
import sys
import threading
import time
import urllib.parse
import urllib.request

from .check import OK, check_jobs
from .state import load_state, record_ping


def send_alert(webhook: str, result: dict, previous: str | None) -> None:
    """POSTs one status change. `text` is what Slack, Mattermost and most
    chat webhooks read; `content` is Discord's name for the same thing."""
    arrow = f"{previous} -> " if previous else ""
    text = f"escrow: {result['name']} is {arrow}{result['status'].upper()} -- {result['detail']}"
    body = json.dumps({"text": text, "content": text, "job": result["name"],
                       "status": result["status"], "previous": previous,
                       "detail": result["detail"]}).encode()
    req = urllib.request.Request(webhook, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "escrow"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


class Watcher:
    """Owns the state file for the server's lifetime: one lock around every
    read-modify-write, so concurrent pings can't lose each other's update."""

    def __init__(self, jobs: list[dict], state_path: str, webhook: str | None = None,
                 clock=time.time, log=lambda msg: print(msg, file=sys.stderr, flush=True)):
        self.jobs = jobs
        self.names = {j["name"] for j in jobs}
        self.state_path = state_path
        self.webhook = webhook
        self.clock = clock
        self.log = log
        self.lock = threading.Lock()
        self.last_status: dict[str, str] = {}

    def ping(self, name: str, ok: bool = True, exit_code: int | None = None) -> None:
        with self.lock:
            record_ping(self.state_path, name, self.clock(), ok=ok, exit_code=exit_code)

    def results(self) -> list[dict]:
        with self.lock:
            return check_jobs(self.jobs, load_state(self.state_path), self.clock())

    def tick(self) -> list[dict]:
        """Evaluate every job; alert on each one whose status changed since
        the last tick. On the first tick anything not ok alerts, so a job
        that was already broken when the server (re)started isn't missed."""
        changed = []
        for r in self.results():
            prev = self.last_status.get(r["name"])
            if r["status"] != prev and not (prev is None and r["status"] == OK):
                changed.append((r, prev))
            self.last_status[r["name"]] = r["status"]
        for r, prev in changed:
            self.log(f"escrow: {r['name']}: {prev or 'start'} -> {r['status']} ({r['detail']})")
            if self.webhook:
                try:
                    send_alert(self.webhook, r, prev)
                except Exception as exc:  # noqa: BLE001 -- a dead webhook must not stop the watcher
                    self.log(f"escrow: webhook failed for {r['name']}: {exc}")
        return [r for r, _ in changed]

    def run_forever(self, every: float, stop: threading.Event) -> None:
        while not stop.is_set():
            self.tick()
            stop.wait(every)


def make_handler(watcher: Watcher, token: str | None):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "escrow"

        def _reply(self, code: int, body) -> None:
            data = (json.dumps(body, indent=2) if not isinstance(body, str) else body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json" if not isinstance(body, str) else "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self, query: dict) -> bool:
            if not token:
                return True
            header = self.headers.get("Authorization", "")
            given = header[7:] if header.startswith("Bearer ") else (query.get("token") or [""])[0]
            return hmac.compare_digest(given.encode(), token.encode())

        def _route(self) -> None:
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            parts = [urllib.parse.unquote(p) for p in url.path.strip("/").split("/") if p]
            if parts == ["health"]:
                return self._reply(200, "ok")
            if not self._authorized(query):
                return self._reply(401, {"error": "missing or wrong token"})
            if parts == ["status"]:
                return self._reply(200, watcher.results())
            if len(parts) in (2, 3) and parts[0] == "ping" and (len(parts) == 2 or parts[2] == "fail"):
                name = parts[1]
                if name not in watcher.names:
                    return self._reply(404, {"error": f"'{name}' is not a declared job"})
                ok = len(parts) == 2
                exit_code = None
                if not ok and query.get("exit_code"):
                    try:
                        exit_code = int(query["exit_code"][0])
                    except ValueError:
                        return self._reply(400, {"error": "exit_code must be an integer"})
                watcher.ping(name, ok=ok, exit_code=exit_code)
                return self._reply(200, {"job": name, "recorded": "ok" if ok else "fail"})
            return self._reply(404, {"error": "not found"})

        do_GET = do_POST = _route

        def log_message(self, fmt, *args):  # quiet: the watcher logs what matters
            pass

    return Handler


def serve(jobs: list[dict], state_path: str, *, host: str, port: int, token: str | None,
          webhook: str | None, check_every: float) -> int:
    watcher = Watcher(jobs, state_path, webhook)
    httpd = http.server.ThreadingHTTPServer((host, port), make_handler(watcher, token))
    stop = threading.Event()
    threading.Thread(target=watcher.run_forever, args=(check_every, stop), daemon=True).start()
    print(f"escrow listening on http://{host}:{httpd.server_port}  "
          f"({len(jobs)} job(s), checking every {check_every:g}s"
          f"{', alerting ' + webhook if webhook else ', no webhook'})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
    return 0
