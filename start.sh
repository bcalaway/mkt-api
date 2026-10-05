#!/bin/sh
# Container entrypoint (Dockerfile `final` stage). No database, so nothing to
# migrate. Internal only (mkt-ui's server calls it on the home-platform
# network), so no proxy headers to trust.
set -e
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
