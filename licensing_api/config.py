"""Environment-backed configuration for the licensing service."""
from __future__ import annotations
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

def _csv_env(name: str) -> tuple[str, ...]:
    return tuple(v.strip() for v in os.getenv(name, "").split(",") if v.strip())

@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./manucodeai_licensing.db")
    clerk_issuer: str = os.getenv("CLERK_ISSUER", "").rstrip("/")
    clerk_jwks_url: str = os.getenv("CLERK_JWKS_URL", "")
    clerk_frontend_api_url: str = os.getenv("CLERK_FRONTEND_API_URL", "").rstrip("/")
    clerk_oauth_client_id: str = os.getenv("CLERK_OAUTH_CLIENT_ID", "")
    clerk_secret_key: str = os.getenv("CLERK_SECRET_KEY", "")
    clerk_publishable_key: str = os.getenv("CLERK_PUBLISHABLE_KEY", "")
    clerk_authorized_parties: tuple[str, ...] = _csv_env("CLERK_AUTHORIZED_PARTIES")
    admin_emails: tuple[str, ...] = tuple(v.lower() for v in _csv_env("MANUCODEAI_ADMIN_EMAILS"))
    paystack_secret_key: str = os.getenv("PAYSTACK_SECRET_KEY", "")
    paystack_callback_url: str = os.getenv("PAYSTACK_CALLBACK_URL", "")
    trial_minutes: int = 15
    subscription_days: int = 14
    subscription_amount_subunits: int = 25000
    currency: str = "KES"

settings = Settings()
