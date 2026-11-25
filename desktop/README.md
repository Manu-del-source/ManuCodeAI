# ManuCodeAI Desktop

This directory contains the standalone Windows and Linux desktop application. It is the primary coding workflow; it does not require or use a VS Code extension.

## Current capabilities

- PySide6 desktop interface.
- AgentRouter, Gemini, OpenRouter, and custom OpenAI-compatible endpoints.
- Coarena through the Anthropic Messages API.
- Provider model discovery where supported and manual model ID entry.
- OS credential-store integration for provider API keys.
- Project-folder selection and task input.
- Project inspection, ordered file plans, new-file creation, and targeted edits.
- Visible progressive typing in VS Code, save, and read-back verification.
- Persistent progress checkpoints and file manifests.
- Bounded retries for transient provider failures.
- Stop button and Windows Ctrl+Shift+X emergency stop.

## Install

From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r desktop\requirements.txt
python run_desktop.py
```

On Linux, install `xdotool` and a clipboard utility first, then use the equivalent `python3.11 -m venv`, `source .venv/bin/activate`, and `pip install` commands. Fedora KDE/X11 users can install `xdotool` and `xclip`; Wayland users need `xdotool` and `wl-clipboard`, and must run VS Code in a window that the current focus backend can detect.

## Providers

| Provider | Protocol | Discovery |
| --- | --- | --- |
| AgentRouter | OpenAI-compatible | Automatic |
| Gemini | OpenAI-compatible | Gemini API model list |
| OpenRouter | OpenAI-compatible | Automatic |
| Coarena | Anthropic Messages | Manual model ID |

The API key is saved to the operating system credential store when keyring is available. Provider settings such as base URL and model ID are stored through QSettings. Do not put API keys in project files.

## Task recovery

The selected project stores `.manucodeai/progress.json` and `.manucodeai/file_manifest.json`. These record the task checkpoint, ordered file plan, and verification state. On restart, the agent reconciles saved file hashes and resumes unfinished work rather than blindly repeating successful writes.

Set `MANUCODE_MAX_ROUNDS` before launching to change the default 60-round safety limit (minimum 12).

## Tests

From the repository root:

```powershell
python -m compileall -q desktop
python -m unittest discover -s tests -v
```

## Packaging

Release builds are produced by GitHub Actions on Windows and Linux runners. To build locally, install the root and desktop dependencies, then install PyInstaller:

```bash
python -m pip install -r requirements.txt -r desktop/requirements.txt pyinstaller
pyinstaller --noconfirm --clean --windowed --onedir --name ManuCodeAI --collect-all PySide6 --collect-all shiboken6 --collect-all keyring run_desktop.py
```

The PyInstaller output is in `dist/ManuCodeAI/`.

### Windows

The release workflow wraps the PyInstaller folder in an Inno Setup installer named `ManuCodeAI-Setup.exe`. It creates Start Menu and optional desktop shortcuts. The installer is built on a Windows runner; do not cross-compile it from Linux.

### Linux

The release workflow creates `ManuCodeAI-x86_64.AppImage` and a portable `ManuCodeAI-linux-x86_64.tar.gz` archive on Ubuntu 22.04. Make the AppImage executable before launching it:

```bash
chmod +x ManuCodeAI-x86_64.AppImage
./ManuCodeAI-x86_64.AppImage
```

ManuCodeAI still requires Visual Studio Code and its `code` command to be installed. Linux visible typing also requires `xdotool` and a clipboard utility: `wl-clipboard` on Wayland, or `xclip` / `xsel` on X11. These are host desktop integration tools and are not bundled into the AppImage. Wayland compositors may restrict programmatic window activation.

### Publishing

Push a version tag such as `v1.0.0` to build both platforms and publish the installer, AppImage, and Linux archive as GitHub Release assets. Running **Build desktop releases** manually creates downloadable workflow artifacts for testing without publishing a public release. The workflow targets x86-64 Windows and Linux.
