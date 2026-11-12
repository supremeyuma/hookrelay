"""Destination URL validation and SSRF protection."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse


class InvalidDestinationUrl(ValueError):
    """Raised when a destination URL is malformed or resolves to a private address."""


async def validate_destination_url(url: str, *, allow_private: bool) -> str:
    """Validate scheme, credentials, port, and (unless allowed) resolved address."""
    if len(url) > 2048:
        msg = "destination URL exceeds 2048 characters"
        raise InvalidDestinationUrl(msg)

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        msg = "destination URL must use http or https"
        raise InvalidDestinationUrl(msg)
    if not parsed.hostname:
        msg = "destination URL requires a host"
        raise InvalidDestinationUrl(msg)
    if parsed.username or parsed.password:
        msg = "destination URL must not contain credentials"
        raise InvalidDestinationUrl(msg)

    try:
        port = parsed.port
    except ValueError as exc:
        msg = "destination URL has an invalid port"
        raise InvalidDestinationUrl(msg) from exc
    if port is not None and not 1 <= port <= 65535:
        msg = "destination URL has an invalid port"
        raise InvalidDestinationUrl(msg)

    if allow_private:
        return url

    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        msg = "destination resolves to a non-public address"
        raise InvalidDestinationUrl(msg)

    try:
        address_info = await asyncio.to_thread(socket.getaddrinfo, hostname, port)
    except socket.gaierror as exc:
        msg = "destination host cannot be resolved"
        raise InvalidDestinationUrl(msg) from exc

    for info in address_info:
        raw_address = str(info[4][0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError:
            continue
        if not address.is_global:
            msg = "destination resolves to a non-public address"
            raise InvalidDestinationUrl(msg)

    return url
