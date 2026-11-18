"""Load and validate escrow.yaml: the jobs expected to ping in, and how
often each one is allowed to go quiet before that's a problem.

Jobs must be declared up front, in a file `check` reads -- a job that is
only ever known because it once pinged would be exactly as invisible as
before the first time it silently stopped running, or if it never started
in the first place. Declaring first and checking reality against the
declaration is the same discipline `invariant` already applies to
database state, applied here to "did this job show up at all."
"""
from __future__ import annotations

import datetime as dt
import zoneinfo

import yaml

from .cron import CronError, previous_fire
from .duration import DurationError, parse_duration

# How long after a scheduled time a job may take to ping before it counts
# as missed, when `grace` isn't given.
DEFAULT_GRACE = "1h"


class ConfigError(ValueError):
    pass


def load_config(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML ({exc})") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: {exc}") from exc

    if not isinstance(raw, dict) or "jobs" not in raw:
        raise ConfigError(f"{path}: must be a mapping with a top-level 'jobs' list")
    jobs = raw["jobs"]
    if not isinstance(jobs, list) or not jobs:
        raise ConfigError(f"{path}: 'jobs' must be a non-empty list")

    parsed = []
    seen = set()
    for i, job in enumerate(jobs):
        if not isinstance(job, dict):
            raise ConfigError(f"{path}: jobs[{i}] must be a mapping")
        name = job.get("name")
        if not name or not isinstance(name, str):
            raise ConfigError(f"{path}: jobs[{i}] is missing a string 'name'")
        if name in seen:
            raise ConfigError(f"{path}: duplicate job name '{name}'")
        seen.add(name)

        interval, schedule = job.get("interval"), job.get("schedule")
        if bool(interval) == bool(schedule):
            raise ConfigError(f"{path}: job '{name}' needs exactly one of 'interval' or 'schedule'")
        try:
            if interval:
                parsed.append({"name": name, "interval": str(interval),
                               "interval_seconds": parse_duration(str(interval))})
            else:
                parsed.append(_scheduled(name, str(schedule), job))
        except (DurationError, CronError) as exc:
            raise ConfigError(f"{path}: job '{name}': {exc}") from exc
        except zoneinfo.ZoneInfoNotFoundError:
            raise ConfigError(f"{path}: job '{name}': unknown timezone {job.get('timezone')!r} "
                              "(an IANA name like Europe/Berlin; on Windows, `pip install tzdata`)") from None
    return parsed


def _scheduled(name: str, schedule: str, job: dict) -> dict:
    grace = str(job.get("grace") or DEFAULT_GRACE)
    zone = str(job.get("timezone") or "UTC")
    tz = dt.timezone.utc if zone == "UTC" else zoneinfo.ZoneInfo(zone)
    if previous_fire(schedule, dt.datetime.now(dt.timezone.utc), tz) is None:
        raise CronError(f"{schedule!r} never fires")
    return {"name": name, "schedule": schedule, "grace": grace,
            "grace_seconds": parse_duration(grace), "timezone": zone, "tz": tz}
