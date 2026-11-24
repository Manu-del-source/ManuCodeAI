"""Focused, provider-free tests for standalone desktop agent safety."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from desktop.agent import CodingAgent


class CodingAgentPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()
        self.agent = object.__new__(CodingAgent)
        self.agent.workspace = self.workspace
        self.agent._read_since_write = set()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_accepts_path_inside_workspace(self) -> None:
        target = self.agent._safe_path("src/example.py")
        self.assertEqual(target, self.workspace / "src" / "example.py")

    def test_rejects_parent_traversal(self) -> None:
        with self.assertRaises(ValueError):
            self.agent._safe_path("../outside.txt")

    def test_rejects_absolute_path_outside_workspace(self) -> None:
        outside = self.workspace.parent / "outside-manucodeai-test.txt"
        with self.assertRaises(ValueError):
            self.agent._safe_path(str(outside))

    def test_protects_secret_filenames_and_extensions(self) -> None:
        for filename in (".env", ".env.local", "credentials.json", "private.pem", "key.p12"):
            with self.subTest(filename=filename):
                with self.assertRaises(ValueError):
                    self.agent._safe_path(filename)

    def test_blocks_ignored_directories(self) -> None:
        for filename in (".git/config", ".venv/pyvenv.cfg", "node_modules/pkg/index.js"):
            with self.subTest(filename=filename):
                with self.assertRaises(ValueError):
                    self.agent._safe_path(filename)

    def test_read_file_tracks_read_for_verification(self) -> None:
        target = self.workspace / "sample.txt"
        target.write_text("hello", encoding="utf-8")
        self.assertEqual(self.agent._read_file("sample.txt"), "hello")
        self.assertIn("sample.txt", self.agent._read_since_write)

    def test_read_file_rejects_non_utf8_without_crashing(self) -> None:
        (self.workspace / "binary.dat").write_bytes(b"\xff\xfe\xfd")
        self.assertEqual(
            self.agent._read_file("binary.dat"),
            "This file is not UTF-8 text and was not read.",
        )

    def test_list_files_omits_secrets_and_ignored_folders(self) -> None:
        (self.workspace / "visible.py").write_text("pass", encoding="utf-8")
        (self.workspace / ".env").write_text("SECRET=do-not-read", encoding="utf-8")
        hidden = self.workspace / ".git"
        hidden.mkdir()
        (hidden / "config").write_text("ignored", encoding="utf-8")
        listing = self.agent._list_files(".")
        self.assertIn("visible.py", listing)
        self.assertNotIn(".env", listing)
        self.assertNotIn(".git/config", listing)
        self.assertNotIn("do-not-read", listing)


if __name__ == "__main__":
    unittest.main()
