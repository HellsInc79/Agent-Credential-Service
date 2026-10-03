# auth.py

import hashlib
import secrets
import jwt

from datetime import datetime
from datetime import timedelta
from datetime import timezone

import os

JWT_SECRET = os.getenv(
    "JWT_SECRET",
    "CHANGE_ME"
)

ACCESS_TOKEN_MINUTES = max(5, int(os.getenv("ACCESS_TOKEN_MINUTES", "4320")))


def hash_key(value: str):

    return hashlib.sha256(
        value.encode()
    ).hexdigest()


def create_api_key():

    key_id = (
        "key_"
        + secrets.token_urlsafe(12)
    )

    secret = secrets.token_urlsafe(32)

    api_key = f"agt_{key_id}_{secret}"

    return key_id, api_key


def issue_token(agent_id, key_id):
    now = datetime.now(
        timezone.utc
    )

    exp = now + timedelta(
        minutes=ACCESS_TOKEN_MINUTES
    )

    payload = {
        "sub": agent_id,
        "key_id": key_id,
        "iat": now,
        "exp": exp
    }

    token = jwt.encode(
        payload,
        JWT_SECRET,
        algorithm="HS256"
    )

    return token


def verify_token(token):

    return jwt.decode(
        token,
        JWT_SECRET,
        algorithms=["HS256"]
    )
