# ManuCodeAI Desktop (Windows)

This is the standalone Windows application. Users configure an AI provider,
select a project folder, and enter instructions here. VS Code is launched as
the editor; no ManuCodeAI VS Code extension is required for this workflow.

## Supported providers

### AgentRouter

- Protocol: OpenAI-compatible Chat Completions
- Base URL: `https://agentrouter.org/v1`
- API key: AgentRouter API key
- Model: select **Fetch models** to retrieve IDs exposed to your key, or enter
  a model ID manually.
- Supports tool calls through the OpenAI-compatible endpoint.

### Coarena

- Protocol: Anthropic Messages API
- Base URL: `https://coarena.ai/api` (matching the Anthropic-compatible
  environment configuration used by Coarena)
- API key: Coarena `coa_...` token
- Model: enter the exact model ID enabled for your account.
- Uses the Anthropic SDK rather than sending the Coarena token to OpenAI's API.

The provider selector keeps separate API keys in the operating system
credential store. The API key is not written to project files or settings.

## Current foundation

- Native desktop UI built with PySide6.
- AgentRouter and Coarena provider selection.
- AgentRouter model discovery through its OpenAI-compatible `/models` endpoint.
- Editable model field for manually entering provider model IDs.
- API-key persistence through the operating system credential store (keyring).
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

## Important limitations

- The app targets Windows.
- The first release uses non-streaming tool-call responses, then types each
  approved file into VS Code at a steady human-readable pace.
- Model availability depends on the API key and provider account.
- Coarena model discovery is manual in this version; enter the model ID given
  by Coarena.
- VS Code must be the foreground application when typing begins. Do not use
  the keyboard or switch applications during an active typing operation.
- The current implementation asks for approval in ManuCodeAI before each file
  change and terminal command.
- Registration, packaged installer work, and richer model capability checks
  remain future tasks.

The original Python terminal agent and existing backend/extension are preserved
while this standalone application is developed on its own branch.
