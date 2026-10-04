"""The agent loop: converse with a provider until the task is done."""

from __future__ import annotations

from dataclasses import dataclass

from .events import EventLog
from .tools import Toolbox


@dataclass
class ToolResult:
    id: str
    name: str
    content: str


class Agent:
    def __init__(
        self,
        name: str,
        role: str,
        system: str,
        provider,
        toolbox: Toolbox,
        events: EventLog,
        max_steps: int = 24,
    ) -> None:
        self.name = name
        self.role = role
        self.system = system
        self.provider = provider
        self.toolbox = toolbox
        self.events = events
        self.max_steps = max_steps

    def run(self, task: str) -> str:
        messages: list[dict] = [{"role": "user", "content": task}]
        self.events.emit(
            "agent_start", self.name, role=self.role, task=task[:600]
        )
        final = ""
        last_text = ""
        steps_used = 0

        for step in range(1, self.max_steps + 1):
            steps_used = step
            completion = self.provider.complete(
                self.system, messages, self.toolbox.specs()
            )
            last_text = completion.text
            self.events.emit(
                "assistant",
                self.name,
                step=step,
                text=completion.text[:600],
                tool_calls=[call.name for call in completion.tool_calls],
            )
            if not completion.tool_calls:
                final = completion.text
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": completion.text or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "name": call.name,
                            "arguments": call.arguments,
                        }
                        for call in completion.tool_calls
                    ],
                }
            )
            for result in self.handle_calls(completion.tool_calls):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": result.id,
                        "name": result.name,
                        "content": result.content,
                    }
                )
        else:
            final = last_text or "(stopped: maximum steps reached without a final answer)"

        self.events.emit(
            "agent_done",
            self.name,
            role=self.role,
            steps=steps_used,
            final_chars=len(final),
        )
        return final

    def handle_calls(self, calls: list) -> list[ToolResult]:
        results: list[ToolResult] = []
        for call in calls:
            content = self.toolbox.execute(self.name, call.name, call.arguments)
            results.append(ToolResult(call.id, call.name, content))
        return results
