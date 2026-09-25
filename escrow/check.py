"""Compare declared jobs against recorded pings: ok, overdue, never_seen,
or failed. Silence gets its own status, distinct from failure -- the entire
reason this tool exists is that "no alert" and "everything's fine" look
identical to anything that only watches for a failure exit code.
"""
from __future__ import annotations

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
        if age > job["interval_seconds"]:
            results.append({**base, "status": OVERDUE,
                            "detail": f"'{name}' last pinged {_fmt(age)} ago, past its {job['interval']} interval",
                            "overdue_by_seconds": age - job["interval_seconds"]})
        else:
            results.append({**base, "status": OK,
                            "detail": f"'{name}' last pinged {_fmt(age)} ago, within its {job['interval']} interval"})
    return results
