"""Compare declared jobs against recorded pings: ok, overdue, or
never_seen. Silence gets its own status, distinct from both -- the entire
reason this tool exists is that "no alert" and "everything's fine" look
identical to anything that only watches for a failure exit code.
"""
from __future__ import annotations

OK, OVERDUE, NEVER_SEEN = "ok", "overdue", "never_seen"


def _fmt(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def check_jobs(jobs: list[dict], state: dict, now: float) -> list[dict]:
    results = []
    for job in jobs:
        name = job["name"]
        record = state.get(name)

        if record is None:
            results.append({
                "name": name, "status": NEVER_SEEN,
                "detail": (f"'{name}' has never pinged in -- it may have never run, "
                           "or is pinging under a different name"),
                "last_seen": None, "overdue_by_seconds": None,
            })
            continue

        last_seen = record["last_seen"]
        age = now - last_seen
        if age > job["interval_seconds"]:
            results.append({
                "name": name, "status": OVERDUE,
                "detail": (f"'{name}' last pinged {_fmt(age)} ago, past its "
                           f"{job['interval']} interval"),
                "last_seen": last_seen,
                "overdue_by_seconds": age - job["interval_seconds"],
            })
        else:
            results.append({
                "name": name, "status": OK,
                "detail": f"'{name}' last pinged {_fmt(age)} ago, within its {job['interval']} interval",
                "last_seen": last_seen, "overdue_by_seconds": None,
            })
    return results
