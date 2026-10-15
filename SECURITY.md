# Security

## Reporting a problem

Report vulnerabilities privately through GitHub: **Security -> Report a
vulnerability** on this repository. Please do not open a public issue for
anything exploitable. You will get an acknowledgement within 72 hours.

## What escrow handles

- **Local state only by default.** `escrow ping`, `run` and `check` read and
  write a JSON state file; nothing leaves the machine.
- **`escrow serve` binds to 127.0.0.1 by default.** Pings require the token
  from `$ESCROW_TOKEN`, compared in constant time.
- **Tokens come from the environment** (`ESCROW_TOKEN`, `GITHUB_TOKEN` /
  `GH_TOKEN`) and never from `escrow.yaml`. Prefer `$ESCROW_TOKEN` to
  `--token`: command lines are visible to other local users and in CI logs.
- `escrow gha` and `escrow k8s` only read: they list workflow runs and
  CronJobs, and never change them.

## Supply chain

- GitHub Actions are pinned to full commit SHAs.
- Releases publish to PyPI through trusted publishing (OIDC); no long-lived
  token is stored in the repository.
