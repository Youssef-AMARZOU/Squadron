"""Unit tests for the lead/sub-agent crew."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripted import ScriptedProvider, say, tool_call, tool_calls

from squadron.config import Config
from squadron.crew import Crew
from squadron.events import EventLog
from squadron.runner import Runner

_binary: Path | None = None


def setUpModule() -> None:
    global _binary
    probe = Runner(Path(tempfile.mkdtemp(prefix="sqn-probe-")))
    _binary = probe.ensure()


class CrewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="sqn-crew-"))
        self.runner = Runner(self.workspace, _binary)
        self.events_path = self.workspace / "events.jsonl"
        self.events = EventLog(self.events_path)
        self.config = Config(
            provider="anthropic",
            model="test-model",
            api_key="test-key",
            workspace=self.workspace,
            max_steps=6,
        )

    def tearDown(self) -> None:
        self.events.close()

    def _crew(self, scripts: dict) -> Crew:
        def factory(role, name):
            return scripts.get(role) or ScriptedProvider([say("(unused)")])

        return Crew(
            self.config,
            self.runner,
            self.events,
            provider_factory=factory,
            max_workers=4,
        )

    def _rows(self) -> list[dict]:
        lines = self.events_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def test_lead_delegates_to_coder_and_receives_report(self) -> None:
        scripts = {
            "lead": ScriptedProvider(
                [
                    tool_call(
                        "spawn_subagent",
                        {
                            "role": "coder",
                            "task": "Create greeting.py that prints hello",
                        },
                    ),
                    say("Done: greeting.py created."),
                ]
            ),
            "coder": ScriptedProvider(
                [
                    tool_call(
                        "write_file",
                        {"path": "greeting.py", "content": "print('hello')\n"},
                    ),
                    say("created greeting.py"),
                ]
            ),
        }
        report = self._crew(scripts).run("build greeting.py")

        self.assertEqual(report, "Done: greeting.py created.")
        created = self.workspace / "greeting.py"
        self.assertTrue(created.is_file())
        self.assertIn("print('hello')", created.read_text(encoding="utf-8"))

        kinds = [row["kind"] for row in self._rows()]
        self.assertIn("subagent_start", kinds)
        self.assertIn("subagent_done", kinds)
        self.assertIn("crew_done", kinds)

    def test_parallel_spawns_both_complete(self) -> None:
        scripts = {
            "lead": ScriptedProvider(
                [
                    tool_calls(
                        [
                            (
                                "spawn_subagent",
                                {"role": "researcher", "task": "Survey the files"},
                            ),
                            (
                                "spawn_subagent",
                                {"role": "reviewer", "task": "Review greeting.py"},
                            ),
                        ]
                    ),
                    say("Both specialists reported back."),
                ]
            ),
            "researcher": ScriptedProvider([say("findings: workspace is empty")]),
            "reviewer": ScriptedProvider([say("verdict: fine")]),
        }
        report = self._crew(scripts).run("survey and review")

        self.assertEqual(report, "Both specialists reported back.")
        subagents = [
            row["data"].get("subagent")
            for row in self._rows()
            if row["kind"] == "subagent_done"
        ]
        self.assertCountEqual(subagents, ["researcher-1", "reviewer-1"])

    def test_unknown_role_is_rejected(self) -> None:
        crew = self._crew({})
        result = crew.run_subagent("boss", "do something")
        self.assertIn("ERROR", result)
        self.assertIn("unknown role", result)

    def test_empty_task_is_rejected(self) -> None:
        crew = self._crew({})
        result = crew.run_subagent("coder", "   ")
        self.assertIn("ERROR", result)


if __name__ == "__main__":
    unittest.main()
