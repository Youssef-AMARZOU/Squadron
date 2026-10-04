"""Command line entry point for Squadron."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import REPO_ROOT, ConfigError, load_config
from .crew import Crew
from .events import EventLog
from .providers import ProviderError
from .runner import Runner, RunnerError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="squadron",
        description=(
            "Run a lead agent that directs a team of specialist sub-agents "
            "on a task inside a workspace."
        ),
    )
    parser.add_argument("task", help="The task the crew should accomplish")
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai", "gemini"],
        help="Model provider (default: detected from available credentials)",
    )
    parser.add_argument("--model", help="Model identifier for the provider")
    parser.add_argument(
        "--workspace",
        default=os.getcwd(),
        help="Directory the crew is allowed to work in (default: current directory)",
    )
    parser.add_argument("--max-steps", type=int, help="Maximum loop steps per agent")
    parser.add_argument("--log-dir", help="Directory for the JSONL event log")
    parser.add_argument("--runner", help="Path to the sqn-runner binary")
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Start the status dashboard after the run starts",
    )
    parser.add_argument(
        "--dashboard-port", type=int, default=8787, help="Dashboard port (default 8787)"
    )
    parser.add_argument("--version", action="version", version=f"squadron {__version__}")
    return parser


def start_dashboard(log_path: Path, port: int) -> None:
    script = REPO_ROOT / "dashboard" / "src" / "server.ts"
    if not script.is_file():
        print(f"[squadron] dashboard script missing at {script}", file=sys.stderr)
        return
    try:
        subprocess.Popen(
            ["node", str(script), "--log", str(log_path), "--port", str(port)],
            cwd=str(REPO_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        print(f"[squadron] could not start dashboard: {error}", file=sys.stderr)
        return
    print(f"[squadron] dashboard: http://localhost:{port}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = load_config(
            provider=args.provider,
            model=args.model,
            workspace=args.workspace,
            max_steps=args.max_steps,
            log_dir=args.log_dir,
            runner=args.runner,
        )
    except ConfigError as error:
        print(f"[squadron] configuration error: {error}", file=sys.stderr)
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.log_dir:
        run_dir = Path(args.log_dir).resolve()
    else:
        run_dir = config.log_dir / "runs" / stamp
    events = EventLog(run_dir / "events.jsonl")

    print(f"squadron {__version__}")
    print(f"provider : {config.provider} (model {config.model})")
    print(f"workspace: {config.workspace}")
    print(f"events   : {events.path}")
    print("-" * 60)

    runner = Runner(config.workspace, config.runner_binary)
    crew = Crew(config, runner, events)

    try:
        runner.ensure()
        report = crew.run(args.task)
    except (ProviderError, RunnerError) as error:
        events.emit("error", "lead", message=str(error)[:800])
        events.close()
        print(f"[squadron] {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        events.emit("interrupted", "lead")
        events.close()
        print("\n[squadron] interrupted", file=sys.stderr)
        return 130
    finally:
        events.close()

    print("-" * 60)
    print(report)
    print("-" * 60)
    print(f"[squadron] event log: {events.path}")

    if args.dashboard:
        start_dashboard(events.path, args.dashboard_port)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
