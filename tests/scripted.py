"""Scripted provider used to test agent behaviour without network access."""

from __future__ import annotations

from squadron.providers import Completion, ToolCall


class ScriptedProvider:
    def __init__(self, script: list[Completion]) -> None:
        self.script = list(script)
        self.calls = 0

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Completion:
        self.calls += 1
        if not self.script:
            return Completion(text="(script exhausted)", tool_calls=[])
        return self.script.pop(0)


def say(text: str) -> Completion:
    return Completion(text=text, tool_calls=[])


def tool_call(name: str, arguments: dict, call_id: str = "call_1") -> Completion:
    return Completion(
        text="",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
    )


def tool_calls(items: list[tuple[str, dict]]) -> Completion:
    return Completion(
        text="",
        tool_calls=[
            ToolCall(id=f"call_{index}", name=name, arguments=arguments)
            for index, (name, arguments) in enumerate(items, start=1)
        ],
    )
