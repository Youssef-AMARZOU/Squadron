"""Client for the compiled workspace-confined tool runner."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .config import REPO_ROOT

BINARY_NAME = "sqn-runner.exe" if os.name == "nt" else "sqn-runner"


class RunnerError(Exception):
    pass


class Runner:
    def __init__(self, root: Path, binary: Path | None = None) -> None:
        self.root = Path(root)
        self._binary = binary or self._discover_binary()

    @staticmethod
    def _discover_binary() -> Path | None:
        override = os.environ.get("SQN_RUNNER", "").strip()
        if override:
            return Path(override)
        built = REPO_ROOT / "runner" / "target" / "release" / BINARY_NAME
        if built.is_file():
            return built
        from_path = shutil.which("sqn-runner")
        return Path(from_path) if from_path else None

    @property
    def binary(self) -> Path:
        if self._binary is None or not self._binary.is_file():
            raise RunnerError(
                "tool runner binary not found; build it with: "
                "cargo build --release --manifest-path runner/Cargo.toml"
            )
        return self._binary

    def ensure(self) -> Path:
        if self._binary is not None and self._binary.is_file():
            return self._binary
        cargo = shutil.which("cargo")
        if not cargo:
            raise RunnerError("cargo not found; install Rust to build the tool runner")
        manifest = REPO_ROOT / "runner" / "Cargo.toml"
        completed = subprocess.run(
            [cargo, "build", "--release", "--manifest-path", str(manifest)],
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RunnerError(f"runner build failed:\n{completed.stderr[-2000:]}")
        self._binary = REPO_ROOT / "runner" / "target" / "release" / BINARY_NAME
        if not self._binary.is_file():
            raise RunnerError("runner build finished but the binary is missing")
        return self._binary

    def call(self, payload: dict, timeout_s: float | None = None) -> dict:
        body = json.dumps(payload)
        requested = payload.get("timeout_ms")
        if timeout_s is None:
            timeout_s = (float(requested) / 1000.0 + 30.0) if requested else 60.0
        try:
            completed = subprocess.run(
                [str(self.binary), str(self.root)],
                input=body,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as error:
            raise RunnerError("runner call timed out") from error
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[:500]
            raise RunnerError(f"runner exited with {completed.returncode}: {detail}")
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RunnerError(
                f"runner returned invalid JSON: {completed.stdout[:200]}"
            ) from error
        if not response.get("ok"):
            raise RunnerError(str(response.get("error", "runner error")))
        result = response.get("result")
        return result if isinstance(result, dict) else {"value": result}
