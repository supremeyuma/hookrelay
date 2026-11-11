"""Error envelope tests, including the no-stack-trace guarantee."""

import json

from fastapi import Request
from fastapi.exceptions import RequestValidationError

from app.core.errors import unhandled_error_handler, validation_error_handler


def _request(path: str = "/boom") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 1234),
        }
    )


async def test_unhandled_error_hides_internal_message() -> None:
    response = await unhandled_error_handler(_request(), RuntimeError("secret detail"))
    payload = json.loads(response.body)

    assert response.status_code == 500
    assert payload["detail"] == "internal server error"
    assert payload["path"] == "/boom"
    assert "secret detail" not in response.body.decode()


async def test_validation_error_reports_field_path() -> None:
    exc = RequestValidationError(
        [
            {
                "type": "missing",
                "loc": ("body", "name"),
                "msg": "Field required",
                "input": {},
            }
        ]
    )

    response = await validation_error_handler(_request("/ingest/demo"), exc)
    payload = json.loads(response.body)

    assert response.status_code == 422
    assert payload["detail"][0]["loc"] == ["body", "name"]
    assert payload["path"] == "/ingest/demo"
