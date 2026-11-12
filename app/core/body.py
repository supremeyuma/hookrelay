"""Request body reading with a hard size cap."""

from fastapi import Request


class BodyTooLargeError(Exception):
    """Raised when a request body exceeds the configured maximum."""


async def read_body_with_limit(request: Request, max_bytes: int) -> bytes:
    """Read the body incrementally, aborting as soon as the cap is exceeded."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise BodyTooLargeError
        except ValueError:
            pass

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise BodyTooLargeError

    return bytes(body)
