# mkt-api

The market data platform's API gateway: gRPC to secmaster-svc and quote-svc, JSON to mkt-ui, with short names added to every answer and an OpenAPI schema mkt-ui's typed client is generated from. It holds no data. mkt-ui's server calls it on the `home-platform` network for the signed-in user, so it has no route or login of its own. The plan and status live in mkt-data's [docs/phase-2.md](https://github.com/bcalaway/mkt-data/blob/main/docs/phase-2.md) (Part B, step B9).

It runs on the home platform's AWS hub (`bcalaway/nyc_pa_aws_gitops`) as a registry app (`apps/registry.yml`: no database, no Airflow, no Authentik client, no previews). Started from `templates/python` there; [docs/app-platform.md](https://github.com/bcalaway/nyc_pa_aws_gitops/blob/main/docs/app-platform.md) is the platform contract. The template's database, login and gRPC server examples are gone: mkt-api holds no data, has no login of its own and serves only HTTP.

## The API

JSON under `/api/` (`app/api.py`), read from secmaster-svc and quote-svc over gRPC (`app/upstream.py`, with `proto/securities.proto` and `proto/quotes.proto` copied from those repos). Instruments by short name, never by sec_id; values as decimal strings, computed with `Decimal`: `value` is the rate as a decimal (`"0.041"`), `percent` the same in percent (`"4.10"`), spreads in basis points (`"52"`).

| Endpoint | Answer |
|---|---|
| `GET /api/instruments` | every instrument, shortest tenor first |
| `GET /api/instruments/{name}` | one by short name or alias: identifiers, notes, latest golden value |
| `GET /api/search?q=` | instruments whose name, alias, identifier or description contains q |
| `GET /api/series?name=&name=&start=&end=&source=&interval=` | golden yields (or one source's) with the source of each; default a year, daily; `interval` week, month, quarter or year gives bars (open, high, low, close) |
| `GET /api/curve?date=&compare=1W&compare=1M&compare=1Y` | the curve on a date (default latest) and before it, each on the last business day on or before its date |
| `GET /api/series/daily?name=&start=&end=&source=` | every day since 1962 (default) in columns: dates, percents, source runs, for a chart that loads once and zooms locally |
| `GET /api/spread/daily?long=&short=&start=&end=` | every day's spread since 1962 (default) in columns: dates and basis points |
| `GET /api/spread?long=UST-10Y-CMT&short=UST-2Y-CMT&start=&end=&interval=` | long minus short in basis points, daily or in bars |

A service that doesn't answer is a 502 naming it; an unknown name is a 404.

**The schema is the contract.** `openapi.json` is committed and a test fails if it's out of date (`python -m app.openapi > openapi.json` regenerates it). mkt-ui's typed client is generated from it, so a change here is an API change: update mkt-ui in step.

## How it runs

- **Container:** one process, HTTP on 8000, internal only: no Traefik route and no DNS record. mkt-ui's server reaches it on the `home-platform` network as `mkt-api:8000`, for a user Authentik has already signed in.
- **Upstreams:** `SECMASTER_GRPC` (default `secmaster-svc:9090`) and `QUOTE_GRPC` (`quote-svc:9090`).
- **CI/CD:** `ci.yml` runs the platform's `app-ci.yml` on every PR (`ci / Build, test, lint` is required on `main`). `cd.yml` builds and pushes to ECR on merge, then deploys to the hub. Docs-only merges don't deploy.
- **Secrets:** none yet; anything under `/home-platform/mkt-api/` in SSM would arrive in the container's environment at deploy time.

## Local development

```
pip install -r requirements.txt -r requirements-dev.txt
./gen_proto.sh
uvicorn app.main:app --reload
```

Tests and lint: `pytest` and `ruff check app/ tests/`, or `docker build --target test .` / `--target lint .`, which is what CI runs. Where PyPI is blocked, `scripts/sandbox-test.sh` runs everything but `tests/test_grpc.py`.
