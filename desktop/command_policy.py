"""Global command policy for the standalone coding agent."""
from __future__ import annotations

import re


# These commands can fetch, install, upgrade, or remove dependencies. Block them
# before subprocess execution on every OS; users manage dependency installation.
_INSTALL_PATTERNS = (
    r"\\b(?:python(?:\\d+(?:\\.\\d+)*)?|py)\\s+(?:-m\\s+)?pip\\s+(?:install|uninstall|download)\\b",
    r"\\bpip(?:3(?:\\.\\d+)?)?\\s+(?:install|uninstall|download)\\b",
    r"\\buv\\s+(?:pip\\s+)?(?:install|sync|add|remove)\\b",
    r"\\b(?:poetry|pipenv)\\s+(?:install|add|update|upgrade|remove|uninstall|sync)\\b",
    r"\\b(?:npm|pnpm|bun)\\s+(?:install|i|ci|add|update|upgrade|remove|uninstall)\\b",
    r"\\byarn\\s+(?:install|add|upgrade|remove)\\b",
    r"\\b(?:conda|mamba)\\s+(?:install|update|upgrade|create|remove|uninstall)\\b",
    r"\\bcargo\\s+(?:add|install|update|remove)\\b",
    r"\\bgo\\s+get\\b",
    r"\\bcomposer\\s+(?:install|update|require|remove)\\b",
    r"\\bbundle\\s+(?:install|update|add)\\b",
    r"\\bgem\\s+install\\b",
    r"\\bdotnet\\s+add\\s+package\\b",
    r"\\b(?:apt|apt-get|dnf|yum|pacman|winget|choco|scoop)\\s+(?:install|upgrade|update)\\b",
    r"\\b(?:npx|bunx)\\s+",
    r"\\bpython(?:\\d+(?:\\.\\d+)*)?\\s+-m\\s+ensurepip\\b",
)


def blocked_install_command(command: str) -> str | None:
    """Return a user-facing explanation when a command installs/downloads packages."""
    if any(re.search(pattern, command, re.IGNORECASE | re.MULTILINE) for pattern in _INSTALL_PATTERNS):
        return (
            "Blocked: ManuCodeAI never installs, downloads, updates, upgrades, or removes "
            "dependencies automatically. Keep coding and update the dependency manifest if "
            "needed. Tell the user the exact command to run manually in the terminal."
        )
    return None
