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

Build on the target operating system:

```powershell
python -m pip install pyinstaller
pyinstaller --noconfirm --clean --windowed --name ManuCodeAI --collect-all PySide6 --collect-all shiboken6 run_desktop.py
```

The output is in `dist/ManuCodeAI/`. VS Code and platform-specific visible-input utilities are external prerequisites and are not bundled.
