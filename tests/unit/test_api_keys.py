"""API key generation, hashing, and scope constants."""

from app.core.api_keys import (
    ALL_SCOPES,
    Scope,
    generate_api_key,
    hash_api_key,
)


def test_generated_key_shape_and_hash() -> None:
    generated = generate_api_key()

    assert generated.raw.startswith("hr_live_")
    assert generated.key_prefix == generated.raw[:14]
    assert generated.key_hash == hash_api_key(generated.raw)
    assert len(generated.key_hash) == 64


def test_hashes_are_stable_and_distinct() -> None:
    assert hash_api_key("abc") == hash_api_key("abc")
    assert hash_api_key("abc") != hash_api_key("abd")


def test_all_scopes_match_enum() -> None:
    assert {
        Scope.ADMIN_WRITE.value,
        Scope.EVENTS_READ.value,
        Scope.REPLAY_WRITE.value,
    } == ALL_SCOPES
