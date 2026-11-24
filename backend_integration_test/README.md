# Legacy backend integration harness

This folder contains a separate FastAPI streaming-backend test harness retained for reference. It is not part of the standalone desktop application's coding workflow and does not require a VS Code extension.

The standalone desktop application is started from the repository root with:

```bash
python run_desktop.py
```

Its provider, workspace, visible VS Code typing, file verification, and recovery workflow are implemented in `desktop/`. The current automated desktop-agent checks are in `tests/`.

The files in this folder exercise the legacy server-side streaming API only. They should not be treated as verification that the standalone desktop agent or its visible editor integration works.
