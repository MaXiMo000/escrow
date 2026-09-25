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

### On one machine

Wrap the job, so both "it ran" and "it ran but failed" get recorded:

```bash
escrow run nightly-backup -- /usr/local/bin/backup.sh
```

(`escrow run` passes the job's own exit code through, so cron and systemd
still see the failure. `escrow ping nightly-backup` at the end of a
script works too; `escrow ping nightly-backup --fail --exit-code 3`
records a failure.) Then, on its own schedule, check every declared job:

```bash
escrow check escrow.yaml     # exit 1 if anything is overdue, failed or never seen
```

### Across machines, CI runners, containers: `escrow serve`

A state file only works when the job and the check share a disk -- and a
checker on the same box as the job dies with it. `escrow serve` runs the
switch as a small HTTP service instead (stdlib only, one process, on a
machine you own): jobs ping it from anywhere, and it watches the clock
itself.

```bash
export ESCROW_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe())")
escrow serve escrow.yaml --host 0.0.0.0 --webhook https://hooks.slack.com/services/...
```

Jobs ping it with nothing but curl:

```bash
# crontab
0 3 * * *  /usr/local/bin/backup.sh && curl -fsS -X POST "https://escrow.example.net/ping/nightly-backup?token=$ESCROW_TOKEN"
```

or with escrow itself, which also reports failures:

```bash
escrow run nightly-backup --url https://escrow.example.net -- /usr/local/bin/backup.sh
```

A scheduled GitHub Actions workflow -- whose runner won't exist tomorrow,
so it can never share a state file -- pings the same way:

```yaml
- run: ./generate-report.sh
- if: always()
  run: |
    curl -fsS -X POST -H "Authorization: Bearer ${{ secrets.ESCROW_TOKEN }}" \
      "https://escrow.example.net/ping/weekly-report${{ job.status != 'success' && '/fail' || '' }}"
```

Whenever a job's status changes -- goes quiet, fails, never showed up,
recovers -- escrow POSTs one JSON alert to `--webhook`. The payload
carries `text` (what Slack and Mattermost webhooks read) and `content`
(Discord), plus `job`, `status`, `previous` and `detail` for anything
else. Alerts fire on changes only, never repeatedly for the same state.

| endpoint | |
|---|---|
| `POST /ping/<job>` | ran and succeeded |
| `POST /ping/<job>/fail?exit_code=N` | ran and failed |
| `GET /status` | every job's status, as JSON |
| `GET /health` | `ok` -- the one route that needs no token |

Only jobs declared in `escrow.yaml` are accepted (anything else is a 404),
so a stray ping can't grow the state. The token is required on every
other route and compared in constant time; `escrow serve` refuses to
listen beyond localhost without one, since anyone who can reach the port
could otherwise mark a dead job healthy. Put it behind your usual TLS
reverse proxy (Caddy, nginx) when it's reachable from the internet.

## Four statuses

| Status | Meaning |
|---|---|
| `ok` | Pinged within its declared interval. |
| `overdue` | Pinged before, but not recently enough -- **the job that used to run and stopped.** |
| `never_seen` | Declared in `escrow.yaml`, never once recorded a ping -- **the job that never ran at all**, or a typo between the name in the config and the name in the script. |
| `failed` | Its last run reported failure (`escrow run`, `ping --fail`, `/fail`), until the next success. |

`overdue` and `never_seen` are deliberately different statuses, not one
"bad" bucket: an operator debugs "this stopped" and "this never started"
differently. A failure doesn't move `last_seen`, so a job that keeps
failing also goes `overdue` on schedule. Jobs are declared in
`escrow.yaml` up front, not discovered from whatever happens to have
pinged -- a job that's only "known" because it once pinged would be
exactly as invisible as before the first time it silently stopped.

Exit code is `1` if anything isn't `ok`.

## What this does NOT do

- **No cron expressions.** A job declares an interval ("26h"), not a
  schedule ("03:00 on weekdays"). An interval with some slack covers most
  real jobs; a schedule-aware check is real, addable work.
- **One `escrow serve` is one process.** If the machine running it dies,
  nothing alerts about *that* -- put its `/health` behind whatever uptime
  check you already have, or run the check from somewhere else.
- **Webhook alerts only.** No built-in email or SMS: a webhook reaches
  Slack, Discord, Mattermost, ntfy, PagerDuty (via Events API) or your own
  endpoint without escrow carrying an SMTP client.
- **Not a multi-tenant service.** One config, one token, one state file.

## Compared to a hosted dead-man's-switch

[healthchecks.io](https://healthchecks.io), [Cronitor](https://cronitor.io),
and [Dead Man's Snitch](https://deadmanssnitch.com) solve the same problem
as a real, mature, hosted service: your job pings a URL over HTTPS, and
their infrastructure -- not yours -- watches the clock and sends email/
Slack/SMS/PagerDuty when a ping is late. If you want alerting that works
without you also solving alerting, and don't mind a third party knowing
when your jobs run, one of those is very likely the better choice --
escrow doesn't compete with that and isn't trying to.

escrow's tradeoff runs the other way: nothing leaves machines you own.
There's no account, no third party ever learns your job names or
schedule, and the whole state is one JSON file you can read, back up or
delete. `escrow check` alone needs no server at all; `escrow serve` adds
the watching and alerting a hosted service would give you, in one stdlib
process, when your jobs live on more than one machine.

Healthchecks.io's own open-source server can also be self-hosted, and is
the better pick if you want its dashboard, team accounts and dozens of
integrations -- at the cost of running a Django app and a database.
escrow is for when a YAML file, one process and a webhook are all the
operational weight a job's dead-man's-switch deserves.

## Tests

```
pip install -e .
python tests/test_duration.py   # "26h", "8d" -> seconds
python tests/test_config.py     # escrow.yaml validation
python tests/test_state.py      # the ping record: real files, real temp dirs
python tests/test_check.py      # ok / overdue / never_seen classification
python tests/test_cli.py        # the real CLI entry point, real files, real argv
python tests/test_serve.py      # escrow serve over real HTTP, alerts to a real webhook receiver
```

Two tests exist because testing an actual misconfigured `--state`
(pointed at a directory instead of a file) found a real gap: `load_state`
only caught `JSONDecodeError`, so `IsADirectoryError` -- also an `OSError`
-- escaped as a raw traceback instead of the same graceful "nothing
recorded yet" every other unreadable state file gets.

MIT licensed.
