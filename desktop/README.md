# ManuCodeAI Desktop (Windows)

This is the standalone Windows application. Users configure the AI provider,
select a project folder, and enter instructions here. VS Code is launched as
the editor; no ManuCodeAI VS Code extension is required for this workflow.

## Current foundation

- Native desktop UI built with PySide6.
- Provider URL, model, and API-key fields.
- API key persistence through the operating system credential store (keyring).
- Project-folder selection.
- Coding agent with project listing, file reading, approved file changes, and
  approved command execution.
- VS Code launcher using the `code` command.
- Native Windows `SendInput` Unicode typing, with variable character delays.
- File changes and terminal commands are approved in ManuCodeAI, not through
  VS Code popups.

## Development setup

From the repository root in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py -m pip install -r desktop\requirements.txt
py -m desktop.app
```

VS Code must be installed and its `code` command must be available on PATH.

## Important limitations of this first desktop foundation

- The app targets Windows.
- The first release uses the provider's non-streaming tool-call response, then
  types each approved file into VS Code at a steady human-readable pace.
- VS Code must be the foreground application when the typing begins. Do not
  use the keyboard or switch applications during an active typing operation.
- The current implementation asks for approval in the ManuCodeAI window before
  each file change and terminal command.
- Local account registration/synchronization, richer model discovery, robust
  VS Code window activation, and packaged installer work remain future tasks.

The original Python terminal agent and existing backend/extension are preserved
while this standalone application is developed on its own branch.
