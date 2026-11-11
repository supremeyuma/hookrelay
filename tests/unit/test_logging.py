"""Request correlation and JSON log formatting."""

import json
import logging

from fastapi.testclient import TestClient

from app.core.logging import JsonFormatter, request_id_ctx


def test_request_id_is_generated(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.headers["X-Request-ID"]


def test_supplied_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"


def test_json_formatter_includes_correlation_id() -> None:
    token = request_id_ctx.set("corr-1")
    try:
        record = logging.LogRecord(
            name="hookrelay.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_ctx.reset(token)

    assert payload["msg"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "corr-1"


def test_json_formatter_is_valid_without_request_id() -> None:
    record = logging.LogRecord(
        name="hookrelay.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="no request",
        args=(),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert "request_id" not in payload
