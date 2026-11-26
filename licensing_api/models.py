"""Persistent user entitlement and payment records."""
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clerk_user_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    trial_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    trial_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    payments: Mapped[list["Payment"]] = relationship(back_populates="account")

class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("reference", name="uq_payment_reference"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    reference: Mapped[str] = mapped_column(String(100), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="KES")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    account: Mapped[Account] = relationship(back_populates="payments")
