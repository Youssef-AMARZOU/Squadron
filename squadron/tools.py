"""Tool specifications and dispatch for sub-agents."""

from __future__ import annotations

import json

from .events import EventLog
from .runner import Runner, RunnerError

TOOL_SPECS: dict[str, dict] = {
    "run_command": {
        "name": "run_command",
        "description": (
            "Run a shell command inside the workspace and return its exit code, "
            "stdout and stderr. Commands are killed after the timeout."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command line to execute"},
                "cwd": {
                    "type": "string",
                    "description": "Working directory relative to the workspace root",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Kill the command after this many milliseconds (default 15000)",
                },
            },
            "required": ["command"],
        },
    },
    "read_file": {
        "name": "read_file",
        "description": "Read a UTF-8 text file relative to the workspace root.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the workspace root"},
            },
            "required": ["path"],
        },
    },
    "write_file": {
        "name": "write_file",
        "description": (
            "Create or overwrite a UTF-8 text file relative to the workspace root. "
            "Missing parent directories are created."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the workspace root"},
                "content": {"type": "string", "description": "Full file content to write"},
            },
            "required": ["path", "content"],
        },
    },
    "list_files": {
        "name": "list_files",
        "description": "List the entries of a directory relative to the workspace root.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory relative to the workspace root (default: root)",
                },
            },
            "required": [],
        },
    },
}

TOOL_ORDER = ["run_command", "read_file", "write_file", "list_files"]


class Toolbox:
    def __init__(self, runner: Runner, events: EventLog, allowed: set[str]) -> None:
        self.runner = runner
        self.events = events
        self.allowed = set(allowed)

    def specs(self) -> list[dict]:
        return [TOOL_SPECS[name] for name in TOOL_ORDER if name in self.allowed]

    def execute(self, agent: str, name: str, arguments: dict) -> str:
        self.events.emit("tool_start", agent, tool=name, arguments=_summarize(arguments))
        if name not in self.allowed:
            result = f"ERROR: tool \"{name}\" is not available to this agent"
            self.events.emit("tool_end", agent, tool=name, ok=False, result=result)
            return result
        try:
            result = self._dispatch(name, arguments or {})
        except RunnerError as error:
            result = f"ERROR: {error}"
            self.events.emit("tool_end", agent, tool=name, ok=False, result=result[:500])
            return result
        except Exception as error:  # defensive: agents receive errors as tool output
            result = f"ERROR: {type(error).__name__}: {error}"
            self.events.emit("tool_end", agent, tool=name, ok=False, result=result[:500])
            return result
        self.events.emit("tool_end", agent, tool=name, ok=True, result=result[:500])
        return result

    def _dispatch(self, name: str, arguments: dict) -> str:
        if name == "run_command":
            payload = {"op": "exec", "command": _require_str(arguments, "command")}
            if arguments.get("cwd"):
                payload["cwd"] = str(arguments["cwd"])
            if arguments.get("timeout_ms") is not None:
                payload["timeout_ms"] = int(arguments["timeout_ms"])
            result = self.runner.call(payload)
            lines = [
                f"exit={result.get('exit')} timed_out={result.get('timed_out')} "
                f"duration_ms={result.get('duration_ms')}"
            ]
            lines.append("--- stdout ---")
            lines.append(str(result.get("stdout", "")))
            stderr = str(result.get("stderr", "")).strip()
            if stderr:
                lines.append("--- stderr ---")
                lines.append(stderr)
            return "\n".join(lines).strip()

        if name == "read_file":
            result = self.runner.call(
                {"op": "read", "path": _require_str(arguments, "path")}
            )
            content = str(result.get("content", ""))
            if result.get("truncated"):
                content += "\n...[file truncated by runner]..."
            return content

        if name == "write_file":
            result = self.runner.call(
                {
                    "op": "write",
                    "path": _require_str(arguments, "path"),
                    "content": _require_str(arguments, "content"),
                }
            )
            return f"wrote {result.get('path')} ({result.get('bytes')} bytes)"

        if name == "list_files":
            payload = {"op": "list", "path": arguments.get("path") or "."}
            result = self.runner.call(payload)
            entries = result.get("entries") or []
            if not entries:
                return "(empty directory)"
            lines = []
            for entry in entries:
                kind = entry.get("kind", "?")
                label = f"{kind:6} {entry.get('name')}"
                if kind == "file" and entry.get("size") is not None:
                    label += f" ({entry['size']} bytes)"
                lines.append(label)
            return "\n".join(lines)

        raise RunnerError(f"no handler for tool \"{name}\"")


def _require_str(arguments: dict, key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RunnerError(f"\"{key}\" must be a non-empty string")
    return value


def _summarize(arguments: dict) -> dict:
    summary = {}
    for key, value in (arguments or {}).items():
        if isinstance(value, str) and len(value) > 300:
            summary[key] = value[:300] + "..."
        else:
            summary[key] = value
    return summary


def dumps(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
