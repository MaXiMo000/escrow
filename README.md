# escrow

[![ci](https://github.com/MaXiMo000/escrow/actions/workflows/ci.yml/badge.svg)](https://github.com/MaXiMo000/escrow/actions/workflows/ci.yml)

**A dead-man's-switch for cron jobs, GitHub Actions schedules, and systemd
timers: flags silence, not just failure.**

A scheduled job that throws an exception gets logged, maybe alerted on.
One that silently *stops running* — a cron entry removed by a bad deploy,
a systemd timer disabled and forgotten, a token that expired weeks ago —
produces nothing: no error, no log line, no exit code to check. "No
alert" and "everything's fine" look identical to anything that only
watches for failure. `escrow` watches for the third thing: quiet.

```
$ escrow check escrow.yaml
[!!] 'nightly-backup' last pinged 1d ago, past its 26h interval
[??] 'weekly-report' has never pinged in -- it may have never run, or is pinging under a different name

0/2 ok, 2 need attention
```

(Real output. `nightly-backup` pinged in on time once, then its clock was
wound forward 30 hours past its 26h interval; `weekly-report` never pinged
at all.)

## Install

```
pip install escrow-evidence   # the command it installs is `escrow`
```

(`escrow` was already taken on PyPI -- same story as every sibling in this
portfolio.)

## Use

Declare what you're expecting, in `escrow.yaml`:

```yaml
jobs:
  - name: nightly-backup
    interval: 26h    # a daily job, with a few hours' slack
  - name: weekly-report
    interval: 8d
```

At the end of the actual job's script, record that it ran:

```bash
escrow ping nightly-backup
```

Separately -- on its own schedule, e.g. every 15 minutes -- check every
declared job against what's actually been recorded:

```bash
escrow check escrow.yaml
```

`ping` and `check` share a small JSON state file (`escrow-state.json` by
default, or `--state PATH`) -- `ping` writes to it, `check` reads it. They
need to see the *same* file: the common shape is a single host (the job
and the check both run there, e.g. two cron entries) or a shared
filesystem mount. This is not a hosted, multi-machine service; see "What
this does not do."

## Three statuses, not two

| Status | Meaning |
|---|---|
| `ok` | Pinged within its declared interval. |
| `overdue` | Pinged before, but not recently enough -- **the job that used to run and stopped.** |
| `never_seen` | Declared in `escrow.yaml`, never once recorded a ping -- **the job that never ran at all**, or a typo between the name in the config and the name in the script. |

`overdue` and `never_seen` are deliberately different statuses, not one
"bad" bucket: an operator debugs "this stopped" and "this never started"
differently, and collapsing them would hide which one they're looking at.
Jobs are declared in `escrow.yaml` up front, not discovered from whatever
happens to have pinged -- a job that's only "known" because it once pinged
would be exactly as invisible as before the first time it silently
stopped, or if it never started.

Exit code is `1` if anything is `overdue` or `never_seen`, `0` if every
declared job is `ok`.

## What this does NOT do

- **No daemon, no background process.** There is no `escrow serve` or
  `escrow watch`. `escrow check` is one CLI invocation that reads the
  state file and exits; *you* supply the periodic trigger -- a cron
  entry, a systemd timer, a scheduled GitHub Actions workflow. escrow
  never runs unless something else runs it.
- **No email, Slack, or webhook of its own.** The exit code is the
  interface -- same convention [`receipt`](https://github.com/MaXiMo000/receipt),
  [`invariant`](https://github.com/MaXiMo000/invariant), and
  [`carabiner`](https://github.com/MaXiMo000/carabiner) already share.
  Run `escrow check` as a step that fails loudly in whatever you already
  have (a GitHub Actions job, a systemd `OnFailure=` unit, a cron entry
  piped to your existing paging tool) rather than escrow adding its own
  SMTP client or HTTP dependency for a notification path you may not want.
- **Not a hosted or multi-machine service.** State is one JSON file; `ping`
  and `check` need to see the same one. A fleet of machines all pinging a
  shared endpoint needs a real datastore behind it, not a local file --
  genuinely different scope, not built here.
- **Not resilient to concurrent writers.** State is written to a temp file
  and atomically renamed (a crash mid-write can't corrupt it), but two
  `ping`s racing on the exact same job name on a network filesystem at the
  same instant can still lose one update. Fine for the target use --one
  job pings once per run-- not designed for high-frequency concurrent
  writes.

## Compared to a hosted dead-man's-switch

[healthchecks.io](https://healthchecks.io), [Cronitor](https://cronitor.io),
and [Dead Man's Snitch](https://deadmanssnitch.com) solve the same problem
as a real, mature, hosted service: your job pings a URL over HTTPS, and
their infrastructure -- not yours -- watches the clock and sends email/
Slack/SMS/PagerDuty when a ping is late. If you want alerting that works
without you also solving alerting, and don't mind a third party knowing
when your jobs run, one of those is very likely the better choice --
escrow doesn't compete with that and isn't trying to.

escrow's tradeoff runs the other way, matching the same "stays on your
machine" discipline as the rest of this portfolio: `ping` and `check` never
leave the filesystem, there's no account, no third party ever learns your
job names or schedule, and the whole state is one JSON file you can read,
back up, or delete yourself. The cost of that is everything a hosted
service gives you for free: no scheduling (see "no daemon," above) and no
notification path of its own (see above) -- you supply both, from
infrastructure you already have. Reach for escrow specifically when a
third-party dependency for "is my cron job still running" is the wrong
tradeoff for what the job actually does; reach for a hosted switch
otherwise.

## Tests

```
pip install -e .
python tests/test_duration.py   # "26h", "8d" -> seconds
python tests/test_config.py     # escrow.yaml validation
python tests/test_state.py      # the ping record: real files, real temp dirs
python tests/test_check.py      # ok / overdue / never_seen classification
python tests/test_cli.py        # the real CLI entry point, real files, real argv
```

41 tests. Two exist because testing an actual misconfigured `--state`
(pointed at a directory instead of a file) found a real gap: `load_state`
only caught `JSONDecodeError`, so `IsADirectoryError` -- also an `OSError`
-- escaped as a raw traceback instead of the same graceful "nothing
recorded yet" every other unreadable state file gets.

MIT licensed.
