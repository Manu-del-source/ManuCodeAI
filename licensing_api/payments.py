"""Paystack checkout, signature validation, and transaction verification."""
from __future__ import annotations
import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from .config import settings
from .entitlements import as_utc
from .models import Account, Payment

def initialize_checkout(db: Session, account: Account) -> dict:
    if not settings.paystack_secret_key:
        raise HTTPException(status_code=503, detail="Paystack is not configured.")
    if not settings.paystack_callback_url:
        raise HTTPException(status_code=503, detail="PAYSTACK_CALLBACK_URL is not configured.")
    reference = f"mca_{uuid.uuid4().hex}"
    payload = {
        "email": account.email, "amount": settings.subscription_amount_subunits,
        "currency": settings.currency, "reference": reference,
        "callback_url": settings.paystack_callback_url,
        "metadata": {"product": "ManuCodeAI", "clerk_user_id": account.clerk_user_id,
                     "duration_days": settings.subscription_days},
    }
    try:
        response = httpx.post(
            "https://api.paystack.co/transaction/initialize", json=payload,
            headers={"Authorization": f"Bearer {settings.paystack_secret_key}"}, timeout=15.0,
        )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(status_code=502, detail="Paystack checkout could not be initialized.") from error
    data = result.get("data") or {}
    if not result.get("status") or not data.get("authorization_url"):
        raise HTTPException(status_code=502, detail="Paystack did not return a checkout URL.")
    db.add(Payment(
        account_id=account.id, reference=reference, amount=settings.subscription_amount_subunits,
        currency=settings.currency, status="pending", checkout_url=data["authorization_url"],
    ))
    db.commit()
    return {"reference": reference, "authorization_url": data["authorization_url"]}

def valid_webhook_signature(raw_body: bytes, signature: str | None) -> bool:
    if not settings.paystack_secret_key or not signature:
        return False
    expected = hmac.new(settings.paystack_secret_key.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)

def verify_transaction(reference: str) -> dict:
    try:
        response = httpx.get(
            f"https://api.paystack.co/transaction/verify/{reference}",
            headers={"Authorization": f"Bearer {settings.paystack_secret_key}"}, timeout=15.0,
        )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(status_code=502, detail="Paystack transaction verification failed.") from error
    if not result.get("status"):
        raise HTTPException(status_code=400, detail="Paystack has not verified this transaction.")
    return result.get("data") or {}

def apply_successful_payment(db: Session, reference: str) -> bool:
    payment = db.scalar(select(Payment).where(Payment.reference == reference).with_for_update())
    if not payment:
        return False
    if payment.status == "success":
        return True
    verified = verify_transaction(reference)
    if (verified.get("status") != "success" or verified.get("reference") != reference
        or int(verified.get("amount", 0)) != payment.amount
        or verified.get("currency") != payment.currency):
        payment.status = "failed"
        db.commit()
        return False
    account = db.scalar(select(Account).where(Account.id == payment.account_id).with_for_update())
    if not account:
        return False
    now = datetime.now(timezone.utc)
    current_expiry = as_utc(account.paid_until) if account.paid_until else None
    account.paid_until = (
        current_expiry + timedelta(days=settings.subscription_days)
        if current_expiry and current_expiry > now
        else now + timedelta(days=settings.subscription_days)
    )
    payment.status = "success"
    payment.paid_at = now
    db.commit()
    return True
