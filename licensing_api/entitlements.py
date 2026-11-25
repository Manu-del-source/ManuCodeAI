"""Server-authoritative trial and paid-access calculations."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .config import settings
from .models import Account

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def get_or_create_account(db: Session, clerk_user_id: str, email: str) -> Account:
    account = db.scalar(select(Account).where(Account.clerk_user_id == clerk_user_id))
    if account:
        if account.email != email:
            account.email = email
            db.commit()
        return account
    now = now_utc()
    account = Account(
        clerk_user_id=clerk_user_id, email=email, created_at=now,
        trial_started_at=now, trial_ends_at=now + timedelta(minutes=settings.trial_minutes),
    )
    db.add(account)
    try:
        db.commit()
        db.refresh(account)
        return account
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Account).where(Account.clerk_user_id == clerk_user_id))
        if existing:
            return existing
        raise

def entitlement_payload(account: Account, is_admin: bool = False) -> dict:
    now = now_utc()
    trial_active = account.trial_started_at <= now < account.trial_ends_at
    paid_active = bool(account.paid_until and account.paid_until > now)
    if account.suspended:
        status, has_access = "suspended", False
    elif is_admin:
        status, has_access = "administrator", True
    elif paid_active:
        status, has_access = "subscribed", True
    elif trial_active:
        status, has_access = "trial", True
    else:
        status, has_access = "payment_required", False
    expiry = account.paid_until if paid_active else account.trial_ends_at
    remaining = max(0, int((expiry - now).total_seconds())) if expiry else 0
    return {
        "user_id": account.clerk_user_id, "email": account.email, "status": status,
        "has_access": has_access, "is_admin": is_admin,
        "trial_started_at": account.trial_started_at.isoformat(),
        "trial_ends_at": account.trial_ends_at.isoformat(),
        "paid_until": account.paid_until.isoformat() if account.paid_until else None,
        "seconds_remaining": remaining if not paid_active else None,
        "subscription": {"amount_subunits": settings.subscription_amount_subunits,
                         "currency": settings.currency, "duration_days": settings.subscription_days},
    }
