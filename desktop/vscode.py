"""Open the project and display files saved atomically by the coding agent."""
from __future__ import annotations

import os
import shutil
import tempfile
import subprocess
from pathlib import Path


class VSCodeError(RuntimeError):
    pass


class VSCodeController:
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
