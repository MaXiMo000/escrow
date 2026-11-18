"""Five-field cron expressions: when a schedule fires, and the most recent
time it should have fired. Used by `escrow gha` for workflow schedules and
by `check` for jobs declared with `schedule:` instead of `interval:`."""
from __future__ import annotations

import datetime as dt
import itertools

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
    minute, hour, dom, month, dow, dom_any, dow_any = _parsed(expr)
    for offset in range(days):
        day = start + dt.timedelta(days=offset)
        if not _day_matches(day, month, dom, dow, dom_any, dow_any):
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
    return max(b - a for a, b in itertools.pairwise(times))


def _parsed(expr: str):
    fields = expr.split()
    if len(fields) != 5:
        raise CronError(f"{expr!r} needs five fields")
    try:
        minute, hour, dom, month, dow = (_field(f, i) for i, f in enumerate(fields))
    except ValueError as exc:
        raise CronError(f"{expr!r}: {exc}") from None
    return minute, hour, dom, month, dow, fields[2] == "*", fields[4] == "*"


def _day_matches(day: dt.date, month, dom, dow, dom_any: bool, dow_any: bool) -> bool:
    if day.month not in month:
        return False
    in_dom, in_dow = day.day in dom, (day.isoweekday() % 7) in dow
    # Standard cron: with both restricted, either one matching is enough.
    return (in_dom or in_dow) if not (dom_any or dow_any) else (in_dom and in_dow)


def previous_fire(expr: str, before: dt.datetime, tz: dt.tzinfo = dt.timezone.utc) -> dt.datetime | None:
    """The latest time at or before `before` that `expr` fires, read as wall
    time in `tz` (the zone the job's own cron runs in). None if it hasn't
    fired in the two years before."""
    minute, hour, dom, month, dow, dom_any, dow_any = _parsed(expr)
    local = before.astimezone(tz)
    for back in range(732):
        day = local.date() - dt.timedelta(days=back)
        if not _day_matches(day, month, dom, dow, dom_any, dow_any):
            continue
        for h in sorted(hour, reverse=True):
            for m in sorted(minute, reverse=True):
                fired = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
                if fired <= before:
                    return fired
    return None
