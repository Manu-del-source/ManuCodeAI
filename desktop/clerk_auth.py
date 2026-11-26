"""Clerk Device Authorization and licensing API client for the desktop app.

Only public OAuth client configuration and short-lived user tokens are used here.
Clerk and Paystack secret keys remain on the licensing server.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable

import httpx


TOKEN_SERVICE = "ManuCodeAI"
ACCESS_TOKEN_KEY = "clerk-access-token"
REFRESH_TOKEN_KEY = "clerk-refresh-token"


@dataclass(frozen=True)
class DeviceAuthorization:
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_in: int
    token_endpoint: str


try:
    from .build_config import (
        CLERK_FRONTEND_API_URL as _BUILT_CLERK_FRONTEND_API_URL,
        CLERK_OAUTH_CLIENT_ID as _BUILT_CLERK_OAUTH_CLIENT_ID,
        MANUCODEAI_LICENSING_API_URL as _BUILT_LICENSING_API_URL,
    )
except ImportError:
    _BUILT_CLERK_FRONTEND_API_URL = ""
    _BUILT_CLERK_OAUTH_CLIENT_ID = ""
    _BUILT_LICENSING_API_URL = ""


def licensing_api_url() -> str:
    return (
        os.getenv("MANUCODEAI_LICENSING_API_URL") or _BUILT_LICENSING_API_URL
    ).rstrip("/")


def _clerk_config() -> tuple[str, str]:
    frontend = (
        os.getenv("CLERK_FRONTEND_API_URL") or _BUILT_CLERK_FRONTEND_API_URL
    ).rstrip("/")
    client_id = (
        os.getenv("CLERK_OAUTH_CLIENT_ID") or _BUILT_CLERK_OAUTH_CLIENT_ID
    ).strip()
    if not frontend or not client_id:
        raise RuntimeError(
            "Clerk is not configured in this build. Set CLERK_FRONTEND_API_URL "
            "and CLERK_OAUTH_CLIENT_ID before launching ManuCodeAI."
        )
    return frontend, client_id


def begin_device_authorization() -> DeviceAuthorization:
    frontend, client_id = _clerk_config()
    try:
        metadata_response = httpx.get(
            f"{frontend}/.well-known/oauth-authorization-server", timeout=5.0
        )
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
        device_endpoint = metadata.get("device_authorization_endpoint")
        token_endpoint = metadata.get("token_endpoint")
        if not device_endpoint or not token_endpoint:
            raise RuntimeError(
                "This Clerk OAuth application does not advertise Device Authorization Grant."
            )
        response = httpx.post(
            device_endpoint,
            data={"client_id": client_id, "scope": "openid profile email offline_access"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise RuntimeError("Could not start Clerk sign-in. Check the Clerk OAuth configuration.") from error

    required = ("device_code", "user_code", "verification_uri")
    if any(not data.get(key) for key in required):
        raise RuntimeError("Clerk returned an incomplete device authorization response.")
    return DeviceAuthorization(
        device_code=data["device_code"],
        user_code=data["user_code"],
        verification_uri=data.get("verification_uri_complete") or data["verification_uri"],
        interval=max(1, int(data.get("interval", 5))),
        expires_in=max(1, int(data.get("expires_in", 600))),
        token_endpoint=token_endpoint,
    )


def poll_device_authorization(
    device: DeviceAuthorization,
    cancelled,
    on_wait: Callable[[str], None] | None = None,
) -> dict:
    _, client_id = _clerk_config()
    deadline = time.monotonic() + device.expires_in
    interval = device.interval
    while time.monotonic() < deadline:
        if cancelled.wait(interval):
            raise RuntimeError("Sign-in was cancelled.")
        if on_wait:
            on_wait("Waiting for approval in your browser...")
        try:
            response = httpx.post(
                device.token_endpoint,
                data={
                    "client_id": client_id,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device.device_code,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=5.0,
            )
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise RuntimeError("Could not contact Clerk while waiting for sign-in.") from error

        if response.is_success and payload.get("access_token"):
            if not payload.get("refresh_token"):
                raise RuntimeError("Clerk did not issue a refresh token. Ensure offline_access is enabled.")
            return payload

        error = payload.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        if error == "access_denied":
            raise RuntimeError("Sign-in was denied in the browser.")
        if error == "expired_token":
            raise RuntimeError("The sign-in code expired. Start sign-in again.")
        description = payload.get("error_description") or error or "Unknown OAuth error"
        raise RuntimeError(f"Clerk sign-in failed: {description}")
    raise RuntimeError("The sign-in code expired. Start sign-in again.")


def store_tokens(access_token: str, refresh_token: str, expires_in: int, qsettings) -> None:
    import keyring
    keyring.set_password(TOKEN_SERVICE, ACCESS_TOKEN_KEY, access_token)
    keyring.set_password(TOKEN_SERVICE, REFRESH_TOKEN_KEY, refresh_token)
    qsettings.setValue("clerk/access_token_expires_at", int(time.time()) + int(expires_in))


def clear_tokens(qsettings) -> None:
    import keyring
    for key in (ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY):
        try:
            keyring.delete_password(TOKEN_SERVICE, key)
        except Exception:
            pass
    qsettings.remove("clerk/access_token_expires_at")


def access_token(qsettings) -> str:
    import keyring
    token = keyring.get_password(TOKEN_SERVICE, ACCESS_TOKEN_KEY)
    expiry = int(qsettings.value("clerk/access_token_expires_at", 0) or 0)
    if token and expiry > int(time.time()) + 60:
        return token

    refresh = keyring.get_password(TOKEN_SERVICE, REFRESH_TOKEN_KEY)
    if not refresh:
        raise RuntimeError("Sign in to your ManuCodeAI account first.")
    frontend, client_id = _clerk_config()
    try:
        metadata = httpx.get(
            f"{frontend}/.well-known/oauth-authorization-server", timeout=5.0
        )
        metadata.raise_for_status()
        endpoint = metadata.json()["token_endpoint"]
        response = httpx.post(
            endpoint,
            data={"client_id": client_id, "grant_type": "refresh_token", "refresh_token": refresh},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=5.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, KeyError) as error:
        raise RuntimeError("Your sign-in session expired. Sign in again.") from error

    new_access = payload.get("access_token")
    if not new_access:
        raise RuntimeError("Clerk did not refresh your sign-in session.")
    store_tokens(
        new_access,
        payload.get("refresh_token") or refresh,
        payload.get("expires_in", 86400),
        qsettings,
    )
    return new_access


def fetch_entitlement(token: str) -> dict:
    base = licensing_api_url()
    if not base:
        raise RuntimeError("MANUCODEAI_LICENSING_API_URL is not configured.")
    try:
        response = httpx.get(
            f"{base}/api/v1/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=8.0,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 401:
            raise RuntimeError("Sign-in could not be verified. Please sign in again.") from error
        raise RuntimeError("Could not check your ManuCodeAI access status.") from error
    except (httpx.HTTPError, ValueError) as error:
        raise RuntimeError("Could not reach the ManuCodeAI licensing service.") from error


def create_checkout(token: str) -> str:
    base = licensing_api_url()
    if not base:
        raise RuntimeError("MANUCODEAI_LICENSING_API_URL is not configured.")
    try:
        response = httpx.post(
            f"{base}/api/v1/billing/checkout",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as error:
        try:
            detail = error.response.json().get("detail", "Could not start Paystack checkout.")
        except ValueError:
            detail = "Could not start Paystack checkout."
        raise RuntimeError(detail) from error
    except (httpx.HTTPError, ValueError) as error:
        raise RuntimeError("Could not reach Paystack checkout.") from error
    url = payload.get("authorization_url")
    if not url:
        raise RuntimeError("The licensing service did not return a Paystack checkout URL.")
    return url
