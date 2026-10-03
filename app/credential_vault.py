"""Encryption for locally stored model-provider API keys."""

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _cipher():
    secret = (os.getenv("CREDENTIAL_ENCRYPTION_KEY") or os.getenv("JWT_SECRET", "")).strip()
    if len(secret) < 32:
        raise RuntimeError(
            "Set JWT_SECRET to at least 32 characters before saving provider API keys. "
            "Set CREDENTIAL_ENCRYPTION_KEY to keep key encryption stable if JWT_SECRET changes."
        )
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(value):
    return _cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value):
    try:
        return _cipher().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "Could not decrypt this agent's provider key. Restore the encryption key used when it was saved."
        ) from exc
