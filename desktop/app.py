"""Standalone ManuCodeAI desktop application (Windows)."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QSettings, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .agent import CodingAgent
from .vscode import VSCodeController


APP_ORG = "ManuCodeAI"
APP_NAME = "ManuCodeAI Desktop"


class AgentWorker(QThread):
    status = Signal(str)
    log = Signal(str)
    approval_requested = Signal(str, str, object)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, api_key: str, base_url: str, model: str, workspace: Path, prompt: str):
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.workspace = workspace
        self.prompt = prompt
        self.cancel_event = threading.Event()

    def request_approval(self, title: str, detail: str) -> bool:
        decision: dict[str, bool] = {}
        gate = threading.Event()
        self.approval_requested.emit(title, detail, (gate, decision))
        while not gate.wait(0.1):
            if self.cancel_event.is_set():
                return False
        return decision.get("approved", False)

    def run(self) -> None:
        try:
            self.status.emit("Opening project in Visual Studio Code...")
            vscode = VSCodeController()
            vscode.open_workspace(self.workspace)
            agent = CodingAgent(
                api_key=self.api_key,
                base_url=self.base_url,
                model=self.model,
                workspace=self.workspace,
                vscode=vscode,
                approve=self.request_approval,
                status=self.status.emit,
                cancel_event=self.cancel_event,
            )
            result = agent.run(self.prompt)
            self.completed.emit(result)
        except Exception as error:
            self.failed.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings(APP_ORG, APP_NAME)
        self.worker: AgentWorker | None = None
        self.workspace: Path | None = None

        self.setWindowTitle("ManuCodeAI")
        self.resize(980, 720)
        self.setMinimumSize(760, 560)

        self.base_url = QLineEdit(self.settings.value("base_url", "https://api.openai.com/v1"))
        self.model = QLineEdit(self.settings.value("model", "gpt-4o-mini"))
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Paste your provider API key")
        self.project_label = QLabel("No project folder selected")
        self.project_label.setWordWrap(True)
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText(
            "Describe what you want ManuCodeAI to build, change, or fix..."
        )
        self.prompt.setMinimumHeight(150)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setPlaceholderText("Coding activity will appear here.")

        choose_button = QPushButton("Open project folder")
        choose_button.clicked.connect(self.choose_project)
        self.start_button = QPushButton("Start coding")
        self.start_button.clicked.connect(self.start_coding)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_coding)

        form = QFormLayout()
        form.addRow("AI provider base URL", self.base_url)
        form.addRow("Model", self.model)
        form.addRow("API key", self.api_key)

        project_row = QHBoxLayout()
        project_row.addWidget(choose_button)
        project_row.addWidget(self.project_label, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.start_button)

        root = QVBoxLayout()
        root.addWidget(QLabel("MANUCODEAI  /  STANDALONE CODING AGENT"))
        root.addLayout(form)
        root.addLayout(project_row)
        root.addWidget(QLabel("Instruction"))
        root.addWidget(self.prompt)
        root.addLayout(actions)
        root.addWidget(QLabel("Activity"))
        root.addWidget(self.activity, 1)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)
        self._load_saved_key()
        self._apply_theme()

    def _apply_theme(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #101114; color: #e8e8ea; }
            QLineEdit, QPlainTextEdit {
                background: #191b20; color: #f2f2f3; border: 1px solid #343740;
                border-radius: 7px; padding: 9px; selection-background-color: #8c6b32;
            }
            QPushButton {
                background: #b58b45; color: #111111; border: 0;
                border-radius: 7px; padding: 10px 16px; font-weight: 600;
            }
            QPushButton:disabled { background: #45464b; color: #999999; }
            QLabel { color: #e5e5e7; padding: 3px; }
        """)

    def _load_saved_key(self) -> None:
        try:
            import keyring
            saved = keyring.get_password(APP_ORG, "provider-api-key")
            if saved:
                self.api_key.setText(saved)
        except Exception:
            self._log("Secure credential storage is unavailable; the API key will not be restored.")

    def _save_settings(self) -> None:
        self.settings.setValue("base_url", self.base_url.text().strip())
        self.settings.setValue("model", self.model.text().strip())
        try:
            import keyring
            keyring.set_password(APP_ORG, "provider-api-key", self.api_key.text().strip())
        except Exception as error:
            self._log(f"Could not save API key to Windows Credential Manager: {error}")

    def _log(self, message: str) -> None:
        self.activity.appendPlainText(message)

    def choose_project(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select project folder")
        if selected:
            self.workspace = Path(selected).resolve()
            self.project_label.setText(str(self.workspace))
            self._log(f"Project selected: {self.workspace}")

    def start_coding(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        api_key = self.api_key.text().strip()
        model = self.model.text().strip()
        instruction = self.prompt.toPlainText().strip()
        if not api_key:
            QMessageBox.warning(self, "API key required", "Enter your AI provider API key.")
            return
        if not model:
            QMessageBox.warning(self, "Model required", "Enter a model identifier.")
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
        self._log("\n--- New coding session ---")
        self.worker = AgentWorker(
            api_key,
            self.base_url.text().strip(),
            model,
            self.workspace,
            instruction,
        )
        self.worker.status.connect(self._log)
        self.worker.completed.connect(self._on_completed)
        self.worker.failed.connect(self._on_failed)
        self.worker.approval_requested.connect(self._on_approval_requested)
        self.worker.start()

    def _on_approval_requested(self, title: str, detail: str, payload: object) -> None:
        gate, decision = payload
        answer = QMessageBox.question(
            self,
            title,
            detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        decision["approved"] = answer == QMessageBox.StandardButton.Yes
        gate.set()

    def _on_completed(self, message: str) -> None:
        self._log("\n" + message)
        self._log("--- Coding session finished ---")
        self._set_idle()

    def _on_failed(self, message: str) -> None:
        self._log(f"Session stopped: {message}")
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
    if sys.platform != "win32":
        print("The current ManuCodeAI desktop build targets Windows.")
        return 1
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_ORG)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
