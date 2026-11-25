"""Clerk JWT verification and server-side user identity resolution."""
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

def require_identity(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Identity:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Sign in with your ManuCodeAI account.")
    if not settings.clerk_issuer or not settings.clerk_jwks_url:
        raise HTTPException(status_code=503, detail="Clerk authentication is not configured.")
    try:
        key = _jwks_client().get_signing_key_from_jwt(credentials.credentials)
        claims = jwt.decode(
            credentials.credentials, key.key, algorithms=["RS256"],
            issuer=settings.clerk_issuer, options={"verify_aud": False},
        )
        user_id = claims.get("sub")
        if not isinstance(user_id, str) or not user_id:
            raise jwt.InvalidTokenError("Missing Clerk subject.")
        audience = claims.get("aud")
        if audience:
            audiences = audience if isinstance(audience, list) else [audience]
            if not settings.clerk_oauth_client_id or settings.clerk_oauth_client_id not in audiences:
                raise jwt.InvalidTokenError("Token audience is not this ManuCodeAI OAuth client.")
        else:
            party = claims.get("azp")
            if settings.clerk_authorized_parties and party not in settings.clerk_authorized_parties:
                raise jwt.InvalidTokenError("Token authorized party is not allowed.")
    except (jwt.PyJWTError, RuntimeError) as error:
        raise HTTPException(status_code=401, detail="Your sign-in session is invalid or expired.") from error
    email = _clerk_email(user_id)
    return Identity(user_id=user_id, email=email, is_admin=email in settings.admin_emails)

def require_admin(identity: Identity = Depends(require_identity)) -> Identity:
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return identity
