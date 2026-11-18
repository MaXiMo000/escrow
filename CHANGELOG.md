# Changelog

## Unreleased

- `escrow serve` has a status page at `/`: every job, worst first, refreshing once a minute, token-gated, no scripts.
- A container image for `escrow serve` (non-root, state on a volume, health check), published to `ghcr.io/maximo000/escrow` on release.

## 0.5.0 - 2026-11-18

- `schedule:` for pinged jobs: a five-field cron line, with `grace` (default 1h) and `timezone` (default UTC). A job is overdue when its latest scheduled run plus the grace passes without a ping.

## 0.4.1 - 2026-10-16

- Documentation: a demo of real output at the top of the README, a security policy, a changelog, and issue templates.

## 0.4.0 - 2026-10-02

- `escrow gha --org` scans every repository of an organization or user.
- `escrow k8s` checks Kubernetes CronJobs.
- A GitHub Action.

## 0.3.0 - 2026-09-27

- `escrow gha`: find scheduled GitHub Actions workflows that stopped running, including ones GitHub disabled.

## 0.2.0 - 2026-09-26

- `escrow serve` accepts pings over HTTP and alerts a webhook on changes.
- `escrow run` records failures as well as runs.
- CI on Windows.

## 0.1.0 - 2026-09-10

- First release: declare jobs and intervals, ping, and check for silence.
