"""`escrow gha OWNER/REPO`: scheduled GitHub Actions workflows that went
quiet, with no pings to wire up.

A scheduled workflow stops in ways that raise nothing: GitHub disables the
schedule after 60 days without repository activity, a run gets dropped
under load, a cron line is edited into one that never fires. Each workflow's
own `schedule:` says how often it should run, and the Actions API says when
it last did -- so the silence is checkable from outside, with no change to
the workflow itself.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import yaml

from .check import FAILED, NEVER_SEEN, OK, OVERDUE, _fmt

DISABLED = "disabled"
API = "https://api.github.com"

_NAMES = {
    3: {m: i for i, m in enumerate(
        "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)},
    4: {d: i for i, d in enumerate("sun mon tue wed thu fri sat".split())},
}
_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]


class CronError(ValueError):
    pass


def _field(text: str, index: int) -> set[int]:
    lo, hi = _RANGES[index]
    out: set[int] = set()
    for part in text.lower().split(","):
        body, _, step = part.partition("/")
        for name, number in _NAMES.get(index, {}).items():
            body = body.replace(name, str(number))
        if body == "*":
            start, end = lo, hi
        elif "-" in body:
            start, end = (int(x) for x in body.split("-", 1))
        else:
            start = int(body)
            end = hi if step else start
        if not (lo <= start <= end <= hi):
            raise CronError(f"{part!r} is out of range {lo}-{hi}")
        out.update(range(start, end + 1, int(step) if step else 1))
    if index == 4 and 7 in out:  # 7 is Sunday too
        out = (out - {7}) | {0}
    return out


def fire_times(expr: str, start: dt.datetime, days: int):
    """Every minute `expr` fires in [start, start+days), in UTC -- the only
    zone GitHub schedules run in."""
    fields = expr.split()
    if len(fields) != 5:
        raise CronError(f"{expr!r} needs five fields")
    try:
        minute, hour, dom, month, dow = (_field(f, i) for i, f in enumerate(fields))
    except ValueError as exc:
        raise CronError(f"{expr!r}: {exc}") from None
    dom_any, dow_any = fields[2] == "*", fields[4] == "*"
    for offset in range(days):
        day = start + dt.timedelta(days=offset)
        if day.month not in month:
            continue
        in_dom, in_dow = day.day in dom, (day.isoweekday() % 7) in dow
        # Standard cron: with both restricted, either one matching is enough.
        if not ((in_dom or in_dow) if not (dom_any or dow_any) else (in_dom and in_dow)):
            continue
        for h in sorted(hour):
            for m in sorted(minute):
                yield day.replace(hour=h, minute=m)


def longest_gap(crons: list[str]) -> dt.timedelta:
    """The longest a workflow should ever go between runs. Two years, so a
    yearly schedule and February both get seen; several cron lines are one
    schedule."""
    start = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
    times = sorted({t for c in crons for t in fire_times(c, start, 731)})
    if len(times) < 2:
        raise CronError(f"{crons} fires fewer than twice in two years")
    return max(b - a for a, b in zip(times, times[1:]))


def _get(path: str, raw: bool = False):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "escrow"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(API + path, headers=headers), timeout=30) as r:
        body = r.read().decode("utf-8")
    return body if raw else json.loads(body)


def _crons(text: str) -> list[str]:
    doc = yaml.safe_load(text) or {}
    on = doc.get("on", doc.get(True)) if isinstance(doc, dict) else None
    sched = on.get("schedule") if isinstance(on, dict) else None
    return [str(s["cron"]) for s in sched or [] if isinstance(s, dict) and s.get("cron")]


def check_repo(repo: str, now: dt.datetime, grace: dt.timedelta, get=_get) -> list[dict]:
    results = []
    for wf in get(f"/repos/{repo}/actions/workflows?per_page=100")["workflows"]:
        if not wf["path"].startswith(".github/workflows/"):
            continue  # dynamic workflows (Dependabot, Pages) have no file to read
        try:
            crons = _crons(get(f"/repos/{repo}/contents/{urllib.parse.quote(wf['path'])}", raw=True))
        except (urllib.error.HTTPError, yaml.YAMLError):
            continue  # deleted from the default branch, or not YAML we can read
        if not crons:
            continue
        name = f"{wf['name']} ({wf['path'].rsplit('/', 1)[-1]})"
        base = {"name": name, "cron": crons, "state": wf["state"], "last_run": None}

        if wf["state"] == "disabled_inactivity":
            results.append({**base, "status": DISABLED, "detail": (
                f"'{name}' was disabled by GitHub after 60 days without repository "
                "activity -- its schedule no longer runs, and nothing said so")})
            continue
        if wf["state"] != "active":
            results.append({**base, "status": DISABLED, "intentional": True,
                            "detail": f"'{name}' is {wf['state'].replace('_', ' ')}"})
            continue
        try:
            gap = longest_gap(crons)
        except CronError as exc:
            results.append({**base, "status": NEVER_SEEN, "detail": f"'{name}': {exc}"})
            continue

        runs = get(f"/repos/{repo}/actions/workflows/{wf['id']}/runs?event=schedule&per_page=1")
        run = (runs.get("workflow_runs") or [None])[0]
        allowed = gap + grace
        if run is None:
            created = wf.get("created_at")
            age = now - dt.datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None
            if age is not None and age <= allowed:
                # Added less than one schedule ago: no run was due yet.
                results.append({**base, "status": OK, "detail": (
                    f"'{name}' was added {_fmt(age.total_seconds())} ago; its first scheduled "
                    f"run is due within {_fmt((allowed - age).total_seconds())}")})
                continue
            results.append({**base, "status": NEVER_SEEN, "detail": (
                f"'{name}' is scheduled ({'; '.join(crons)}) but has no scheduled run on record")})
            continue
        started = dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if now - started > allowed:
            # The filtered runs list can lag: one pass over eight large repos
            # saw django, vite and cpython "overdue" by days while their
            # newest scheduled runs were hours old. Ask again, unfiltered,
            # before calling anything silent.
            recent = get(f"/repos/{repo}/actions/workflows/{wf['id']}/runs?per_page=30")
            for other in recent.get("workflow_runs") or []:
                if other.get("event") == "schedule" and other["created_at"] > run["created_at"]:
                    run = other
            started = dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        age = now - started
        base["last_run"] = run["created_at"]
        if age > allowed:
            results.append({**base, "status": OVERDUE, "detail": (
                f"'{name}' last ran on schedule {_fmt(age.total_seconds())} ago; "
                f"'{'; '.join(crons)}' should never leave more than {_fmt(gap.total_seconds())}")})
        elif run.get("conclusion") == "failure":
            results.append({**base, "status": FAILED, "detail": (
                f"'{name}' ran on schedule {_fmt(age.total_seconds())} ago and failed")})
        else:
            results.append({**base, "status": OK, "detail": (
                f"'{name}' ran on schedule {_fmt(age.total_seconds())} ago, "
                f"within its {_fmt(gap.total_seconds())} schedule")})
    return results


def owner_repos(owner: str, get=_get) -> list[str]:
    """Every live repository of an organization or a user: not archived
    (nothing runs there) and not a fork (schedules never run in forks)."""
    import urllib.error
    names, page = [], 1
    try:
        get(f"/orgs/{owner}")
        base = f"/orgs/{owner}/repos?type=all&per_page=100"
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        base = f"/users/{owner}/repos?type=owner&per_page=100"
    while True:
        batch = get(f"{base}&page={page}")
        names += [r["full_name"] for r in batch if not r.get("archived") and not r.get("fork")]
        if len(batch) < 100:
            return names
        page += 1

