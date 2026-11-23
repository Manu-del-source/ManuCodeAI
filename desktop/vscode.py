"""Launch VS Code and write approved code visibly into its editor."""
from __future__ import annotations

import ctypes
import os
import shutil
import tempfile
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path

from .typing_engine import WindowsTypingEngine


class VSCodeError(RuntimeError):
    pass


class VSCodeController:
    def __init__(self, typing_engine: WindowsTypingEngine | None = None):
        self.typing_engine = typing_engine or WindowsTypingEngine()
        self._window_handle = None

    @staticmethod
    def _code_command() -> str:
        command = shutil.which("code")
        if not command:
            raise VSCodeError(
                "The VS Code 'code' command was not found. Add the VS Code bin "
                "directory to PATH or enable the shell command."
            )
        return command

    def open_workspace(self, workspace: Path) -> None:
        folder = workspace.resolve()
        if not folder.is_dir():
            raise VSCodeError(f"Project folder does not exist: {folder}")
        # Reuse one VS Code window for the entire project session.
        subprocess.Popen(
            [self._code_command(), "--reuse-window", str(folder)], close_fds=True
        )

    def _focus_editor(self, filename: str, timeout: float = 8.0) -> None:
        """Bring the VS Code window displaying filename to the foreground."""
        if not hasattr(ctypes, "windll"):
            raise VSCodeError("Window activation is available on Windows only.")

        user32 = ctypes.windll.user32
        deadline = time.monotonic() + timeout
        matched = wintypes.HWND()

        # Prefer the window already associated with this controller. Its title
        # changes as files change, so validate the handle rather than reopening
        # or creating another VS Code window for every file.
        if self._window_handle and user32.IsWindow(self._window_handle):
            length = user32.GetWindowTextLengthW(self._window_handle)
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(self._window_handle, title, length + 1)
            if (
                "visual studio code" in title.value.lower()
                and filename.lower() in title.value.lower()
            ):
                matched.value = self._window_handle

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(hwnd, _lparam):
            if matched.value or not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if not length:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            text = title.value.lower()
            if "visual studio code" in text and filename.lower() in text:
                matched.value = hwnd
                return False
            return True

        while time.monotonic() < deadline:
            if not matched.value:
                user32.EnumWindows(visit, 0)
            if matched.value:
                self._window_handle = matched.value
                # Restore only if actually minimized. Do not change a normal
                # or maximized window's state while switching between files.
                if user32.IsIconic(matched):
                    user32.ShowWindow(matched, 9)  # SW_RESTORE
                if not user32.SetForegroundWindow(matched):
                    raise VSCodeError(
                        "Windows did not allow ManuCodeAI to focus VS Code. "
                        "Bring the intended VS Code window to the foreground and retry."
                    )
                time.sleep(0.25)
                if user32.GetForegroundWindow() != matched.value:
                    raise VSCodeError(
                        "VS Code did not become the foreground window. "
                        "No code was typed to avoid sending keystrokes to another app."
                    )
                return
            time.sleep(0.2)
        raise VSCodeError(
            f"Could not find the VS Code editor for {filename}. "
            "Make sure VS Code opened the file before typing."
        )

    def write_file_and_open(self, workspace: Path, relative_path: str, content: str,
                            cancel_event: threading.Event | None = None,
                            progress_callback=None) -> Path:
        """Atomically save exact UTF-8 content, then open the saved file in VS Code.

        VS Code is deliberately not used as the file-writing transport. This avoids
        focus errors, editor auto-formatting, clipboard races, partial keyboard
        injection, and duplicated content after interrupted typing.
        """
        root = workspace.resolve()
        target = (root / relative_path).resolve()
        if target != root and root not in target.parents:
            raise VSCodeError("Refusing to write outside the selected project folder.")
        if target.name.lower() in {".env", ".env.local", ".env.production"}:
            raise VSCodeError("Refusing to edit a file that may contain secrets.")
        if target.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
            raise VSCodeError("Refusing to edit a file that may contain secrets.")
        if cancel_event and cancel_event.is_set():
            raise VSCodeError("Write cancelled before starting.")

        target.parent.mkdir(parents=True, exist_ok=True)
        previous_mode = None
        if target.exists():
            previous_mode = target.stat().st_mode

        temporary_path = None
        try:
            # The temporary file lives beside the destination so os.replace is
            # atomic on the same filesystem. newline="" preserves exact line endings.
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".manucodeai-tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())

            if cancel_event and cancel_event.is_set():
                raise VSCodeError("Write cancelled before the atomic replace.")

            if previous_mode is not None:
                os.chmod(temporary_path, previous_mode)

            # One atomic replacement: never append and never expose a half-written file.
            os.replace(temporary_path, target)
            temporary_path = None

            if cancel_event and cancel_event.is_set():
                raise VSCodeError(
                    "Stop was requested after the atomic save. The complete file is saved; "
                    "verification will be performed when the task resumes."
                )

            # Open the actual saved file for visibility. No simulated typing,
            # select-all, clipboard mutation, or save shortcut is involved.
            subprocess.Popen(
                [self._code_command(), "--reuse-window", "--goto", f"{target}:1"],
                close_fds=True,
            )
            return target
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
