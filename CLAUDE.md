# Claude Code Instructions — mkt-api

The market data platform's API gateway: gRPC to secmaster-svc and quote-svc, JSON to mkt-ui, with short names added to every answer and an OpenAPI schema mkt-ui's typed client is generated from. The overview is in `README.md`; the plan and status are in mkt-data's `docs/phase-2.md` (Part B), which is the one place for status. Platform mechanics (CI/CD, secrets, database onboarding, Airflow) live in `bcalaway/nyc_pa_aws_gitops`: start with its `docs/app-platform.md`, and keep this repo consistent with it rather than re-explaining it here.

## Git

`main` is protected by the platform's ruleset: no direct pushes, no force-push, and `ci / Build, test, lint` must pass. Work on a branch, `git push` it right after every commit without asking, then open a PR (or update the open one). Bill merges, with one exception: for a PR that changes only docs (`docs/**`, `*.md`), Claude turns on auto-merge, so it merges once CI passes. Merging deploys to the hub automatically (no approval since 2026-10-04); docs-only merges don't deploy (`cd.yml` ignores them).

## Rules

- No data of its own: every answer comes from secmaster-svc or quote-svc.
- Changes to the OpenAPI schema are API changes: mkt-ui's build fails on a breaking one, so change both together.
- No secrets in code, in the repo, or on command lines. App secrets go in SSM under `/home-platform/mkt-api/` (add a row to the platform's `docs/ssm-parameters.md`).
- Keep `deploy/docker-compose.yml`'s `mem_limit`; the hub deploy rejects services without one.
- Update mkt-data's `docs/phase-2.md` when a step lands.

## Testing where PyPI is blocked

Claude's sandbox usually can't reach PyPI, but GitHub works. `scripts/sandbox-test.sh` clones the pure-Python dependencies at their pinned versions and runs the whole suite except `test_grpc.py`, which needs compiled grpcio. For lint, download ruff's pinned release binary from GitHub (`https://github.com/astral-sh/ruff/releases/download/<version>/ruff-x86_64-unknown-linux-gnu.tar.gz`) and run `ruff check app/ tests/ migrations/`, the same as CI. CI's logs aren't readable from the sandbox; read a failure from the check run's annotations (`gh api repos/bcalaway/mkt-api/check-runs/<id>/annotations`).
