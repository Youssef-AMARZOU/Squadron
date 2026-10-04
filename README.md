# Squadron

A lead agent that directs a team of specialist sub-agents inside a single
workspace. You hand the crew one task; the lead plans, delegates focused
assignments to specialists in parallel, collects their reports, and returns a
final summary with verification results.

Authored by **Youssef AMARZOU**.

## Architecture

| Component | Language | Responsibility |
|-----------|----------|----------------|
| `squadron/` | Python | Agent loop, lead/sub-agent orchestration, provider adapters, event stream |
| `runner/` | Rust | `sqn-runner`: executes commands and file operations under workspace confinement |
| `dashboard/` | TypeScript | `server.ts`: live status dashboard over the JSONL event log (zero dependencies) |

The three components talk over simple boundaries: the Python layer invokes the
Rust binary with one JSON request on stdin and reads one JSON response on
stdout; the dashboard tails the JSONL event file the Python layer appends to.

## How a run works

1. The CLI starts a **lead** agent with the task.
2. The lead replies with `spawn_subagent` calls; each call runs a specialist
   agent in its own thread with its own system prompt and tool set.
3. Specialists use the Rust runner to read and write files and to execute
   commands, all confined to the workspace directory.
4. Each specialist's final report returns to the lead as a tool result.
5. The lead iterates until the goal is verifiably done, then prints the final
   report. Every step is appended to an event log.

## Roles

| Role | Tools | Job |
|------|-------|-----|
| `lead` | read, list, spawn | Plans, delegates, synthesizes the final report |
| `researcher` | read, list, run | Read-only reconnaissance of the workspace |
| `coder` | read, write, list, run | Creates and edits files |
| `tester` | run, read, list | Executes checks and reports evidence |
| `reviewer` | read, list | Judges correctness of produced files |

## Requirements

- Python 3.10+
- Rust toolchain (`cargo`) to build the runner
- Node.js 24+ to run the TypeScript dashboard directly (no install step)
- Credentials for one provider: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  or `GEMINI_API_KEY`

## Build

```bash
cargo build --release --manifest-path runner/Cargo.toml
```

The Python layer builds the runner automatically if it is missing.

## Configure

```bash
cp .env.example .env
```

Fill in one provider key and a model identifier:

| Variable | Meaning |
|----------|---------|
| `SQUADRON_PROVIDER` | `anthropic`, `openai`, or `gemini` (auto-detected from keys if unset) |
| `SQUADRON_MODEL` | Model identifier available to your account (required) |
| `SQUADRON_MAX_STEPS` | Loop steps per agent (default 24) |
| `SQUADRON_MAX_TOKENS` | Max tokens per request for providers that require it (default 4096) |
| `SQUADRON_ENV_FILE` | Optional path to an extra env file to load |

The project `.env` is the authoritative local configuration: its values win
over inherited environment variables. Files referenced by `SQUADRON_ENV_FILE`
follow the usual rule and never override what is already set.

## Usage

```bash
python -m squadron "Create hello.py that prints Squadron online and verify it runs"
```

```bash
python -m squadron "Audit the scripts folder and report dead code" \
  --workspace "D:\some\project" \
  --provider anthropic --model <model-id> \
  --dashboard --dashboard-port 8787
```

| Flag | Meaning |
|------|---------|
| `--provider` | Force a provider instead of auto-detection |
| `--model` | Model identifier (overrides `SQUADRON_MODEL`) |
| `--workspace` | Directory the crew may touch (default: current directory) |
| `--max-steps` | Loop steps per agent |
| `--log-dir` | Where to write the event log |
| `--runner` | Path to a prebuilt `sqn-runner` binary |
| `--dashboard` | Start the status dashboard alongside the run |
| `--dashboard-port` | Dashboard port (default 8787) |

Each run writes `<workspace>/.squadron/runs/<timestamp>/events.jsonl`.

## Status dashboard

```bash
node dashboard/src/server.ts --log <path-to-events.jsonl> --port 8787
```

Then open `http://localhost:8787`. The page shows the agent roster, per-agent
state, and a live feed of assistant turns, tool calls, and sub-agent
lifecycle events.

## Tests

```bash
python -m unittest discover -s tests -v
```

The suite covers the runner (execution, timeouts, path confinement), the
single-agent loop, and lead/sub-agent delegation including parallel spawns.
Tests run against scripted providers, so no network access is needed.

## Safety model

- File operations are resolved against the workspace root; `..` traversal and
  absolute paths outside the root are rejected by the runner.
- Commands run with a timeout and bounded output capture; they are killed when
  the deadline passes.
- The runner never executes anything outside the spawned command path; it does
  not implement OS-level sandboxing, so do not point it at untrusted tasks.

## License

MIT — Copyright (c) 2026 Youssef AMARZOU. See [LICENSE](LICENSE).
