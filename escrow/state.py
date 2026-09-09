"""Where each job's last ping is recorded: one small JSON file.

Built for the common case -- a single host (or a shared filesystem both
the job and the checker can see) -- not for concurrent writers racing on a
network filesystem. A cron job and a monitoring check on the same box, or
a NAS-mounted state file, are the target; a distributed fleet of machines
all pinging the same file needs a real datastore, not this. Said plainly
in README rather than silently outgrown.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile


def load_state(path: str) -> dict:
    p = pathlib.Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A corrupted state file, or --state pointed at something that
        # isn't a plain file at all (a directory, a broken symlink), reads
        # as "nothing has ever pinged," not a crash -- the next real ping
        # repairs it, and in the meantime every job just reads as
        # never_seen, which is honest: this file is exactly what would
        # tell us otherwise, and it can't right now.
        return {}


def save_state(path: str, state: dict) -> None:
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the same directory, then atomically rename --
    # a crash mid-write leaves the previous, valid state file intact
    # instead of a half-written, unparseable one.
    fd, tmp_path = tempfile.mkstemp(dir=p.parent, prefix=".escrow-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        os.replace(tmp_path, p)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def record_ping(path: str, job_name: str, when: float) -> None:
    state = load_state(path)
    state[job_name] = {"last_seen": when}
    save_state(path, state)
