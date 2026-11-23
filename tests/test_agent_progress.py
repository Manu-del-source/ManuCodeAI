"""Regression tests for persisted file-plan and completion safeguards."""
from __future__ import annotations

import hashlib
import tempfile
import threading
import unittest
from pathlib import Path

from desktop.agent import CodingAgent


class CodingAgentProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()
        self.agent = CodingAgent.__new__(CodingAgent)
        self.agent.workspace = self.workspace
        self.agent.manifest_path = self.workspace / ".manucodeai" / "file_manifest.json"
        self.agent.progress_path = self.workspace / ".manucodeai" / "progress.json"
        self.agent.cancel_event = threading.Event()
        self.agent.task_hash = "test-task-hash"
        self.agent.progress = {"task": "test task", "completed_files": []}
        self.agent.file_manifest = {
            "task_hash": self.agent.task_hash,
            "task": "test task",
            "files": {},
        }
        self.agent._completed_files = set()
        self.agent._successful_edits = {}
        self.agent._last_successful_file_change = None
        self.agent.implementation_complete = False
        self.agent.status = lambda _message: None

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_refreshing_plan_preserves_verified_fingerprint(self):
        fingerprint = hashlib.sha256(b"verified content").hexdigest()
        self.agent.file_manifest["files"] = {
            "src/first.py": {
                "status": "COMPLETED",
                "verification_or_reason": "Exact read-back passed.",
                "verified_sha256": fingerprint,
                "updated_at": "2026-11-23T00:00:00+00:00",
            }
        }

        self.agent._set_file_plan(["src/first.py", "src/second.py"])

        first = self.agent.file_manifest["files"]["src/first.py"]
        self.assertEqual(first["status"], "COMPLETED")
        self.assertEqual(first["verified_sha256"], fingerprint)
        self.assertIn("src/second.py", self.agent.file_manifest["files"])

    def test_completed_file_cannot_be_written_again(self):
        self.agent.file_manifest["files"] = {
            "src/first.py": {"status": "COMPLETED"}
        }

        result = self.agent._write_file("src/first.py", "print('duplicate')")

        self.assertIn("marked COMPLETED", result)

    def test_completed_file_cannot_be_edited_again(self):
        target = self.workspace / "src" / "first.py"
        target.parent.mkdir(parents=True)
        target.write_text("print('already done')\n", encoding="utf-8")
        self.agent.file_manifest["files"] = {
            "src/first.py": {"status": "COMPLETED"}
        }

        result = self.agent._edit_file(
            "src/first.py", "already done", "duplicate change"
        )

        self.assertIn("first transition it to NEEDS_CORRECTION", result)

    def test_later_file_cannot_skip_first_unfinished_file(self):
        self.agent.file_manifest["files"] = {
            "src/first.py": {"status": "IN_PROGRESS"},
            "src/second.py": {"status": "IN_PROGRESS"},
        }

        result = self.agent._write_file("src/second.py", "print('skip')")

        self.assertIn("finish src/first.py first", result)

    def test_completion_gate_detects_file_changed_after_verification(self):
        target = self.workspace / "src" / "first.py"
        target.parent.mkdir(parents=True)
        target.write_text("print('changed')\n", encoding="utf-8")
        original_digest = hashlib.sha256(b"print('original')\n").hexdigest()
        self.agent.file_manifest["files"] = {
            "src/first.py": {
                "status": "COMPLETED",
                "verified_sha256": original_digest,
            }
        }

        result = self.agent._complete_implementation(["src/first.py"])

        self.assertIn("saved content changed after verification", result)
        self.assertFalse(self.agent.implementation_complete)

    def test_needs_correction_reopens_only_with_a_reason(self):
        target = self.workspace / "src" / "first.py"
        target.parent.mkdir(parents=True)
        target.write_text("print('before')\n", encoding="utf-8")
        self.agent.file_manifest["files"] = {
            "src/first.py": {"status": "COMPLETED"}
        }
        self.agent._last_successful_file_change = "src/first.py"

        rejected = self.agent._update_file_status(
            "src/first.py", "NEEDS_CORRECTION", " "
        )
        self.assertIn("include a verification note", rejected)
        self.assertEqual(
            self.agent.file_manifest["files"]["src/first.py"]["status"],
            "COMPLETED",
        )

        accepted = self.agent._update_file_status(
            "src/first.py", "NEEDS_CORRECTION", "A failing syntax check requires correction."
        )
        self.assertIn("NEEDS_CORRECTION", accepted)
        self.assertEqual(
            self.agent.file_manifest["files"]["src/first.py"]["status"],
            "NEEDS_CORRECTION",
        )
        self.assertIsNone(self.agent._last_successful_file_change)


if __name__ == "__main__":
    unittest.main()
