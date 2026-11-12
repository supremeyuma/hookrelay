"""Destination URL validation and SSRF guard."""

import pytest

from app.core.urls import InvalidDestinationUrl, validate_destination_url


async def test_private_target_allowed_when_configured() -> None:
    url = "http://127.0.0.1:9099/hook"

    assert await validate_destination_url(url, allow_private=True) == url


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/hook",
        "file:///etc/passwd",
        "http://",
        "http://user:pass@example.com/hook",
        "http://example.com:99999/hook",
        "not-a-url",
    ],
)
async def test_malformed_urls_are_rejected(url: str) -> None:
    with pytest.raises(InvalidDestinationUrl):
        await validate_destination_url(url, allow_private=True)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:9099/hook",
        "http://127.0.0.1:9099/hook",
        "http://10.0.0.1/hook",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/hook",
    ],
)
async def test_private_addresses_are_rejected_by_default(url: str) -> None:
    with pytest.raises(InvalidDestinationUrl, match="non-public"):
        await validate_destination_url(url, allow_private=False)


async def test_public_literal_is_allowed_by_default() -> None:
    url = "http://93.184.216.34/hook"

    assert await validate_destination_url(url, allow_private=False) == url
