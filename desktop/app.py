"""Cross-platform standalone ManuCodeAI desktop application."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QSettings, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from .agent import CodingAgent, discover_models
from .vscode import VSCodeController
from .typing_engine import KeyboardKillSwitch


APP_ORG = "ManuCodeAI"
APP_NAME = "ManuCodeAI Desktop"
PROVIDERS = {
    "AgentRouter": {
        "base_url": "https://agentrouter.org/v1",
        "protocol": "openai",
        "default_model": "",
    },
    "Coarena": {
        "base_url": "https://coarena.ai/api",
        "protocol": "anthropic",
        "default_model": "",
    },
    "Gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "protocol": "openai",
        "default_model": "gemini-3.8-flash",
    },
    "OpenRouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "protocol": "openai",
        "default_model": "openrouter/free",
    },
}


class AgentWorker(QThread):
    status = Signal(str)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, provider: str, api_key: str, base_url: str, model: str,
                 workspace: Path, prompt: str):
        super().__init__()
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.workspace = workspace
        self.prompt = prompt
        self.cancel_event = threading.Event()

    def run(self) -> None:
        kill_switch = KeyboardKillSwitch(self.cancel_event, self.status.emit)
        try:
            if kill_switch.start():
                self.status.emit("Emergency stop active: press Ctrl+Shift+X at any time.")
            else:
                self.status.emit(
                    f"Keyboard emergency stop unavailable: {kill_switch.error}. "
                    "The Stop button remains available."
                )
            self.status.emit("Opening project in Visual Studio Code...")
            vscode = VSCodeController()
            vscode.open_workspace(self.workspace)
            agent = CodingAgent(
                provider=self.provider,
                api_key=self.api_key,
                base_url=self.base_url,
                model=self.model,
                workspace=self.workspace,
                vscode=vscode,
                status=self.status.emit,
                cancel_event=self.cancel_event,
            )
            self.completed.emit(agent.run(self.prompt))
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            kill_switch.stop()


class MainWindow(QMainWindow):
    """Main desktop shell. Existing coding workflow remains on the Workspace page."""

    NAV_ITEMS = ("Overview", "Workspace", "Activity", "Account")

    def __init__(self):
        super().__init__()
        self.settings = QSettings(APP_ORG, APP_NAME)
        self.worker: AgentWorker | None = None
        self.workspace: Path | None = None

        self.setWindowTitle("ManuCodeAI")
        self.resize(1180, 790)
        self.setMinimumSize(900, 650)

        self._build_provider_controls()
        self._build_shell()
        self.provider.currentTextChanged.connect(self._provider_changed)
        self._load_saved_key()
        self._apply_theme()
        self._refresh_workspace_label()

    def _build_provider_controls(self) -> None:
        self.provider = QComboBox()
        self.provider.addItems(PROVIDERS.keys())
        saved_provider = self.settings.value("provider", "AgentRouter")
        self.provider.setCurrentText(saved_provider if saved_provider in PROVIDERS else "AgentRouter")
        self._active_provider = self.provider.currentText()
        config = PROVIDERS[self._active_provider]

        self.base_url = QLineEdit(
            self.settings.value(f"{self._active_provider}/base_url", config["base_url"])
        )
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model.setCurrentText(
            self.settings.value(f"{self._active_provider}/model", config.get("default_model", ""))
        )
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Enter your provider API key")

        self.fetch_models_button = QPushButton("Fetch models")
        self.fetch_models_button.clicked.connect(self.fetch_models)
        self.start_button = QPushButton("Start coding")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self.start_coding)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_coding)

        self.project_label = QLabel("No project selected")
        self.project_label.setObjectName("mutedText")
        self.project_label.setWordWrap(True)
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText(
            "Describe what you want ManuCodeAI to build, change, debug, or review..."
        )
        self.prompt.setMinimumHeight(170)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setPlaceholderText("Your coding session activity will appear here.")

    def _build_shell(self) -> None:
        shell = QWidget()
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(218)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(18, 24, 18, 20)
        side_layout.setSpacing(12)

        brand = QLabel("MANU<span style='color:#D4A85C'>CODE</span>AI")
        brand.setObjectName("brand")
        side_layout.addWidget(brand)
        side_layout.addWidget(self._label("DESKTOP AGENT", "eyebrow"))
        side_layout.addSpacing(18)

        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        for name in self.NAV_ITEMS:
            item = QListWidgetItem(name)
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
            self.navigation.addItem(item)
        self.navigation.setCurrentRow(0)
        self.navigation.currentRowChanged.connect(self._navigate)
        side_layout.addWidget(self.navigation, 1)

        side_footer = QLabel("Visible coding. Full control.")
        side_footer.setObjectName("mutedText")
        side_layout.addWidget(side_footer)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(30, 24, 30, 24)
        content_layout.setSpacing(18)

        topbar = QHBoxLayout()
        topbar.addWidget(self._label("YOUR AI DEVELOPMENT WORKSPACE", "eyebrow"))
        topbar.addStretch(1)
        self.session_status = QLabel("●  READY")
        self.session_status.setObjectName("statusPill")
        topbar.addWidget(self.session_status)
        content_layout.addLayout(topbar)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._overview_page())
        self.pages.addWidget(self._workspace_page())
        self.pages.addWidget(self._activity_page())
        self.pages.addWidget(self._account_page())
        content_layout.addWidget(self.pages, 1)

        shell_layout.addWidget(sidebar)
        shell_layout.addWidget(content, 1)
        self.setCentralWidget(shell)

    def _label(self, text: str, name: str | None = None) -> QLabel:
        label = QLabel(text)
        if name:
            label.setObjectName(name)
        return label

    def _card(self, title: str, description: str | None = None) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("surfaceCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        heading = self._label(title, "cardTitle")
        layout.addWidget(heading)
        if description:
            layout.addWidget(self._label(description, "mutedText"))
        return card, layout

    def _overview_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(18)

        layout.addWidget(self._label("Build with an AI coding agent.", "pageTitle"))
        layout.addWidget(self._label(
            "Choose a project, describe the work, and watch ManuCodeAI make changes in VS Code.",
            "mutedText",
        ))

        cards = QHBoxLayout()
        cards.setSpacing(14)
        project_card, project_layout = self._card("CURRENT PROJECT")
        self.overview_project = self._label("No project selected", "metricValue")
        self.overview_project.setWordWrap(True)
        project_layout.addWidget(self.overview_project)
        project_layout.addWidget(self._label("Your selected local workspace", "mutedText"))
        cards.addWidget(project_card, 1)

        provider_card, provider_layout = self._card("AI PROVIDER")
        self.overview_provider = self._label(self.provider.currentText(), "metricValue")
        provider_layout.addWidget(self.overview_provider)
        provider_layout.addWidget(self._label("Configured in Workspace settings", "mutedText"))
        cards.addWidget(provider_card, 1)
        layout.addLayout(cards)

        action_card, action_layout = self._card(
            "Start a coding session",
            "ManuCodeAI works inside your selected project and uses VS Code as the visible editor.",
        )
        actions = QHBoxLayout()
        open_workspace = QPushButton("Open workspace")
        open_workspace.setObjectName("primaryButton")
        open_workspace.clicked.connect(lambda: self.navigation.setCurrentRow(1))
        actions.addWidget(open_workspace)
        actions.addStretch(1)
        action_layout.addLayout(actions)
        layout.addWidget(action_card)

        note_card, note_layout = self._card("YOUR WORKFLOW")
        note_layout.addWidget(self._label(
            "1. Configure your AI provider and model.\n"
            "2. Select the project folder.\n"
            "3. Describe the task and start coding.\n"
            "4. Review the changes and test your project.",
            "bodyText",
        ))
        layout.addWidget(note_card)
        layout.addStretch(1)
        return page

    def _workspace_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._label("Workspace", "pageTitle"))
        layout.addWidget(self._label(
            "Configure the model and describe the work you want the agent to perform.",
            "mutedText",
        ))

        provider_card, provider_layout = self._card("AI PROVIDER CONFIGURATION")
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(13)
        form.addRow("Provider", self.provider)
        form.addRow("API base URL", self.base_url)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        model_row.addWidget(self.fetch_models_button)
        form.addRow("Model", model_row)
        form.addRow("Provider API key", self.api_key)
        provider_layout.addLayout(form)
        layout.addWidget(provider_card)

        project_card, project_layout = self._card("PROJECT FOLDER")
        project_row = QHBoxLayout()
        choose = QPushButton("Choose folder")
        choose.clicked.connect(self.choose_project)
        project_row.addWidget(choose)
        project_row.addWidget(self.project_label, 1)
        project_layout.addLayout(project_row)
        layout.addWidget(project_card)

        task_card, task_layout = self._card("TASK INSTRUCTIONS")
        task_layout.addWidget(self.prompt)
        action_row = QHBoxLayout()
        action_row.addWidget(self.stop_button)
        action_row.addStretch(1)
        action_row.addWidget(self.start_button)
        task_layout.addLayout(action_row)
        layout.addWidget(task_card, 1)
        return page

    def _activity_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._label("Activity", "pageTitle"))
        layout.addWidget(self._label(
            "Live status messages and results from your coding sessions.",
            "mutedText",
        ))
        card, card_layout = self._card("SESSION LOG")
        card_layout.addWidget(self.activity, 1)
        layout.addWidget(card, 1)
        return page

    def _account_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._label("Account & access", "pageTitle"))
        layout.addWidget(self._label(
            "Your sign-in, free trial, and subscription controls will appear here.",
            "mutedText",
        ))

        auth_card, auth_layout = self._card("MANUCODEAI ACCOUNT")
        auth_layout.addWidget(self._label(
            "Authentication is being connected to Clerk. Account status will be verified online.",
            "bodyText",
        ))
        layout.addWidget(auth_card)

        plan_card, plan_layout = self._card("SUBSCRIPTION")
        price = self._label("KSh 250  /  14 days", "metricValue")
        plan_layout.addWidget(price)
        plan_layout.addWidget(self._label(
            "15-minute free trial. After the trial, purchase is required to continue coding. "
            "Payments will be processed securely through Paystack.",
            "mutedText",
        ))
        self.account_status = self._label("Billing is not connected yet.", "warningText")
        plan_layout.addWidget(self.account_status)
        layout.addWidget(plan_card)
        layout.addStretch(1)
        return page

    def _navigate(self, index: int) -> None:
        if 0 <= index < self.pages.count():
            self.pages.setCurrentIndex(index)

    def _refresh_workspace_label(self) -> None:
        if self.workspace:
            value = str(self.workspace)
            self.project_label.setText(value)
            self.overview_project.setText(self.workspace.name)
        else:
            self.overview_project.setText("No project selected")

    def _apply_theme(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #0D0F12; color: #E8E9EC; }
            QFrame#sidebar { background: #090A0C; border-right: 1px solid #24272D; }
            QLabel#brand { color: #F3F0E8; font-size: 21px; font-weight: 800; padding: 5px 2px; }
            QLabel#eyebrow { color: #9298A3; font-size: 10px; font-weight: 700; letter-spacing: 1px; }
            QLabel#pageTitle { color: #F4F1E9; font-size: 27px; font-weight: 700; padding: 2px 0; }
            QLabel#cardTitle { color: #D6B477; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
            QLabel#metricValue { color: #F4F1E9; font-size: 22px; font-weight: 700; }
            QLabel#mutedText { color: #9298A3; font-size: 12px; }
            QLabel#bodyText { color: #D3D5DA; font-size: 13px; line-height: 1.5; }
            QLabel#statusPill { color: #D7BC83; background: #292216; border: 1px solid #594629;
                border-radius: 12px; padding: 7px 11px; font-size: 10px; font-weight: 700; }
            QLabel#warningText { color: #D7BC83; font-size: 12px; }
            QFrame#surfaceCard { background: #15181D; border: 1px solid #292D35; border-radius: 12px; }
            QListWidget#navigation { background: transparent; border: 0; outline: 0; }
            QListWidget#navigation::item { color: #9CA2AD; padding: 13px 12px; border-radius: 7px; margin: 2px 0; }
            QListWidget#navigation::item:selected { color: #F1D49A; background: #292216; border-left: 3px solid #C49A52; }
            QLineEdit, QComboBox, QPlainTextEdit {
                background: #0F1216; color: #F0F1F3; border: 1px solid #343943;
                border-radius: 7px; padding: 9px; selection-background-color: #765A2E;
            }
            QPlainTextEdit { padding: 12px; }
            QPushButton { background: #292D34; color: #E8E9EC; border: 1px solid #3B404A;
                border-radius: 7px; padding: 10px 15px; font-weight: 600; }
            QPushButton:hover { background: #363B44; }
            QPushButton#primaryButton { background: #C49A52; color: #101114; border: 0; }
            QPushButton#primaryButton:hover { background: #D5AE6A; }
            QPushButton#dangerButton { background: #3A2022; color: #F0B8B8; border: 1px solid #6B3438; }
            QPushButton:disabled { background: #282B30; color: #737983; border-color: #30343B; }
            QComboBox QAbstractItemView { background: #171A20; color: #F0F1F3; selection-background-color: #765A2E; }
        """)

    def _keyring_name(self, provider: str | None = None) -> str:
        return f"{(provider or self.provider.currentText()).lower()}-api-key"

    def _load_saved_key(self) -> None:
        try:
            import keyring
            saved = keyring.get_password(APP_ORG, self._keyring_name())
            if saved:
                self.api_key.setText(saved)
        except Exception:
            self._log("Secure credential storage is unavailable; the API key will not be restored.")

    def _provider_changed(self, provider: str) -> None:
        previous = self._active_provider
        self.settings.setValue(f"{previous}/base_url", self.base_url.text().strip())
        self.settings.setValue(f"{previous}/model", self.model.currentText().strip())
        try:
            import keyring
            keyring.set_password(APP_ORG, self._keyring_name(previous), self.api_key.text().strip())
            saved = keyring.get_password(APP_ORG, self._keyring_name(provider))
        except Exception:
            saved = None
        self._active_provider = provider
        self.api_key.setText(saved or "")
        self.base_url.setText(
            self.settings.value(f"{provider}/base_url", PROVIDERS[provider]["base_url"])
        )
        self.model.clear()
        self.model.setCurrentText(
            self.settings.value(
                f"{provider}/model", PROVIDERS[provider].get("default_model", "")
            )
        )
        self.overview_provider.setText(provider)

    def _save_settings(self) -> None:
        current_provider = self.provider.currentText()
        self.settings.setValue("provider", current_provider)
        self.settings.setValue(f"{current_provider}/base_url", self.base_url.text().strip())
        self.settings.setValue(f"{current_provider}/model", self.model.currentText().strip())
        try:
            import keyring
            keyring.set_password(APP_ORG, self._keyring_name(), self.api_key.text().strip())
        except Exception as error:
            self._log(f"Could not save API key to the system credential store: {error}")

    def _log(self, message: str) -> None:
        self.activity.appendPlainText(message)

    def choose_project(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select project folder")
        if selected:
            self.workspace = Path(selected).resolve()
            self._refresh_workspace_label()
            self._log(f"Project selected: {self.workspace}")

    def fetch_models(self) -> None:
        provider = self.provider.currentText()
        if provider == "Coarena":
            QMessageBox.information(
                self, "Model selection",
                "Coarena model discovery is not enabled. Enter a model ID supplied "
                "by your Coarena account.",
            )
            return
        key = self.api_key.text().strip()
        if not key:
            QMessageBox.warning(self, "API key required", f"Enter your {provider} API key first.")
            return
        self.fetch_models_button.setEnabled(False)
        self._log(f"Fetching available {provider} models...")
        try:
            models = discover_models(key, self.base_url.text().strip(), provider)
            current = self.model.currentText().strip()
            self.model.clear()
            self.model.addItems(models)
            if current:
                self.model.setCurrentText(current)
            elif models:
                self.model.setCurrentIndex(0)
            self._log(f"Loaded {len(models)} model(s) from {provider}.")
            if not models:
                self._log("No compatible models were returned. Check API access and provider model availability.")
        except Exception as error:
            QMessageBox.critical(self, "Model discovery failed", str(error))
            self._log(f"Model discovery failed: {error}")
        finally:
            self.fetch_models_button.setEnabled(True)

    def start_coding(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        api_key = self.api_key.text().strip()
        model = self.model.currentText().strip()
        instruction = self.prompt.toPlainText().strip()
        if not api_key:
            QMessageBox.warning(self, "API key required", "Enter your AI provider API key.")
            return
        if not model:
            QMessageBox.warning(self, "Model required", "Enter or select a model identifier.")
            return
        if not self.workspace or not self.workspace.is_dir():
            QMessageBox.warning(self, "Project required", "Select a valid project folder first.")
            return
        if not instruction:
            QMessageBox.warning(self, "Instruction required", "Describe the coding task first.")
            return

        self._save_settings()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.session_status.setText("●  RUNNING")
        self.navigation.setCurrentRow(2)
        self._log(f"\n--- New coding session ({self.provider.currentText()}) ---")
        self.worker = AgentWorker(
            self.provider.currentText(), api_key, self.base_url.text().strip(),
            model, self.workspace, instruction,
        )
        self.worker.status.connect(self._log)
        self.worker.completed.connect(self._on_completed)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_completed(self, message: str) -> None:
        self._log("\n" + message)
        self._log("--- Coding session finished ---")
        self.session_status.setText("●  READY")
        self._set_idle()

    def _on_failed(self, message: str) -> None:
        self._log(f"Session stopped: {message}")
        self.session_status.setText("●  READY")
        self._set_idle()

    def _set_idle(self) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def stop_coding(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel_event.set()
            self._log("Stop requested. Waiting for the current operation to stop.")

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel_event.set()
            self.worker.wait(1500)
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_ORG)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
