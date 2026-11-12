"""The committed OpenAPI document covers the public surface."""

from app.main import app


def test_openapi_contains_all_public_paths() -> None:
    spec = app.openapi()
    paths = spec["paths"]

    for path in [
        "/healthz",
        "/readyz",
        "/ingest/{source_slug}",
        "/events/{event_id}",
        "/events/{event_id}/replay",
        "/admin/sources",
        "/admin/sources/{slug}/destinations",
        "/admin/destinations/{destination_id}",
        "/admin/dead-letters/{dead_letter_id}/replay",
        "/admin/api-keys",
        "/admin/api-keys/{api_key_id}",
    ]:
        assert path in paths, path


def test_openapi_version_matches_release() -> None:
    assert app.openapi()["info"]["version"] == "1.0.0"
