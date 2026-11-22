"""Launch VS Code and write approved code visibly into its editor."""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

from .typing_engine import WindowsTypingEngine


class VSCodeError(RuntimeError):
    pass


class VSCodeController:
    def __init__(self, typing_engine: WindowsTypingEngine | None = None):
        self.typing_engine = typing_engine or WindowsTypingEngine()
        self._launched = False

    @staticmethod
    def _code_command() -> str:
        command = shutil.which("code")
        if not command:
            raise VSCodeError(
                "The VS Code 'code' command was not found. Enable the shell command "
                "or add the VS Code bin directory to PATH."
            )
        return command

    def open_workspace(self, workspace: Path) -> None:
        folder = workspace.resolve()
        if not folder.is_dir():
            raise VSCodeError(f"Project folder does not exist: {folder}")
        subprocess.Popen([self._code_command(), str(folder)], close_fds=True)
        self._launched = True

    def write_file_visibly(
        self,
        workspace: Path,
        relative_path: str,
        content: str,
        cancel_event: threading.Event | None = None,
        progress_callback=None,
    ) -> Path:
        root = workspace.resolve()
        target = (root / relative_path).resolve()
        if target != root and root not in target.parents:
            raise VSCodeError("Refusing to write outside the selected project folder.")
        if target.suffix.lower() in {".env", ".pem", ".key", ".p12", ".pfx"}:
            raise VSCodeError("Refusing to edit a file that may contain secrets.")
        target.parent.mkdir(parents=True, exist_ok=True)
        existed = target.exists()
        if not existed:
            target.touch()

        code = self._code_command()
        subprocess.Popen(
            [code, "--reuse-window", "--goto", f"{target}:1"],
            close_fds=True,
        )
        # Allow VS Code to open the document and take focus before sending keys.
        time.sleep(1.8)

        if cancel_event and cancel_event.is_set():
            return target

        if existed:
            self.typing_engine.replace_current_document()

        self.typing_engine.type_text(
            content,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )
        self.typing_engine.save_document()
        return target
