"""mkt-api: the market data platform's API gateway (mkt-data's docs/phase-2.md, Part B step 9).

JSON over HTTP for mkt-ui's server, read from secmaster-svc and quote-svc
over gRPC (app/upstream.py); it holds no data. Internal only: no Traefik
route, no login of its own. mkt-ui's server calls it as mkt-api:8000 on the
home-platform network for a user Authentik has already signed in.
"""

import time

from fastapi import FastAPI, Request

from app import api
from app.upstream import UpstreamError, start_timing

app = FastAPI(
    title="mkt-api",
    version="1",
    description="The market data platform's API: Treasury CMT yields, curves, spreads and the security master. "
                "Instruments by short name; values as decimal strings.",
)
app.include_router(api.router)
app.add_exception_handler(UpstreamError, api.upstream_error_handler)


@app.middleware("http")
async def server_timing(request: Request, call_next):
    """Server-Timing: the request's time here (`api`) and the part spent waiting on secmaster-svc and
    quote-svc (`upstream`), so a page can tell slow data from slow network."""
    upstream = start_timing()
    t0 = time.perf_counter()
    response = await call_next(request)
    total = (time.perf_counter() - t0) * 1000
    response.headers["Server-Timing"] = f"api;dur={total:.1f}, upstream;dur={upstream[0]:.1f}"
    return response


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}
