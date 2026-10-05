# mkt-api

The market data platform's API gateway: gRPC to secmaster-svc and quote-svc, JSON to mkt-ui, with short names added to every answer and an OpenAPI schema mkt-ui's typed client is generated from. It holds no data. mkt-ui's server calls it on the `home-platform` network for the signed-in user, so it has no route or login of its own. The plan and status live in mkt-data's [docs/phase-2.md](https://github.com/bcalaway/mkt-data/blob/main/docs/phase-2.md) (Part B, step B9).

It runs on the home platform's AWS hub (`bcalaway/nyc_pa_aws_gitops`) as a registry app (`apps/registry.yml`: no database, no Airflow, no Authentik client, no previews). Started from `templates/python` there, whose README explains the template's pieces; [docs/app-platform.md](https://github.com/bcalaway/nyc_pa_aws_gitops/blob/main/docs/app-platform.md) is the platform contract. The template's `Item` model, `/db-check`, `/login` and `ExampleService.Ping` are still examples until step B9 replaces them.

## How it runs

- **Container:** one process with HTTP on 8000 and gRPC on 9090, internal only: no Traefik route and no DNS record. Other services reach it on the `home-platform` network as `mkt-api:8000` / `mkt-api:9090`.
- **Database:** none. The template's database code stays dormant without `POSTGRES_PASSWORD`.
- **CI/CD:** `ci.yml` runs the platform's `app-ci.yml` on every PR (`ci / Build, test, lint` is required on `main`). `cd.yml` builds and pushes to ECR on merge, then deploys to the hub. Docs-only merges don't deploy.
- **Secrets:** anything under `/home-platform/mkt-api/` in SSM arrives in the container's environment at deploy time.

## Local development

```
pip install -r requirements.txt -r requirements-dev.txt
./gen_proto.sh
uvicorn app.main:app --reload
```

`POSTGRES_PASSWORD` is optional locally; without it the app runs with no database.

Tests and lint: `pytest` and `ruff check app/ tests/ migrations/`, or `docker build --target test .` / `--target lint .`, which is what CI runs. Where PyPI is blocked, `scripts/sandbox-test.sh` runs everything but `tests/test_grpc.py`.
