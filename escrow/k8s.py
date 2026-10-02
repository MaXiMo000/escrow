"""`escrow k8s`: Kubernetes CronJobs that stopped succeeding.

A CronJob records when it was last scheduled and when a run last
*succeeded*. Those two drifting apart is the silence this tool is for: the
schedule fires, every Job fails or never starts (a bad image, a missing
secret, a quota), and nothing alerts because nothing ever errored at the
CronJob level. Compared against the job's own schedule -- no pings.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess

from .check import FAILED, NEVER_SEEN, OK, OVERDUE, _fmt
from .gha import DISABLED, CronError, longest_gap

_ALIASES = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
            "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *",
            "@annually": "0 0 1 1 *"}


def _time(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def kubectl_cronjobs(context: str | None = None, namespace: str | None = None) -> dict:
    argv = ["kubectl", "get", "cronjobs", "-o", "json"]
    argv += ["-n", namespace] if namespace else ["-A"]
    if context:
        argv += ["--context", context]
    ran = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    if ran.returncode != 0:
        raise RuntimeError((ran.stderr or "kubectl failed").strip())
    return json.loads(ran.stdout)


def check_cronjobs(listing: dict, now: dt.datetime, grace: dt.timedelta) -> list[dict]:
    results = []
    for item in listing.get("items", []):
        meta, spec, status = item.get("metadata", {}), item.get("spec", {}), item.get("status", {}) or {}
        name = f"{meta.get('namespace', 'default')}/{meta.get('name')}"
        schedule = _ALIASES.get(spec.get("schedule", "").strip(), spec.get("schedule", "").strip())
        base = {"name": name, "cron": [schedule], "last_run": status.get("lastSuccessfulTime")}
        if spec.get("suspend"):
            results.append({**base, "status": DISABLED, "intentional": True,
                            "detail": f"'{name}' is suspended"})
            continue
        try:
            gap = longest_gap([schedule])
        except CronError as exc:
            results.append({**base, "status": NEVER_SEEN, "detail": f"'{name}': {exc}"})
            continue
        allowed = gap + grace
        succeeded = _time(status.get("lastSuccessfulTime"))
        scheduled = _time(status.get("lastScheduleTime"))
        created = _time(meta.get("creationTimestamp"))
        span = _fmt(gap.total_seconds())

        if succeeded and now - succeeded <= allowed:
            results.append({**base, "status": OK, "detail": (
                f"'{name}' last succeeded {_fmt((now - succeeded).total_seconds())} ago, "
                f"within its {span} schedule")})
        elif scheduled and now - scheduled <= allowed:
            # Still firing, never succeeding: the silent kind of failure.
            since = f"last succeeded {_fmt((now - succeeded).total_seconds())} ago" if succeeded \
                else "has never succeeded"
            results.append({**base, "status": FAILED, "detail": (
                f"'{name}' is still being scheduled but {since} -- its Jobs are failing")})
        elif scheduled or succeeded:
            last = max(t for t in (scheduled, succeeded) if t)
            results.append({**base, "status": OVERDUE, "detail": (
                f"'{name}' last ran {_fmt((now - last).total_seconds())} ago; "
                f"'{schedule}' should never leave more than {span}")})
        elif created and now - created <= allowed:
            results.append({**base, "status": OK, "detail": (
                f"'{name}' was created {_fmt((now - created).total_seconds())} ago; "
                "its first run is not due yet")})
        else:
            results.append({**base, "status": NEVER_SEEN, "detail": (
                f"'{name}' ({schedule}) has never been scheduled")})
    return results
