"""Write the OpenAPI schema mkt-ui's client is generated from: `python -m app.openapi > openapi.json`."""

import json

from app.main import app


def schema_text() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    print(schema_text(), end="")
