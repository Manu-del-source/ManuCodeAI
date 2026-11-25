"""Clerk OAuth access-token and browser session verification."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import httpx
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from .config import settings

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Identity:
    user_id: str
    email: str
    is_admin: bool


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    if not settings.clerk_jwks_url:
        raise RuntimeError("CLERK_JWKS_URL is not configured.")
    return PyJWKClient(settings.clerk_jwks_url, cache_keys=True)


def _verify_oauth_access_token(token: str) -> str:
    """Verify Clerk OAuth access tokens, including opaque tokens, using Clerk BAPI."""
    if not settings.clerk_secret_key or not settings.clerk_oauth_client_id:
        raise HTTPException(status_code=503, detail="Clerk OAuth verification is not configured.")
    try:
        response = httpx.post(
            "https://api.clerk.com/oauth_applications/access_tokens/verify",
            json={"access_token": token},
            headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
            timeout=8.0,
        )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(status_code=401, detail="Your Clerk OAuth token is invalid or expired.") from error

    if (
        result.get("active") is False
        or result.get("revoked") is True
        or result.get("expired") is True
        or result.get("client_id") != settings.clerk_oauth_client_id
    ):
        raise HTTPException(status_code=401, detail="Your Clerk OAuth token is inactive or not issued to ManuCodeAI.")
    scopes = set(result.get("scopes") or [])
    if not {"profile", "email"}.issubset(scopes):
        raise HTTPException(status_code=403, detail="The Clerk token must include profile and email scopes.")
    user_id = result.get("subject")
    if not isinstance(user_id, str) or not user_id.startswith("user_"):
        raise HTTPException(status_code=401, detail="Clerk did not return a valid user identity.")
    return user_id


def _verify_session_token(token: str) -> str:
    if not settings.clerk_issuer or not settings.clerk_jwks_url:
        raise HTTPException(status_code=503, detail="Clerk session verification is not configured.")
    try:
        key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"], issuer=settings.clerk_issuer,
            options={"verify_aud": False},
        )
        user_id = claims.get("sub")
        if not isinstance(user_id, str) or not user_id.startswith("user_"):
            raise jwt.InvalidTokenError("Missing Clerk user subject.")
        party = claims.get("azp")
        if settings.clerk_authorized_parties and party not in settings.clerk_authorized_parties:
            raise jwt.InvalidTokenError("Token authorized party is not allowed.")
        return user_id
    except jwt.PyJWTError as error:
        raise HTTPException(status_code=401, detail="Your Clerk session is invalid or expired.") from error


def _clerk_email(user_id: str) -> str:
    if not settings.clerk_secret_key:
        raise HTTPException(status_code=503, detail="Clerk server credentials are not configured.")
    try:
        response = httpx.get(
            f"https://api.clerk.com/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
            timeout=8.0,
        )
        response.raise_for_status()
        user = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(status_code=502, detail="Unable to verify the Clerk account profile.") from error
    primary_id = user.get("primary_email_address_id")
    for address in user.get("email_addresses", []):
        verification = address.get("verification") or {}
        if (address.get("id") == primary_id and address.get("email_address")
                and verification.get("status") == "verified"):
            return address["email_address"].strip().lower()
    raise HTTPException(status_code=403, detail="A verified primary email address is required.")


def require_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Identity:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Sign in with your ManuCodeAI account.")

    token = credentials.credentials
    try:
        unverified = jwt.decode(
            token, options={"verify_signature": False, "verify_exp": False, "verify_aud": False}
        )
    except jwt.PyJWTError:
        unverified = {}

    # Clerk browser session tokens contain sid. Native OAuth access tokens are
    # verified through Clerk's dedicated endpoint, including opaque-token mode.
    user_id = _verify_session_token(token) if unverified.get("sid") else _verify_oauth_access_token(token)
    email = _clerk_email(user_id)
    return Identity(user_id=user_id, email=email, is_admin=email in settings.admin_emails)


def require_admin(identity: Identity = Depends(require_identity)) -> Identity:
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return identity
