"""In-app account and administrator dashboard for ManuCodeAI."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView,
)


class AccountDashboard(QWidget):
    refreshRequested = Signal()
    grantRequested = Signal(str, int)
    suspensionRequested = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _card(self, title: str):
        frame = QFrame()
        frame.setObjectName("surfaceCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        heading = QLabel(title.upper())
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)
        return frame, layout

    def _metric(self, title: str):
        card, layout = self._card(title)
        value = QLabel("—")
        value.setObjectName("metricValue")
        layout.addWidget(value)
        return card, value

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(14)

        heading = QHBoxLayout()
        title = QLabel("Account dashboard")
        title.setObjectName("pageTitle")
        heading.addWidget(title)
        heading.addStretch(1)
        refresh = QPushButton("Refresh dashboard")
        refresh.clicked.connect(self.refreshRequested.emit)
        heading.addWidget(refresh)
        root.addLayout(heading)
        subtitle = QLabel("Your ManuCodeAI access, subscription and account activity.")
        subtitle.setObjectName("mutedText")
        root.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        identity, identity_layout = self._card("Your account")
        self.email_value = QLabel("Not signed in")
        self.email_value.setObjectName("metricValue")
        self.email_value.setWordWrap(True)
        self.access_value = QLabel("Sign in to view your account dashboard.")
        self.access_value.setObjectName("mutedText")
        identity_layout.addWidget(self.email_value)
        identity_layout.addWidget(self.access_value)
        layout.addWidget(identity)

        metrics = QGridLayout()
        metrics.setSpacing(12)
        self.plan_card, self.plan_value = self._metric("Current plan")
        self.expiry_card, self.expiry_value = self._metric("Access expires")
        self.remaining_card, self.remaining_value = self._metric("Time remaining")
        metrics.addWidget(self.plan_card, 0, 0)
        metrics.addWidget(self.expiry_card, 0, 1)
        metrics.addWidget(self.remaining_card, 0, 2)
        layout.addLayout(metrics)

        payment_card, payment_layout = self._card("Your payment history")
        self.payment_table = QTableWidget(0, 4)
        self.payment_table.setHorizontalHeaderLabels(["Date", "Reference", "Amount", "Status"])
        self._prepare_table(self.payment_table)
        payment_layout.addWidget(self.payment_table)
        self.payment_empty = QLabel("Your payments will appear here after checkout.")
        self.payment_empty.setObjectName("mutedText")
        payment_layout.addWidget(self.payment_empty)
        layout.addWidget(payment_card)

        self.admin_panel, admin_layout = self._card("Administrator console")
        self.admin_panel.hide()
        self.admin_description = QLabel("Administrator tools are available to authorized accounts only.")
        self.admin_description.setObjectName("mutedText")
        admin_layout.addWidget(self.admin_description)

        stats = QGridLayout()
        self.admin_metrics = {}
        for index, key in enumerate(("total_users", "trial_users", "paying_users", "successful_payments", "revenue_kes")):
            labels = {
                "total_users": "Registered users",
                "trial_users": "Active trials",
                "paying_users": "Paying users",
                "successful_payments": "Successful payments",
                "revenue_kes": "Total revenue",
            }
            metric_card, metric_value = self._metric(labels[key])
            self.admin_metrics[key] = metric_value
            stats.addWidget(metric_card, index // 3, index % 3)
        admin_layout.addLayout(stats)

        config_card, config_layout = self._card("Paystack configuration")
        self.config_value = QLabel("Configuration will load when you refresh.")
        self.config_value.setObjectName("mutedText")
        self.config_value.setWordWrap(True)
        config_layout.addWidget(self.config_value)
        admin_layout.addWidget(config_card)

        users_card, users_layout = self._card("User management")
        self.users_table = QTableWidget(0, 5)
        self.users_table.setHorizontalHeaderLabels(["Email", "Status", "Trial ends", "Paid until", "Actions"])
        self._prepare_table(self.users_table)
        users_layout.addWidget(self.users_table)
        admin_layout.addWidget(users_card)

        admin_payments_card, admin_payments_layout = self._card("All payments")
        self.admin_payments_table = QTableWidget(0, 5)
        self.admin_payments_table.setHorizontalHeaderLabels(["Email", "Reference", "Amount", "Status", "Date"])
        self._prepare_table(self.admin_payments_table)
        admin_payments_layout.addWidget(self.admin_payments_table)
        admin_layout.addWidget(admin_payments_card)
        layout.addWidget(self.admin_panel)

        layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

    @staticmethod
    def _prepare_table(table: QTableWidget):
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(False)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(150)
        table.setWordWrap(False)

    @staticmethod
    def _put(table: QTableWidget, row: int, col: int, value):
        item = QTableWidgetItem("—" if value in (None, "") else str(value))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, col, item)

    def set_entitlement(self, data: dict):
        email = data.get("email") or "Signed in"
        self.email_value.setText(email)
        status = data.get("status", "unknown")
        self.access_value.setText({
            "trial": "Your free trial is active.",
            "subscribed": "Your subscription is active.",
            "administrator": "Administrator access is active.",
            "suspended": "This account is suspended. Contact support.",
            "payment_required": "Your free trial has ended. Purchase access to continue.",
        }.get(status, "Account access status: " + status))
        sub = data.get("subscription") or {}
        self.plan_value.setText(
            "Administrator" if status == "administrator" else
            ("KSh 250 / 14 days" if status == "subscribed" else
             ("Free trial" if status == "trial" else "No active plan"))
        )
        expiry = data.get("paid_until") if status == "subscribed" else data.get("trial_ends_at")
        self.expiry_value.setText((expiry or "—").replace("T", " ").split("+")[0][:19])
        seconds = data.get("seconds_remaining")
        if status == "administrator":
            remaining = "Unlimited"
        elif seconds is None:
            remaining = "—"
        else:
            remaining = f"{max(0, int(seconds) // 3600)}h {max(0, (int(seconds) % 3600) // 60)}m"
        self.remaining_value.setText(remaining)
        self.admin_panel.setVisible(bool(data.get("is_admin")))

    def apply_user_payments(self, rows: list[dict]):
        self.payment_table.setRowCount(len(rows))
        for row, payment in enumerate(rows):
            date = (payment.get("created_at") or "").replace("T", " ").split("+")[0][:19]
            self._put(self.payment_table, row, 0, date)
            self._put(self.payment_table, row, 1, payment.get("reference"))
            self._put(self.payment_table, row, 2, f"KSh {payment.get('amount_kes', 0):,.2f}")
            self._put(self.payment_table, row, 3, payment.get("status"))
        self.payment_empty.setVisible(not rows)

    def apply_admin_data(self, data: dict):
        overview = data.get("overview") or {}
        for key, label in self.admin_metrics.items():
            value = overview.get(key, 0)
            label.setText(f"KSh {value:,.2f}" if key == "revenue_kes" else f"{value:,}")

        config = data.get("config") or {}
        self.config_value.setText(
            f"Paystack: {'Configured' if config.get('configured') else 'Not configured'} "
            f"({config.get('mode', 'unknown')})  ·  "
            f"Callback: {'Ready' if config.get('callback_configured') else 'Missing'}  ·  "
            f"Plan: KSh {config.get('subscription_amount_kes', 250):,.0f} / "
            f"{config.get('subscription_days', 14)} days  ·  "
            f"Trial: {config.get('trial_minutes', 15)} minutes. "
            "Secret keys remain on the licensing server."
        )

        users = data.get("users") or []
        self.users_table.setRowCount(len(users))
        for row, user in enumerate(users):
            self._put(self.users_table, row, 0, user.get("email"))
            self._put(self.users_table, row, 1, user.get("status"))
            self._put(self.users_table, row, 2, (user.get("trial_ends_at") or "").replace("T", " ")[:16])
            self._put(self.users_table, row, 3, (user.get("paid_until") or "—").replace("T", " ")[:16])
            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(2, 2, 2, 2)
            grant = QPushButton("Grant 14 days")
            grant.clicked.connect(lambda checked=False, uid=user["user_id"]: self.grantRequested.emit(uid, 14))
            suspend = QPushButton("Restore" if user.get("suspended") else "Suspend")
            suspend.clicked.connect(lambda checked=False, uid=user["user_id"], value=not user.get("suspended"): self.suspensionRequested.emit(uid, value))
            action_layout.addWidget(grant)
            action_layout.addWidget(suspend)
            self.users_table.setCellWidget(row, 4, actions)

        payments = data.get("payments") or []
        self.admin_payments_table.setRowCount(len(payments))
        for row, payment in enumerate(payments):
            self._put(self.admin_payments_table, row, 0, payment.get("email"))
            self._put(self.admin_payments_table, row, 1, payment.get("reference"))
            self._put(self.admin_payments_table, row, 2, f"KSh {payment.get('amount_kes', 0):,.2f}")
            self._put(self.admin_payments_table, row, 3, payment.get("status"))
            self._put(self.admin_payments_table, row, 4, (payment.get("created_at") or "").replace("T", " ")[:16])
