"""Local renewable JWTs and optional Cognito user-pool token verification."""

import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException

from app.platform_store import authenticate_agent_key


def _auth_mode():
    return os.getenv("AUTH_MODE", "local").strip().lower()


def issue_platform_token(api_key):
    agent = authenticate_agent_key(api_key)
    if not agent:
        raise HTTPException(status_code=401, detail="Agent API key is invalid, revoked, or disabled.")
    secret = os.getenv("JWT_SECRET", "").strip()
    if len(secret) < 32:
        raise HTTPException(status_code=503, detail="Set JWT_SECRET to at least 32 characters in .env, then restart Uvicorn.")
    lifetime = max(5, int(os.getenv("ACCESS_TOKEN_MINUTES", "4320")))
    issued = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": agent["id"],
            "agent_id": agent["id"],
            "name": agent["name"],
            "title": agent["title"],
            "token_use": "access",
            "iat": issued,
            "exp": issued + timedelta(minutes=lifetime),
            "iss": "local-agent-platform",
            "aud": "agent-platform",
        },
        secret,
        algorithm="HS256",
    )
    return {"access_token": token, "token_type": "bearer", "expires_in": lifetime * 60, "agent": {"id": agent["id"], "name": agent["name"], "title": agent["title"]}}


def _verify_local(token):
    secret = os.getenv("JWT_SECRET", "").strip()
    if len(secret) < 32:
        raise jwt.InvalidTokenError("Local JWT_SECRET is not configured")
    return jwt.decode(token, secret, algorithms=["HS256"], audience="agent-platform", issuer="local-agent-platform")


@lru_cache(maxsize=8)
def _jwks_client(issuer):
    try:
        from jwt import PyJWKClient
    except ImportError as exc:
        raise RuntimeError("PyJWT cryptography support is required for Cognito verification.") from exc
    return PyJWKClient(f"{issuer}/.well-known/jwks.json", cache_jwk_set=True, lifespan=3600)


def _verify_cognito(token):
    region = os.getenv("COGNITO_REGION", "").strip()
    pool_id = os.getenv("COGNITO_USER_POOL_ID", "").strip()
    client_id = os.getenv("COGNITO_APP_CLIENT_ID", "").strip()
    if not region or not pool_id:
        raise jwt.InvalidTokenError("Cognito is selected but COGNITO_REGION or COGNITO_USER_POOL_ID is not configured")
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"
    jwks_client = _jwks_client(issuer)
    signing_key = jwks_client.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=issuer,
        options={"verify_aud": False},
    )
    if claims.get("token_use") != "access":
        raise jwt.InvalidTokenError("Cognito access token required")
    if client_id and claims.get("client_id") != client_id:
        raise jwt.InvalidTokenError("Cognito token is for a different app client")
    required_scope = os.getenv("COGNITO_REQUIRED_SCOPE", "").strip()
    if required_scope and required_scope not in claims.get("scope", "").split():
        raise jwt.InvalidTokenError("Cognito token is missing the required resource-server scope")
    return claims


def verify_bearer_token(token):
    mode = _auth_mode()
    errors = []
    if mode not in {"local", "cognito", "hybrid"}:
        raise HTTPException(status_code=500, detail="AUTH_MODE must be local, cognito, or hybrid.")
    if mode in {"local", "hybrid"}:
        try:
            return _verify_local(token)
        except jwt.InvalidTokenError as exc:
            errors.append(exc)
    if mode in {"cognito", "hybrid"}:
        try:
            return _verify_cognito(token)
        except Exception as exc:
            errors.append(exc)
    raise HTTPException(status_code=401, detail="Access token is invalid or expired. Exchange an agent key for a new token or sign in through Cognito.") from (errors[-1] if errors else None)


def require_auth(authorization: str | None = Header(default=None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Add an Authorization: Bearer <access_token> header.")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Authorization must use the Bearer scheme.")
    return verify_bearer_token(token)
