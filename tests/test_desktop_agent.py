"""Focused, provider-free tests for standalone desktop agent safety."""
from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from desktop.agent import AgentError, CodingAgent


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



    def test_completed_file_cannot_be_downgraded_to_in_progress(self) -> None:
        self.agent.file_manifest = {
            "task_hash": "test",
            "files": {
                "src/example.py": {
                    "status": "COMPLETED",
                    "verified_sha256": "abc",
                }
            },
        }
        self.agent.manifest_path = self.workspace / ".manucodeai" / "file_manifest.json"
        self.agent.progress_path = self.workspace / ".manucodeai" / "progress.json"
        self.agent.progress = {}
        self.agent._completed_files = {"src/example.py"}
        result = self.agent._update_file_status(
            "src/example.py", "IN_PROGRESS", "start editing again"
        )
        self.assertIn("cannot be reopened or downgraded", result.lower())
        self.assertEqual(
            self.agent.file_manifest["files"]["src/example.py"]["status"],
            "COMPLETED",
        )

    def test_validation_pending_file_requires_readback_before_completion(self) -> None:
        target = self.workspace / "sample.py"
        target.write_text("print('ok')\n", encoding="utf-8")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        self.agent.file_manifest = {
            "files": {
                "sample.py": {
                    "status": "VALIDATION_PENDING",
                    "saved_sha256": digest,
                }
            }
        }
        self.agent.manifest_path = self.workspace / ".manucodeai" / "file_manifest.json"
        self.agent.progress_path = self.workspace / ".manucodeai" / "progress.json"
        self.agent.progress = {}
        self.agent._completed_files = set()

        blocked = self.agent._update_file_status(
            "sample.py", "COMPLETED", "Focused unit tests passed."
        )
        self.assertIn("read the saved file again", blocked.lower())

        self.agent._read_file("sample.py")
        completed = self.agent._update_file_status(
            "sample.py", "COMPLETED", "Focused unit tests passed."
        )
        self.assertIn("sample.py is now COMPLETED", completed)
        self.assertEqual(
            self.agent.file_manifest["files"]["sample.py"]["status"],
            "COMPLETED",
        )

    def test_plan_order_prevents_starting_later_file(self) -> None:
        self.agent.file_manifest = {
            "files": {
                "first.py": {"status": "IN_PROGRESS"},
                "second.py": {"status": "NOT_STARTED"},
            }
        }
        self.agent.manifest_path = self.workspace / ".manucodeai" / "file_manifest.json"
        self.agent.progress_path = self.workspace / ".manucodeai" / "progress.json"
        self.agent.progress = {}
        self.agent._completed_files = set()
        result = self.agent._update_file_status(
            "second.py", "IN_PROGRESS", "start second file"
        )
        self.assertIn("finish first.py before starting second.py", result)
        self.assertEqual(
            self.agent.file_manifest["files"]["second.py"]["status"],
            "NOT_STARTED",
        )


class CodingAgentToolLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.agent = object.__new__(CodingAgent)
        self.agent.workspace = Path(self.temp_dir.name).resolve()
        self.agent.cancel_event = threading.Event()
        self.agent.provider = "OpenRouter"
        self.agent.model = "openrouter/free"
        self.agent.status = lambda _message: None
        self.agent.implementation_complete = False
        self.agent.progress = {"events": []}
        self.agent.progress_path = self.agent.workspace / ".manucodeai" / "progress.json"
        self.agent.client = Mock()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _message(content=None, tool_calls=None):
        return SimpleNamespace(
            content=content,
            tool_calls=tool_calls or [],
            model_dump=lambda exclude_none=True: {
                "role": "assistant",
                "content": content,
            },
        )

    @staticmethod
    def _response(message):
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def test_prose_response_does_not_finish_before_tool_execution(self) -> None:
        completion_call = SimpleNamespace(
            id="call-complete",
            function=SimpleNamespace(
                name="complete_implementation",
                arguments='{"planned_files":["sample.py"]}',
            ),
        )
        self.agent.client.chat.completions.create.side_effect = [
            self._response(self._message("Here is the architecture.")),
            self._response(self._message(tool_calls=[completion_call])),
            self._response(self._message("Implementation verified.")),
        ]

        def execute_tool(_name, _arguments):
            self.agent.implementation_complete = True
            return "Implementation gate passed."

        self.agent._execute_tool = execute_tool
        result = self.agent._run_openai("Build the application")
        self.assertEqual(result, "Implementation verified.")
        self.assertEqual(self.agent.client.chat.completions.create.call_count, 3)

    def test_repeated_prose_response_pauses_instead_of_reporting_success(self) -> None:
        self.agent.client.chat.completions.create.side_effect = [
            self._response(self._message("Architecture proposal.")),
            self._response(self._message("Another architecture proposal.")),
        ]
        with self.assertRaisesRegex(AgentError, "task remains PAUSED"):
            self.agent._run_openai("Build the application")


if __name__ == "__main__":
    unittest.main()
