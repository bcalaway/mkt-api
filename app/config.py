import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # APP_NAME must match this app's ECR repo / IAM role name -- one name ties
    # the platform integration together, see docs/app-platform.md in
    # nyc_pa_aws_gitops.
    app_name: str = os.environ.get("APP_NAME", "mkt-api")

    # The services it reads, over gRPC on the home-platform network (ADR-0020).
    secmaster_grpc: str = os.environ.get("SECMASTER_GRPC", "secmaster-svc:9090")
    quote_grpc: str = os.environ.get("QUOTE_GRPC", "quote-svc:9090")
    grpc_timeout_seconds: float = float(os.environ.get("GRPC_TIMEOUT_SECONDS", "10"))


settings = Settings()
