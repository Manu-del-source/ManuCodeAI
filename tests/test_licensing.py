"""Regression tests for server-authoritative ManuCodeAI trial entitlements."""
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from licensing_api.database import Base
from licensing_api.entitlements import entitlement_payload, get_or_create_account
from licensing_api.models import Account


class EntitlementTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.session_factory()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_first_authenticated_account_gets_one_fifteen_minute_trial(self):
        account = get_or_create_account(self.db, "user_trial_1", "trial@example.com")
        payload = entitlement_payload(account)
        self.assertEqual(payload["status"], "trial")
        self.assertTrue(payload["has_access"])
        self.assertGreaterEqual(payload["seconds_remaining"], 895)
        self.assertLessEqual(payload["seconds_remaining"], 900)

    def test_repeated_login_does_not_restart_trial(self):
        account = get_or_create_account(self.db, "user_trial_2", "first@example.com")
        original_start = account.trial_started_at
        original_end = account.trial_ends_at
        again = get_or_create_account(self.db, "user_trial_2", "updated@example.com")
        self.assertEqual(again.trial_started_at, original_start)
        self.assertEqual(again.trial_ends_at, original_end)
        self.assertEqual(again.email, "updated@example.com")

    def test_expired_trial_requires_payment(self):
        now = datetime.now(timezone.utc)
        account = Account(
            clerk_user_id="user_expired", email="expired@example.com",
            created_at=now - timedelta(hours=1),
            trial_started_at=now - timedelta(minutes=20),
            trial_ends_at=now - timedelta(minutes=5),
        )
        payload = entitlement_payload(account)
        self.assertEqual(payload["status"], "payment_required")
        self.assertFalse(payload["has_access"])
        self.assertEqual(payload["seconds_remaining"], 0)

    def test_active_paid_period_grants_access_after_trial(self):
        now = datetime.now(timezone.utc)
        account = Account(
            clerk_user_id="user_paid", email="paid@example.com",
            created_at=now - timedelta(days=2),
            trial_started_at=now - timedelta(days=2),
            trial_ends_at=now - timedelta(days=2, minutes=-15),
            paid_until=now + timedelta(days=14),
        )
        payload = entitlement_payload(account)
        self.assertEqual(payload["status"], "subscribed")
        self.assertTrue(payload["has_access"])

    def test_suspension_overrides_admin_and_paid_access(self):
        now = datetime.now(timezone.utc)
        account = Account(
            clerk_user_id="user_suspended", email="suspended@example.com",
            created_at=now, trial_started_at=now,
            trial_ends_at=now + timedelta(minutes=15),
            paid_until=now + timedelta(days=14), suspended=True,
        )
        payload = entitlement_payload(account, is_admin=True)
        self.assertEqual(payload["status"], "suspended")
        self.assertFalse(payload["has_access"])


if __name__ == "__main__":
    unittest.main()
