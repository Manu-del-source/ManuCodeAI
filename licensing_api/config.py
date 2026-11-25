"""Environment-backed configuration for the licensing service."""
from __future__ import annotations
import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./manucodeai_licensing.db")
    clerk_issuer: str = os.getenv("CLERK_ISSUER", "").rstrip("/")
    clerk_jwks_url: str = os.getenv("CLERK_JWKS_URL", "")
    clerk_secret_key: str = os.getenv("CLERK_SECRET_KEY", "")
    clerk_publishable_key: str = os.getenv("CLERK_PUBLISHABLE_KEY", "")
    admin_emails: tuple[str, ...] = tuple(
        value.strip().lower() for value in
        os.getenv("MANUCODEAI_ADMIN_EMAILS", "kiptooe142@gmail.com").split(",") if value.strip()
    )
    paystack_secret_key: str = os.getenv("PAYSTACK_SECRET_KEY", "")
    paystack_callback_url: str = os.getenv("PAYSTACK_CALLBACK_URL", "")
    trial_minutes: int = 15
    subscription_days: int = 14
    subscription_amount_subunits: int = 25000
    currency: str = "KES"

settings = Settings()
