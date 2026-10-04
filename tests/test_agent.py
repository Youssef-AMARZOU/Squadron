"""Unit tests for the single-agent loop."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripted import ScriptedProvider, say, tool_call

from squadron.agent import Agent
from squadron.events import EventLog
from squadron.runner import Runner
from squadron.tools import Toolbox

_binary: Path | None = None


def setUpModule() -> None:
    global _binary
    probe = Runner(Path(tempfile.mkdtemp(prefix="sqn-probe-")))
    _binary = probe.ensure()


class AgentLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="sqn-agent-"))
        self.runner = Runner(self.workspace, _binary)
        self.events = EventLog(self.workspace / "events.jsonl")

    def tearDown(self) -> None:
        self.events.close()

    def _agent(self, provider, allowed: set[str]) -> Agent:
        toolbox = Toolbox(self.runner, self.events, allowed)
        return Agent(
            name="coder-1",
            role="coder",
            system="test system",
            provider=provider,
            toolbox=toolbox,
            events=self.events,
            max_steps=6,
        )

    def test_agent_writes_file_then_reports(self) -> None:
        provider = ScriptedProvider(
            [
                tool_call(
                    "write_file",
                    {"path": "hello.py", "content": "print('Squadron online')\n"},
                ),
                say("Created hello.py with the requested content."),
            ]
        )
        report = self._agent(provider, {"write_file", "read_file"}).run(
            "create hello.py"
        )
        self.assertEqual(report, "Created hello.py with the requested content.")
        content = (self.workspace / "hello.py").read_text(encoding="utf-8")
        self.assertIn("Squadron online", content)
        self.assertEqual(provider.calls, 2)

    def test_disallowed_tool_returns_error_to_the_model(self) -> None:
        provider = ScriptedProvider(
            [
                tool_call("write_file", {"path": "x.txt", "content": "no"}),
                say("noted the failure"),
            ]
        )
        report = self._agent(provider, {"read_file"}).run("try to write")
        self.assertEqual(report, "noted the failure")
        rows = [
            json.loads(line)
            for line in (self.workspace / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        failures = [
            row
            for row in rows
            if row["kind"] == "tool_end" and row["data"].get("ok") is False
        ]
        self.assertTrue(failures)
        self.assertIn("ERROR", failures[0]["data"]["result"])

    def test_tool_errors_are_reported_without_crashing(self) -> None:
        provider = ScriptedProvider(
            [
                tool_call("read_file", {"path": "missing.txt"}),
                say("file was missing"),
            ]
        )
        report = self._agent(provider, {"read_file"}).run("read missing.txt")
        self.assertEqual(report, "file was missing")


if __name__ == "__main__":
    unittest.main()
