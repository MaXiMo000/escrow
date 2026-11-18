"""Compare declared jobs against recorded pings: ok, overdue, never_seen,
or failed. Silence gets its own status, distinct from failure -- the entire
reason this tool exists is that "no alert" and "everything's fine" look
identical to anything that only watches for a failure exit code.
"""
from __future__ import annotations

import datetime as dt

from .cron import previous_fire

OK, OVERDUE, NEVER_SEEN, FAILED = "ok", "overdue", "never_seen", "failed"


def _fmt(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _on_schedule(job: dict, last_seen: float, age: float, now: float) -> dict:
    """Overdue when the latest scheduled run that is past its grace period
    has no ping at or after it. A job that only runs on weekdays is then
    quiet on a weekend without being late."""
    cutoff = dt.datetime.fromtimestamp(now - job["grace_seconds"], dt.timezone.utc)
    due = previous_fire(job["schedule"], cutoff, job["tz"])
    if due is not None and last_seen < due.timestamp():
        return {"status": OVERDUE,
                "detail": (f"'{job['name']}' missed its run due {due:%Y-%m-%d %H:%M} {job['timezone']} "
                           f"({job['schedule']}, {job['grace']} grace); last pinged {_fmt(age)} ago"),
                "overdue_by_seconds": now - due.timestamp() - job["grace_seconds"]}
    return {"status": OK,
            "detail": f"'{job['name']}' last pinged {_fmt(age)} ago, on schedule ({job['schedule']})"}


def check_jobs(jobs: list[dict], state: dict, now: float) -> list[dict]:
    results = []
    for job in jobs:
        name = job["name"]
        record = state.get(name) or {}
        last_seen = record.get("last_seen")
        base = {"name": name, "last_seen": last_seen, "overdue_by_seconds": None}

        if record.get("last_status") == "fail":
            # A reported failure outranks everything else: the job did run,
            # and it said so. Cleared by the next successful ping.
            code = record.get("exit_code")
            results.append({**base, "status": FAILED,
                            "detail": (f"'{name}' last run failed"
                                       + (f" (exit {code})" if code is not None else "")
                                       + f", {_fmt(now - record['failed_at'])} ago")})
            continue

        if last_seen is None:
            results.append({**base, "status": NEVER_SEEN,
                            "detail": (f"'{name}' has never pinged in -- it may have never run, "
                                       "or is pinging under a different name")})
            continue

        age = now - last_seen
        if "schedule" in job:
            results.append({**base, **_on_schedule(job, last_seen, age, now)})
            continue
        if age > job["interval_seconds"]:
            results.append({**base, "status": OVERDUE,
                            "detail": f"'{name}' last pinged {_fmt(age)} ago, past its {job['interval']} interval",
                            "overdue_by_seconds": age - job["interval_seconds"]})
        else:
            results.append({**base, "status": OK,
                            "detail": f"'{name}' last pinged {_fmt(age)} ago, within its {job['interval']} interval"})
    return results
