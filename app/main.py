"""mkt-api: the market data platform's API gateway (mkt-data's docs/phase-2.md, Part B step 9).

JSON over HTTP for mkt-ui's server, read from secmaster-svc and quote-svc
over gRPC (app/upstream.py); it holds no data. Internal only: no Traefik
route, no login of its own. mkt-ui's server calls it as mkt-api:8000 on the
home-platform network for a user Authentik has already signed in.
"""

from fastapi import FastAPI

from app import api
from app.upstream import UpstreamError

app = FastAPI(
    title="mkt-api",
    version="1",
    description="The market data platform's API: Treasury CMT yields, curves, spreads and the security master. "
                "Instruments by short name; values as decimal strings.",
)
app.include_router(api.router)
app.add_exception_handler(UpstreamError, api.upstream_error_handler)


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}
