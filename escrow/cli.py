"""escrow ping <job> [--fail [--exit-code N]] [--state PATH | --url URL]
escrow run <job> [--state PATH | --url URL] -- <command...>
escrow check <config.yaml> [--state PATH] [--json]
escrow serve <config.yaml> [--state PATH] [--host H] [--port P] [--token T] [--webhook URL]
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from .check import FAILED, NEVER_SEEN, OK, OVERDUE, check_jobs
from .config import ConfigError, load_config
from .duration import DurationError, parse_duration
from .state import load_state, record_ping

DEFAULT_STATE = "escrow-state.json"
_TAG = {OK: "OK", OVERDUE: "!!", NEVER_SEEN: "??", FAILED: "XX"}


def _record(args, job: str, ok: bool, exit_code: int | None) -> None:
    """Locally into --state, or to a remote `escrow serve` via --url."""
    if args.url:
        path = f"/ping/{urllib.parse.quote(job, safe='')}" + ("" if ok else "/fail")
        if not ok and exit_code is not None:
            path += f"?exit_code={exit_code}"
        req = urllib.request.Request(args.url.rstrip("/") + path, method="POST", data=b"",
                                     headers={"User-Agent": "escrow"})
        token = args.token or os.environ.get("ESCROW_TOKEN")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace").strip()
            sys.exit(f"escrow: the server refused the ping ({exc.code}): {detail}")
        except (urllib.error.URLError, OSError) as exc:
            sys.exit(f"escrow: could not reach {args.url}: {exc}")
        return
    try:
        record_ping(args.state, job, time.time(), ok=ok, exit_code=exit_code)
    except OSError as exc:
        # --state pointed at something that can't be written (a directory, a
        # read-only path) -- a wrong argument, not a crash. Silently
        # swallowing this would be worse: it's the one write escrow makes.
        sys.exit(f"escrow: could not record the ping: {exc}")


def _ping(args) -> int:
    ok = not args.fail
    _record(args, args.job_name, ok, args.exit_code if not ok else None)
    print(f"pinged '{args.job_name}'" + ("" if ok else " (failed)"))
    return 0


def _run(args) -> int:
    """Run the job itself and report its outcome -- so 'it ran but failed'
    is caught too, not only 'it stopped running'."""
    if not args.cmd:
        sys.exit("escrow: no command given -- pass it after `--`")
    try:
        code = subprocess.run(args.cmd).returncode
    except OSError as exc:
        code = 127
        print(f"escrow: could not start {args.cmd[0]}: {exc}", file=sys.stderr)
    _record(args, args.job_name, code == 0, None if code == 0 else code)
    return code


def _check(args) -> int:
    try:
        jobs = load_config(args.config)
    except ConfigError as exc:
        sys.exit(f"escrow: {exc}")
    results = check_jobs(jobs, load_state(args.state), time.time())

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(f"[{_TAG[r['status']]}] {r['detail']}")
        n_bad = sum(1 for r in results if r["status"] != OK)
        summary = f"{len(results) - n_bad}/{len(results)} ok"
        if n_bad:
            summary += f", {n_bad} need attention"
        print(f"\n{summary}")

    # Non-blocking by design: escrow check reports, it doesn't page anyone --
    # wire the exit code into whatever alerting already exists, or run
    # `escrow serve --webhook` to have escrow alert on its own.
    return 1 if any(r["status"] != OK for r in results) else 0


def _gha(args) -> int:
    import datetime as dt
    import urllib.error
    from .gha import check_repo
    try:
        grace = dt.timedelta(seconds=parse_duration(args.grace))
    except DurationError as exc:
        sys.exit(f"escrow: {exc}")
    from concurrent.futures import ThreadPoolExecutor
    from .gha import owner_repos
    repos = list(args.repos)
    for owner in args.org:
        try:
            repos += owner_repos(owner)
        except (urllib.error.URLError, KeyError, ValueError) as exc:
            sys.exit(f"escrow: could not list {owner}'s repositories ({exc})")
    if not repos:
        sys.exit("escrow: name at least one OWNER/REPO or --org")
    now = dt.datetime.now(dt.timezone.utc)

    def one(repo):
        try:
            return [{**r, "repo": repo} for r in check_repo(repo, now, grace)], None
        except (urllib.error.URLError, KeyError, ValueError) as exc:
            return [], f"{repo}: could not read its workflows ({exc})"

    results, failed = [], False
    with ThreadPoolExecutor(max_workers=8) as pool:
        for found, error in pool.map(one, repos):
            results += found
            if error:
                # Could not look is its own answer, never an empty "all ok".
                print(f"[??] {error}; set GITHUB_TOKEN for private repos or rate limits",
                      file=sys.stderr)
                failed = True
    return _report_schedules(results, args, "scheduled workflow(s)",
                             prefix=len(repos) > 1) or (1 if failed else 0)


def _report_schedules(results, args, noun: str, prefix: bool = False) -> int:
    from .gha import DISABLED
    bad = [r for r in results if r["status"] != OK and not r.get("intentional")]
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        tag = {**_TAG, DISABLED: "--"}
        for r in results:
            where = f"{r['repo']}: " if prefix and r.get("repo") else ""
            print(f"[{tag[r['status']]}] {where}{r['detail']}")
        print(f"\n{len(results) - len(bad)}/{len(results)} {noun} ok"
              + (f", {len(bad)} need attention" if bad else ""))
    return 1 if bad else 0


def _k8s(args) -> int:
    import datetime as dt
    from .k8s import check_cronjobs, kubectl_cronjobs
    try:
        grace = dt.timedelta(seconds=parse_duration(args.grace))
    except DurationError as exc:
        sys.exit(f"escrow: {exc}")
    try:
        if args.from_file:
            text = sys.stdin.read() if args.from_file == "-" else open(args.from_file, encoding="utf-8").read()
            listing = json.loads(text)
        else:
            listing = kubectl_cronjobs(args.context, args.namespace)
    except (OSError, ValueError, RuntimeError) as exc:
        # Could not look is its own answer, never an empty "all ok".
        sys.exit(f"escrow: could not read the CronJobs: {exc}")
    return _report_schedules(check_cronjobs(listing, dt.datetime.now(dt.timezone.utc), grace),
                             args, "CronJob(s)")


def _serve(args) -> int:
    from .server import serve

    try:
        jobs = load_config(args.config)
        every = parse_duration(args.check_every)
    except (ConfigError, DurationError) as exc:
        sys.exit(f"escrow: {exc}")
    token = args.token or os.environ.get("ESCROW_TOKEN")
    try:
        loopback = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        loopback = args.host == "localhost"
    if not loopback and not token:
        # Reachable from other machines with no token means anyone who can
        # reach the port can mark a dead job healthy.
        sys.exit("escrow: refusing to listen beyond localhost without a token "
                 "(pass --token or set ESCROW_TOKEN)")
    return serve(jobs, args.state, host=args.host, port=args.port, token=token,
                 webhook=args.webhook, check_every=every)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    cmd: list[str] = []
    if "--" in argv:
        split = argv.index("--")
        argv, cmd = argv[:split], argv[split + 1:]

    parser = argparse.ArgumentParser(prog="escrow")
    sub = parser.add_subparsers(dest="command", required=True)

    def remote_opts(p):
        p.add_argument("--state", default=DEFAULT_STATE,
                       help=f"local state file (default: {DEFAULT_STATE})")
        p.add_argument("--url", help="ping a remote `escrow serve` instead, e.g. http://host:8765")
        p.add_argument("--token", help="the server's token (default: $ESCROW_TOKEN)")

    ping_p = sub.add_parser("ping", help="record that a job ran, right now")
    ping_p.add_argument("job_name")
    ping_p.add_argument("--fail", action="store_true", help="record that it ran and failed")
    ping_p.add_argument("--exit-code", type=int, help="with --fail: the job's exit code")
    remote_opts(ping_p)
    ping_p.set_defaults(func=_ping)

    run_p = sub.add_parser("run", help="run a job's command and ping with its outcome")
    run_p.add_argument("job_name")
    remote_opts(run_p)
    run_p.set_defaults(func=_run)

    check_p = sub.add_parser("check", help="check every declared job against its recorded pings")
    check_p.add_argument("config", help="escrow.yaml -- the declared jobs and intervals")
    check_p.add_argument("--state", default=DEFAULT_STATE,
                         help=f"path to the state file (default: {DEFAULT_STATE})")
    check_p.add_argument("--json", action="store_true", help="print the full report as JSON")
    check_p.set_defaults(func=_check)

    gha_p = sub.add_parser(
        "gha", help="check a repo's scheduled GitHub Actions workflows are still running")
    gha_p.add_argument("repos", nargs="*", metavar="OWNER/REPO")
    gha_p.add_argument("--org", action="append", default=[], metavar="OWNER",
                       help="every live repository of this organization or user (repeatable)")
    gha_p.add_argument("--grace", default="1h",
                       help="slack past the schedule's longest gap; GitHub starts scheduled "
                            "runs late under load (default: 1h)")
    gha_p.add_argument("--json", action="store_true", help="print the full report as JSON")
    gha_p.set_defaults(func=_gha)

    k8s_p = sub.add_parser("k8s", help="check Kubernetes CronJobs are still succeeding on schedule")
    k8s_p.add_argument("--context", help="kubectl context (default: the current one)")
    k8s_p.add_argument("--namespace", "-n", help="one namespace (default: all)")
    k8s_p.add_argument("--from", dest="from_file", metavar="FILE",
                       help="read `kubectl get cronjobs -A -o json` output from FILE ('-' for stdin)")
    k8s_p.add_argument("--grace", default="1h", help="slack past each schedule's longest gap")
    k8s_p.add_argument("--json", action="store_true")
    k8s_p.set_defaults(func=_k8s)

    serve_p = sub.add_parser("serve", help="accept pings over HTTP and alert a webhook on changes")
    serve_p.add_argument("config", help="escrow.yaml -- the declared jobs and intervals")
    serve_p.add_argument("--state", default=DEFAULT_STATE,
                         help=f"path to the state file (default: {DEFAULT_STATE})")
    serve_p.add_argument("--host", default="127.0.0.1", help="interface to bind (default: 127.0.0.1)")
    serve_p.add_argument("--port", type=int, default=8765, help="port (default: 8765)")
    serve_p.add_argument("--token", help="require this token on every request (default: $ESCROW_TOKEN)")
    serve_p.add_argument("--webhook", help="POST a JSON alert here whenever a job's status changes")
    serve_p.add_argument("--check-every", default="1m", help="how often to evaluate jobs (default: 1m)")
    serve_p.set_defaults(func=_serve)

    args = parser.parse_args(argv)
    args.cmd = cmd
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
