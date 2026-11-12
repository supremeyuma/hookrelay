"""Encryption of provider secrets at rest."""

from cryptography.fernet import Fernet


class SecretCipher:
    """Small wrapper around Fernet so call sites never handle raw keys."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()
