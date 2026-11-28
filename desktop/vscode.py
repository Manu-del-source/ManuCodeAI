"""Open project files in VS Code and type generated code visibly."""
from __future__ import annotations

import ctypes
import shutil
import subprocess
import threading
import time
import os
import sys
from ctypes import wintypes
from pathlib import Path

from .typing_engine import create_typing_engine


class VSCodeError(RuntimeError):
    pass


class VSCodeController:
    def __init__(self):
        # Keep one typing engine for the session so its natural timing stays consistent.
        self.typing_engine = create_typing_engine(
            min_delay=0.07,
            max_delay=0.15,
            line_delay_min=0.35,
            line_delay_max=0.85,
        )

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
        subprocess.Popen(
            [self._code_command(), "--reuse-window", str(folder)], close_fds=True
        )

    @staticmethod
    def _focus_editor(filename: str, cancel_event: threading.Event | None = None) -> None:
        """Wait for the requested VS Code tab/window and explicitly focus it."""
        if sys.platform.startswith("linux"):
            target_name = Path(filename).name
            deadline = time.monotonic() + 20.0
            if not shutil.which("xdotool"):
                raise VSCodeError("Linux editor focus requires xdotool. Install it and retry.")
            while time.monotonic() < deadline:
                if cancel_event and cancel_event.is_set():
                    raise VSCodeError("Typing cancelled while waiting for VS Code.")
                try:
                    found = subprocess.run(
                        ["xdotool", "search", "--onlyvisible", "--name", "Visual Studio Code"],
                        capture_output=True, text=True, check=False,
                    )
                    window_ids = [item for item in found.stdout.splitlines() if item.strip()]
                    for window_id in reversed(window_ids):
                        title = subprocess.run(
                            ["xdotool", "getwindowname", window_id],
                            capture_output=True, text=True, check=False,
                        )
                        if title.returncode != 0 or target_name.lower() not in title.stdout.lower():
                            continue
                        activated = subprocess.run(
                            ["xdotool", "windowactivate", "--sync", window_id],
                            capture_output=True, text=True, check=False,
                        )
                        if activated.returncode == 0:
                            active = subprocess.run(
                                ["xdotool", "getactivewindow"],
                                capture_output=True, text=True, check=False,
                            )
                            if active.returncode == 0 and active.stdout.strip() == window_id:
                                time.sleep(0.25)
                                return
                except OSError:
                    pass
                time.sleep(0.25)
            session = os.environ.get("XDG_SESSION_TYPE", "unknown")
            hint = (
                "On Wayland, your compositor may block programmatic window activation. "
                "Bring the requested VS Code tab to the foreground and retry."
                if session.lower() == "wayland"
                else "Check that VS Code is open and the xdotool window search can see it."
            )
            raise VSCodeError(
                f"Could not focus {target_name} in VS Code. No keyboard input was sent. {hint}"
            )
        if not hasattr(ctypes, "windll"):
            raise VSCodeError("Visible editor typing is supported on Windows and Linux only.")
        user32 = ctypes.windll.user32
        target_name = Path(filename).name.lower()
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if cancel_event and cancel_event.is_set():
                raise VSCodeError("Typing cancelled while waiting for VS Code.")
            matches = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def enum_callback(hwnd, _):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                title = buffer.value.lower()
                if target_name in title and ("visual studio code" in title or " - code" in title):
                    matches.append(hwnd)
                return True

            user32.EnumWindows(enum_callback, 0)
            if matches:
                hwnd = matches[0]
                # Restore only when actually minimized; do not toggle the window state.
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.25)
                if user32.GetForegroundWindow() == hwnd:
                    return
            time.sleep(0.25)
        raise VSCodeError(
            f"Could not confirm that VS Code is focused on {filename}. "
            "No keyboard input was sent; retry after bringing the correct VS Code tab forward."
        )

    def write_file_visibly(
        self,
        workspace: Path,
        relative_path: str,
        content: str,
        cancel_event: threading.Event | None = None,
        progress_callback=None,
    ) -> Path:
        """Replace one editor document once, type progressively, save, and verify."""
        root = workspace.resolve()
        target = (root / relative_path).resolve()
        if target != root and root not in target.parents:
            raise VSCodeError("Refusing to write outside the selected project folder.")
        if target.name.lower() in {".env", ".env.local", ".env.production"}:
            raise VSCodeError("Refusing to edit a file that may contain secrets.")
        if target.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
            raise VSCodeError("Refusing to edit a file that may contain secrets.")
        if cancel_event and cancel_event.is_set():
            raise VSCodeError("Write cancelled before typing.")

        target.parent.mkdir(parents=True, exist_ok=True)
        # Create an empty file only when necessary; existing content is never appended.
        target.touch(exist_ok=True)
        subprocess.Popen(
            [self._code_command(), "--reuse-window", "--goto", f"{target}:1"],
            close_fds=True,
        )
        self._focus_editor(str(target), cancel_event=cancel_event)
        if cancel_event and cancel_event.is_set():
            raise VSCodeError("Typing cancelled before document replacement.")

        # This is deliberately performed exactly once. Ctrl+A followed by Backspace
        # ensures a retry starts from an empty document instead of appending a duplicate.
        self.typing_engine.replace_current_document()
        self.typing_engine.type_text(
            content,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )
        if cancel_event and cancel_event.is_set():
            raise VSCodeError(
                "Typing was interrupted. The file may be incomplete; it remains marked "
                "unfinished. Resume by reopening the file, clearing it once, and typing "
                "the complete intended content from the beginning."
            )
        self.typing_engine.save_document()
        time.sleep(0.35)
        if cancel_event and cancel_event.is_set():
            raise VSCodeError("Stop was requested after saving; the agent will verify on resume.")
        return target
