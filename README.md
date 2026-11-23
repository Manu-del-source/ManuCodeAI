# ManuCodeAI

AI coding agent with a standalone desktop application for Windows and Linux, using Visual Studio Code as the visible editor.

## Standalone desktop application

The desktop app is the primary autonomous workflow. It plans only the files that need changes, processes them in order, types changes visibly in VS Code at a deliberately paced and configurable rate, saves and verifies each file, and persists progress under `.manucodeai/` in the selected project.

### Windows

Install Python 3.11 or newer and Visual Studio Code. Ensure the `code` command is available on PATH.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py -m desktop.app
```

### Linux

Install Python 3.11 or newer, Visual Studio Code, and the visible-input utilities for your desktop session.

For Fedora KDE/X11:

```bash
sudo dnf install xdotool xclip
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m desktop.app
```

For Wayland, install `xdotool` and `wl-clipboard` (`wl-copy` and `wl-paste`). Some Wayland compositors restrict programmatic window activation; if ManuCodeAI cannot confirm VS Code focus, bring the requested VS Code tab to the foreground and retry. ManuCodeAI will not send keyboard input when it cannot confirm focus.

The Windows backend uses native keyboard input. The Linux backend uses `xdotool` and the session clipboard. Both retain the same adjustable natural typing delays and literal newline/tab insertion.


## Architecture

- `server/api.py` — FastAPI backend exposing `/health`, `/chat`, `/agent`, and streaming `/agent/stream` (SSE).
- `vscode-extension/` — VS Code extension. Generated code streams progressively into native editor tabs (untitled in-memory documents), per file, with approval (native diff + modal) before anything touches the workspace.
- `main.py` — original terminal agent (preserved).

### Extension structure

- `src/core/` — pure, VS Code-free modules (unit-tested with `node --test`):
  - `partialArgumentsParser.ts` — incremental, escape-aware parser for partial JSON tool arguments (any key order, malformed-safe, no raw JSON fragments).
  - `previewTracker.ts` — per-file generation state (streaming → ready → cancelled / applied / rejected).
  - `typewriter.ts` — human-like typing pace for the preview editor (queue, tiny chunks, per-file cancellation).
  - `agentStreamRunner.ts` — parses the backend SSE stream and drives the tracker.
  - `fileOps.ts` — the only workspace writers (read/write/conflict detection).
  - `approval.ts` / `language.ts` — approval decisions and path → language mapping.
- `src/previewManager.ts` — VS Code glue: untitled documents, native tabs, paced (human-like) reveal updates, cancellation status.
- `src/extension.ts` — wiring: SSE consumption, approval flow, webview sidebar.

### Preview → approval pipeline

1. The backend streams `tool_call_start`, `tool_call_delta` (raw argument fragments), `tool_call`, `done`.
2. The extension opens one untitled preview tab per `write_file` and types the incoming characters into it at the configured human-like speed. The stream itself is consumed at full speed — only the editor rendering is paced.
3. When the stream completes and the typing finishes, a native diff (original vs proposed) is shown; the user approves or rejects.
4. Approved changes are written — with an external-modification conflict check — and the workspace file is opened.
5. Rejected changes leave the workspace byte-identical; cancelled generation marks the preview incomplete and never executes writes.

## Typing speed

Generated code is revealed in the preview editor at a human-like pace instead of appearing in large batches. The pace applies to editor rendering only — the AI's generation and the SSE stream are never slowed down.

| Setting | Characters per second |
| --- | --- |
| `manucodeai.typingSpeed` = `slow` | 15 |
| `manucodeai.typingSpeed` = `normal` (default) | 35 |
| `manucodeai.typingSpeed` = `fast` | 70 |

Set it in **Settings → ManuCodeAI → Typing Speed** (`settings.json`: `"manucodeai.typingSpeed": "normal"`). Changes apply immediately, including to text already queued for an in-flight preview.

Pressing **Stop** halts the visual typing immediately: queued characters are dropped, pending reveal timers are cleared, and no further editor updates are applied. Characters already shown stay in the preview for reference (the incomplete preview is never written).

## Start the Python API

```bash
cd C:\Users\VISITOR\ManuCodeAI
.venv\Scripts\python.exe -m uvicorn server.api:app --host 127.0.0.1 --port 8765
```

Health check:

```bash
curl http://127.0.0.1:8765/health
```

## Compile and package the extension

```bash
cd C:\Users\VISITOR\ManuCodeAI\vscode-extension
npm.cmd install
.\node_modules\.bin\tsc.cmd -p tsconfig.json --noEmit
npm.cmd run compile
npx vsce package
```

This produces a `.vsix` file.

## Install the VSIX

In VS Code: Extensions view -> "..." menu -> "Install from VSIX..." -> select the generated `.vsix`.

## Streaming event protocol

SSE events from `/agent/stream`:

- `start` — stream began
- `delta` — assistant text chunk `{ content }`
- `tool_call_start` — a new tool call began `{ index, id, name }`
- `tool_call_delta` — partial tool call `{ index, id, name, arguments_delta }` (raw JSON fragments; the extension decodes them incrementally)
- `tool_call` — complete validated tool call `{ index, id, name, arguments }`
- `done` — `{ finished, tool_calls, response }`
- `cancelled` — stream aborted
- `error` — `{ message }`

## Security notes

- API binds to localhost by default.
- `.env` and secret files are never read or modified.
- File writes and command execution require explicit user approval.
- Workspace boundaries are enforced.
