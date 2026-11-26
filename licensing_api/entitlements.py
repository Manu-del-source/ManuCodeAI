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

def as_utc(value: datetime) -> datetime:
    """SQLite drops timezone metadata; normalize DB timestamps before comparisons."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

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
    trial_start = as_utc(account.trial_started_at)
    trial_end = as_utc(account.trial_ends_at)
    paid_until = as_utc(account.paid_until) if account.paid_until else None
    trial_active = trial_start <= now < trial_end
    paid_active = bool(paid_until and paid_until > now)
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
    expiry = paid_until if paid_active else trial_end
    remaining = max(0, int((expiry - now).total_seconds())) if expiry else 0
    return {
        "user_id": account.clerk_user_id, "email": account.email, "status": status,
        "has_access": has_access, "is_admin": is_admin,
        "trial_started_at": trial_start.isoformat(), "trial_ends_at": trial_end.isoformat(),
        "paid_until": paid_until.isoformat() if paid_until else None,
        "seconds_remaining": remaining if not paid_active else None,
        "subscription": {"amount_subunits": settings.subscription_amount_subunits,
                         "currency": settings.currency, "duration_days": settings.subscription_days},
    }
