# ManuCodeAI

ManuCodeAI is a **standalone Windows and Linux desktop coding agent**. It uses Visual Studio Code as the visible editor and types generated code progressively so you can watch each change. It is not a VS Code extension.

## Features

- Desktop interface built with PySide6.
- OpenAI-compatible providers: AgentRouter, Gemini, OpenRouter, and compatible custom endpoints.
- Anthropic-compatible Coarena provider.
- Provider-specific API keys stored in the operating system credential store through keyring.
- Model discovery where the provider exposes a compatible model-list endpoint; manual model IDs are supported.
- Select an existing project folder and give the agent a coding task.
- Project discovery, file reading, ordered file plans, targeted edits, and new-file creation.
- Visible, paced typing in VS Code; no silent direct writes of generated source code.
- Automatic save and read-back verification after each file.
- Persistent task progress and file manifest under `.manucodeai/` in the selected project.
- Bounded retries for transient provider/network failures.
- Stop button and Windows global emergency shortcut (Ctrl+Shift+X).
- Workspace-boundary checks and protection for common secret files.
- Automated Python tests and cross-platform CI.

## Requirements

- Python 3.11 or newer.
- Visual Studio Code installed, with the `code` command available on PATH.
- A supported AI provider API key and a model ID.
- Windows 10/11 or Linux desktop session.

### Linux input dependencies

The Linux visible-typing backend requires `xdotool` and a clipboard utility.

Fedora KDE/X11:

```bash
sudo dnf install xdotool xclip
```

Wayland:

```bash
sudo dnf install xdotool wl-clipboard
```

The current Linux focus backend depends on the VS Code window being visible to `xdotool`. Some native Wayland configurations prevent external window activation; ManuCodeAI should stop rather than type into an unverified window.

## Install and run from source

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r desktop\requirements.txt
python run_desktop.py
```

### Linux

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r desktop/requirements.txt
python run_desktop.py
```

## Configure and use

1. Start ManuCodeAI.
2. Select the AI provider.
3. Enter the provider API key.
4. Fetch available models or enter the exact model ID manually.
5. Select the project folder.
6. Describe the requested change, including relevant requirements and constraints.
7. Select **Start coding**. ManuCodeAI opens the project in VS Code and works through its ordered plan.
8. Use **Stop** or press **Ctrl+Shift+X** on Windows to interrupt an active task.

API keys are stored using the OS credential store when available. They are not written to the selected project or the progress manifest. If secure credential storage is unavailable, ManuCodeAI reports that the key cannot be persisted.

## Provider notes

| Provider | API protocol | Model discovery |
| --- | --- | --- |
| AgentRouter | OpenAI-compatible | Fetch models |
| Gemini | OpenAI-compatible chat endpoint | Gemini model-list endpoint |
| OpenRouter | OpenAI-compatible | Fetch models |
| Coarena | Anthropic Messages API | Enter model ID manually |

For compatible custom providers, choose the closest matching protocol and supply the endpoint and model ID. Provider compatibility depends on the provider implementing the relevant API and tool-calling behavior.

## Progress and recovery

The selected project receives a `.manucodeai/` directory containing a progress checkpoint and file manifest. These files track task state, planned files, verification hashes, and limited tool-event metadata. They do not intentionally store API keys or full command output.

Starting the same unfinished prompt in the same project can resume from the saved checkpoint. The agent reconciles completed-file hashes against the current workspace before treating earlier work as complete.

The default agent safety limit is 60 model rounds. Set `MANUCODE_MAX_ROUNDS` before launching to change it (minimum 12).

## Development and verification

Install both dependency sets, then run:

```bash
python -m compileall -q desktop
python -m unittest discover -s tests -v
```

GitHub Actions runs the same checks on Windows and Ubuntu for supported Python versions.

## Packaging a Windows executable

Install the optional packaging dependency and build on Windows:

```powershell
python -m pip install pyinstaller
pyinstaller --noconfirm --clean --windowed --name ManuCodeAI --collect-all PySide6 --collect-all shiboken6 run_desktop.py
```

The application bundle is written to `dist/ManuCodeAI/`. Build on the target operating system; a Windows executable must be built on Windows and a Linux executable on Linux. VS Code and Linux input utilities remain external prerequisites.

## Important behavior and limitations

- ManuCodeAI is a standalone desktop application; there is no `vscode-extension/` component.
- Generated source changes are entered visibly through the selected VS Code window. Keep that window available and do not manually edit the same file during an active write.
- The global Ctrl+Shift+X emergency shortcut is currently Windows-only. The on-screen Stop button is available on both supported platforms.
- Linux visible typing depends on the active display server, window focus permissions, `xdotool`, and a supported clipboard utility.
- Model names, tool calling, rate limits, and availability are controlled by the selected provider.
- Review generated code and test results before using changes in production.
