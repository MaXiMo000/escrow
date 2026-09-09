"""escrow ping <job-name> [--state PATH]
escrow check <config.yaml> [--state PATH] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from .check import NEVER_SEEN, OK, OVERDUE, check_jobs
from .config import ConfigError, load_config
from .state import load_state, record_ping

DEFAULT_STATE = "escrow-state.json"
_TAG = {OK: "OK", OVERDUE: "!!", NEVER_SEEN: "??"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="escrow")
    sub = parser.add_subparsers(dest="command", required=True)

    ping_p = sub.add_parser("ping", help="record that a job ran, right now")
    ping_p.add_argument("job_name")
    ping_p.add_argument("--state", default=DEFAULT_STATE,
                         help=f"path to the state file (default: {DEFAULT_STATE})")

    check_p = sub.add_parser(
        "check", help="check every declared job against its recorded pings")
    check_p.add_argument("config", help="escrow.yaml -- the declared jobs and intervals")
    check_p.add_argument("--state", default=DEFAULT_STATE,
                          help=f"path to the state file (default: {DEFAULT_STATE})")
    check_p.add_argument("--json", action="store_true", help="print the full report as JSON")

    args = parser.parse_args(argv)

    if args.command == "ping":
        try:
            record_ping(args.state, args.job_name, time.time())
        except OSError as exc:
            # --state pointed at something that can't be written (a
            # directory, a read-only path) -- a wrong argument, not a
            # crash. Silently swallowing this would be worse: it's the one
            # write escrow makes, and its whole job is knowing whether that
            # write actually happened.
            sys.exit(f"escrow: could not record the ping: {exc}")
        print(f"pinged '{args.job_name}'")
        return 0

    try:
        jobs = load_config(args.config)
    except ConfigError as exc:
        sys.exit(f"escrow: {exc}")

    state = load_state(args.state)
    results = check_jobs(jobs, state, time.time())

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

    # Non-blocking by design: escrow reports, it doesn't page anyone --
    # wire the exit code into whatever alerting already exists (a failing
    # CI step, a systemd OnFailure= unit), the same "exit code is the
    # interface" convention receipt/invariant/carabiner already share.
    return 1 if any(r["status"] != OK for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
