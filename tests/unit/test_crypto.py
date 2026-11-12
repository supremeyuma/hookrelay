"""Secret encryption round-trips and failure behaviour."""

import pytest
from cryptography.fernet import InvalidToken

from app.core.crypto import SecretCipher

KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def test_round_trip() -> None:
    cipher = SecretCipher(KEY)
    token = cipher.encrypt("super-secret")

    assert token != "super-secret"
    assert cipher.decrypt(token) == "super-secret"


def test_encryption_is_randomized() -> None:
    cipher = SecretCipher(KEY)

    assert cipher.encrypt("same") != cipher.encrypt("same")


def test_invalid_token_raises() -> None:
    cipher = SecretCipher(KEY)

    with pytest.raises(InvalidToken):
        cipher.decrypt("not-a-token")
