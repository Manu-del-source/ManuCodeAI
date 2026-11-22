# Backend → extension integration verification

A manual verification harness (requires no VS Code) for the streaming
pipeline:

1. `backend_integration_test/fake_provider.py` starts the real FastAPI
   backend on `127.0.0.1:8766` with a mocked AI provider that streams a
   `write_file` call in genuine fragments.
2. A node client can drive the backend through the extension's own
   `AgentStreamRunner` (`vscode-extension/src/core/agentStreamRunner.ts` —
   the same code path the VS Code extension uses).

To run:

```bash
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn openai python-dotenv pydantic
# terminal 1: start the mocked backend
.venv/bin/python backend_integration_test/fake_provider.py
# terminal 2: point the consumer at http://127.0.0.1:8766
node --import tsx consumer.ts   # built like the session-level consumer used in verification
```

The `consumer.ts` helper logs each event (`filePath`, `fileContent`,
`fileDone`) proving genuine incremental delivery, and asserts the session
path/content/status against the final tool call.

Note: this verifies the backend + runner/parser + tracker wiring. The
actual editor rendering in VS Code requires a real desktop VS Code instance —
see the main README.
