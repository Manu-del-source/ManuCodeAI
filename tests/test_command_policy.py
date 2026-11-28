"""Tests for the global manual dependency-installation policy."""
from __future__ import annotations

import unittest

from desktop.command_policy import blocked_install_command


class DependencyInstallPolicyTests(unittest.TestCase):
    def test_blocks_common_install_and_download_commands(self) -> None:
        commands = (
            "pip install -r requirements.txt",
            "python -m pip install flask",
            "py -m pip install requests",
            "pip3 download django",
            "npm install",
            "npm ci",
            "pnpm add react",
            "yarn install",
            "uv sync",
            "uv pip install fastapi",
            "poetry add sqlalchemy",
            "pipenv install",
            "conda install numpy",
            "cargo add serde",
            "go get example.com/package",
            "composer install",
            "bundle install",
            "gem install rails",
            "dotnet add package Newtonsoft.Json",
            "winget install Python.Python.3.13",
            "npx create-vite my-app",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNotNone(blocked_install_command(command))

    def test_blocks_install_commands_in_chained_shell_commands(self) -> None:
        self.assertIsNotNone(
            blocked_install_command("python --version; pip install flask")
        )
        self.assertIsNotNone(
            blocked_install_command("cd frontend && npm install")
        )

    def test_allows_coding_and_validation_commands(self) -> None:
        commands = (
            "python --version",
            "python -m pytest",
            "python -m compileall desktop",
            "npm run lint",
            "npm test",
            "git status",
            "Get-ChildItem",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(blocked_install_command(command))


if __name__ == "__main__":
    unittest.main()
