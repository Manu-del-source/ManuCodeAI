"""Windows release updater for the ManuCodeAI desktop application."""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QThread, Signal


REPOSITORY = "Manu-del-source/ManuCodeAI"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
USER_AGENT = "ManuCodeAI-Updater"


def _request(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.match(r"^v?(\d+(?:\.\d+)*)", value.strip())
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


class ReleaseCheckWorker(QThread):
    available = Signal(object)
    up_to_date = Signal()
    failed = Signal(str)

    def __init__(self, current_version: str):
        super().__init__()
        self.current_version = current_version

    def run(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import json

            release = json.loads(_request(API_URL).decode("utf-8"))
            latest = str(release.get("tag_name", "")).strip()
            current_tuple = _version_tuple(self.current_version)
            latest_tuple = _version_tuple(latest)
            if not latest_tuple:
                raise RuntimeError("GitHub returned a release without a valid version tag.")
            if current_tuple and latest_tuple <= current_tuple:
                self.up_to_date.emit()
                return

            assets = {
                item.get("name"): item.get("browser_download_url")
                for item in release.get("assets", [])
            }
            installer_url = assets.get("ManuCodeAI-Setup.exe")
            checksum_url = assets.get("SHA256SUMS.txt")
            if not installer_url or not checksum_url:
                raise RuntimeError(
                    "The latest release does not contain the Windows installer and checksum file."
                )
            self.available.emit({
                "version": latest,
                "notes": str(release.get("body") or "").strip(),
                "installer_url": installer_url,
                "checksum_url": checksum_url,
                "html_url": str(release.get("html_url") or ""),
            })
        except Exception as error:
            self.failed.emit(str(error))


class InstallerDownloadWorker(QThread):
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, release: dict):
        super().__init__()
        self.release = release

    def run(self) -> None:
        try:
            checksum_text = _request(self.release["checksum_url"]).decode("utf-8")
            expected = None
            for line in checksum_text.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[-1].lstrip("*") == "ManuCodeAI-Setup.exe":
                    expected = parts[0].lower()
                    break
            if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise RuntimeError("The release does not contain a valid installer SHA-256 checksum.")

            installer_data = _request(self.release["installer_url"], timeout=180)
            actual = hashlib.sha256(installer_data).hexdigest()
            if actual != expected:
                raise RuntimeError("The downloaded installer failed SHA-256 verification.")

            target_dir = Path(tempfile.gettempdir()) / "ManuCodeAI-Updates"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"ManuCodeAI-Setup-{self.release['version']}.exe"
            target.write_bytes(installer_data)
            self.completed.emit(str(target))
        except (OSError, urllib.error.URLError, UnicodeError, RuntimeError) as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit(f"Could not download the update: {error}")


def launch_windows_installer(installer_path: str) -> None:
    """Hand off installation to a detached script, then restart the installed app."""
    if sys.platform != "win32":
        raise RuntimeError("Automatic installer updates are currently supported on Windows only.")
    import subprocess

    installer = Path(installer_path).resolve()
    app_exe = (Path(sys.executable).resolve().parent / "ManuCodeAI.exe")
    if not installer.is_file() or not app_exe.is_file():
        raise RuntimeError("Could not locate the downloaded installer or installed ManuCodeAI executable.")

    script = Path(tempfile.gettempdir()) / "ManuCodeAI-apply-update.cmd"
    script.write_text(
        "@echo off\r\n"
        "timeout /t 3 /nobreak >nul\r\n"
        f'start /wait "" "{installer}" /SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS\r\n'
        f'if exist "{app_exe}" start "" "{app_exe}"\r\n',
        encoding="utf-8",
    )
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
    )
    subprocess.Popen(
        ["cmd.exe", "/c", str(script)],
        creationflags=flags,
        close_fds=True,
        cwd=str(app_exe.parent),
    )
