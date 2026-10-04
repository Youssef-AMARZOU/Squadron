"""Integration tests for the compiled tool runner."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from squadron.runner import Runner, RunnerError

_binary: Path | None = None


def setUpModule() -> None:
    global _binary
    probe = Runner(Path(tempfile.mkdtemp(prefix="sqn-probe-")))
    _binary = probe.ensure()


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="sqn-ws-"))
        self.runner = Runner(self.workspace, _binary)

    def test_exec_returns_stdout_and_exit_code(self) -> None:
        result = self.runner.call({"op": "exec", "command": "echo hello-squadron"})
        self.assertEqual(result["exit"], 0)
        self.assertFalse(result["timed_out"])
        self.assertIn("hello-squadron", result["stdout"])

    def test_write_read_list_roundtrip(self) -> None:
        written = self.runner.call(
            {
                "op": "write",
                "path": "docs/notes.txt",
                "content": "line one\nline two\n",
            }
        )
        self.assertGreater(written["bytes"], 0)

        listing = self.runner.call({"op": "list", "path": "."})
        names = [entry["name"] for entry in listing["entries"]]
        self.assertIn("docs", names)

        nested = self.runner.call({"op": "list", "path": "docs"})
        self.assertIn("notes.txt", [entry["name"] for entry in nested["entries"]])

        read = self.runner.call({"op": "read", "path": "docs/notes.txt"})
        self.assertEqual(read["content"], "line one\nline two\n")
        self.assertFalse(read["truncated"])

    def test_path_escape_is_rejected(self) -> None:
        with self.assertRaises(RunnerError):
            self.runner.call({"op": "write", "path": "../evil.txt", "content": "no"})
        outside = self.workspace.parent / "evil.txt"
        self.assertFalse(outside.exists())

    def test_absolute_path_outside_workspace_is_rejected(self) -> None:
        with self.assertRaises(RunnerError):
            self.runner.call({"op": "read", "path": "C:\\Windows\\win.ini"})

    def test_stat_reports_missing_files(self) -> None:
        result = self.runner.call({"op": "stat", "path": "does-not-exist.txt"})
        self.assertFalse(result["exists"])

    def test_command_timeout_is_enforced(self) -> None:
        result = self.runner.call(
            {
                "op": "exec",
                "command": "python -c \"import time; time.sleep(8)\"",
                "timeout_ms": 300,
            }
        )
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["exit"])


if __name__ == "__main__":
    unittest.main()
