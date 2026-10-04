"""The crew: a lead agent plus its specialist sub-agents."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from .agent import Agent, ToolResult
from .config import Config
from .events import EventLog
from .providers import build_provider
from .runner import Runner
from .tools import Toolbox

LEAD_SYSTEM = """You are the lead of Squadron, a team of specialist sub-agents that \
share one workspace.

You receive a task from the user. You do not do the hands-on work yourself; you \
delegate it:

- spawn_subagent(role, task, context) hands a self-contained assignment to a \
specialist. Give exact file paths, acceptance criteria and constraints in `task`.
- When assignments are independent, spawn several specialists in a single reply \
so they run in parallel.
- You may use read_file and list_files yourself for quick reconnaissance.
- Never spawn more specialists than the task needs.

Available roles:
- researcher: read-only reconnaissance, surveys existing files and reports facts.
- coder: creates and edits files, implements the requested changes.
- tester: runs commands and reports whether the workspace behaves as expected.
- reviewer: reads the produced files and reports defects, gaps and risks.

Work until the goal is verifiably achieved, then reply with a final report that \
lists: what was done, which files were created or changed, how it was verified, \
and anything still open. Keep the report concise and factual."""

ROLE_SYSTEM = {
    "lead": LEAD_SYSTEM,
    "researcher": (
        "You are a researcher sub-agent on the Squadron team. You inspect the \
workspace with read_file, list_files and run_command and report precise facts to \
the lead: what exists, what each file contains, which conventions are in use. \
You never modify files. Your final message is a structured findings report with \
concrete paths and short quoted excerpts."
    ),
    "coder": (
        "You are a coder sub-agent on the Squadron team. You implement exactly \
what the assignment asks, using write_file to create or overwrite files and \
read_file/list_files/run_command to stay oriented. Follow the existing style of \
the workspace. Keep code minimal and correct; do not add unrelated features. Your \
final message lists the files you wrote and any decisions the lead must know."
    ),
    "tester": (
        "You are a tester sub-agent on the Squadron team. You verify the \
workspace by running commands with run_command and inspecting results with \
read_file/list_files. Report each check, its outcome, and the exact output that \
proves it. If something fails, report the failure precisely; you may only fix \
things if the assignment explicitly tells you to."
    ),
    "reviewer": (
        "You are a reviewer sub-agent on the Squadron team. You read the files \
named in your assignment and judge correctness, consistency and completeness \
against the assignment. You never modify files. Your final message is a short \
verdict: findings ordered by severity with file paths, or an explicit statement \
that nothing material was found."
    ),
}

ROLE_TOOLS = {
    "lead": {"read_file", "list_files"},
    "researcher": {"read_file", "list_files", "run_command"},
    "coder": {"read_file", "write_file", "list_files", "run_command"},
    "tester": {"run_command", "read_file", "list_files"},
    "reviewer": {"read_file", "list_files"},
}

SPECIALIST_ROLES = ["researcher", "coder", "tester", "reviewer"]

SPAWN_TOOL = {
    "name": "spawn_subagent",
    "description": (
        "Delegate a self-contained assignment to a specialist sub-agent. Multiple "
        "spawns in one reply run in parallel. The tool result is the specialist's "
        "final report."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "role": {
                "type": "string",
                "enum": SPECIALIST_ROLES,
                "description": "Which specialist should take the assignment",
            },
            "task": {
                "type": "string",
                "description": (
                    "The full assignment: goal, exact file paths, acceptance "
                    "criteria and constraints. Assume the specialist sees nothing "
                    "else of the conversation."
                ),
            },
            "context": {
                "type": "string",
                "description": "Optional background the specialist needs (file paths, facts)",
            },
        },
        "required": ["role", "task"],
    },
}


class LeadAgent(Agent):
    def __init__(self, crew: "Crew", **kwargs) -> None:
        super().__init__(**kwargs)
        self.crew = crew

    def handle_calls(self, calls: list) -> list[ToolResult]:
        results: dict[int, ToolResult] = {}
        spawns: list[tuple[int, object]] = []

        for index, call in enumerate(calls):
            if call.name == "spawn_subagent":
                spawns.append((index, call))
            else:
                content = self.toolbox.execute(
                    self.name, call.name, call.arguments
                )
                results[index] = ToolResult(call.id, call.name, content)

        if spawns:
            workers = min(self.crew.max_workers, len(spawns))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = []
                for index, call in spawns:
                    arguments = call.arguments or {}
                    future = pool.submit(
                        self.crew.run_subagent,
                        str(arguments.get("role", "")),
                        str(arguments.get("task", "")),
                        str(arguments.get("context") or ""),
                    )
                    futures.append((index, call, future))
                for index, call, future in futures:
                    try:
                        content = future.result()
                    except Exception as error:
                        content = (
                            f"ERROR: sub-agent failed: "
                            f"{type(error).__name__}: {error}"
                        )
                    results[index] = ToolResult(call.id, call.name, content)

        return [results[index] for index in sorted(results)]


class Crew:
    def __init__(
        self,
        config: Config,
        runner: Runner,
        events: EventLog,
        provider_factory=None,
        max_workers: int = 4,
    ) -> None:
        self.config = config
        self.runner = runner
        self.events = events
        self.max_workers = max_workers
        if provider_factory is None:
            default_provider = build_provider(
                config.provider, config.api_key, config.model, config.max_tokens
            )
            provider_factory = lambda role, name: default_provider
        self.provider_factory = provider_factory
        self._counters: dict[str, int] = {}
        self._counter_lock = threading.Lock()

    def run(self, task: str) -> str:
        toolbox = Toolbox(self.runner, self.events, ROLE_TOOLS["lead"] | {"spawn_subagent"})
        lead = LeadAgent(
            crew=self,
            name="lead",
            role="lead",
            system=ROLE_SYSTEM["lead"],
            provider=self.provider_factory("lead", "lead"),
            toolbox=toolbox,
            events=self.events,
            max_steps=self.config.max_steps,
        )
        self.events.emit("crew_start", "lead", task=task[:600])
        report = lead.run(task)
        self.events.emit("crew_done", "lead", report_chars=len(report))
        return report

    def run_subagent(self, role: str, task: str, context: str = "") -> str:
        if role not in ROLE_SYSTEM or role == "lead":
            return (
                f"ERROR: unknown role \"{role}\"; expected one of: "
                + ", ".join(SPECIALIST_ROLES)
            )
        if not task.strip():
            return "ERROR: empty task; the lead must provide a concrete assignment"

        with self._counter_lock:
            self._counters[role] = self._counters.get(role, 0) + 1
            number = self._counters[role]
        name = f"{role}-{number}"

        full_task = task.strip()
        if context.strip():
            full_task += "\n\nContext provided by the lead:\n" + context.strip()

        self.events.emit(
            "subagent_start", "lead", subagent=name, role=role, task=task[:400]
        )
        toolbox = Toolbox(self.runner, self.events, ROLE_TOOLS[role])
        agent = Agent(
            name=name,
            role=role,
            system=ROLE_SYSTEM[role],
            provider=self.provider_factory(role, name),
            toolbox=toolbox,
            events=self.events,
            max_steps=self.config.max_steps,
        )
        report = agent.run(full_task)
        self.events.emit(
            "subagent_done", "lead", subagent=name, role=role, chars=len(report)
        )
        return f"Sub-agent {name} ({role}) finished:\n{report}"
