# Multi-stage build following the convention app-ci.yml (nyc_pa_aws_gitops)
# relies on: a `lint` stage and a `test` stage ahead of the final runtime
# stage, so the platform's CI workflow stays language-agnostic instead of
# hardcoding Python tooling. `docker build` with no --target builds the
# last stage (`final`) -- exactly what app-build-push.yml pushes to ECR.

FROM python:3.12-slim AS base
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
COPY proto/ proto/
COPY gen_proto.sh start.sh openapi.json ./
# gRPC stubs (ADR-0020) are generated here, never committed.
RUN ./gen_proto.sh

FROM base AS dev
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests/ tests/
COPY ruff.toml .

FROM dev AS lint
RUN ruff check app/ tests/

FROM dev AS test
RUN pytest

FROM base AS final
# 8000: HTTP, internal to home-platform only (mkt-ui's server).
EXPOSE 8000
CMD ["./start.sh"]
