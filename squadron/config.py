"""Runtime configuration for Squadron."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PROVIDER_ENV_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def load_env_file(path: Path, override: bool = False) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and (override or key not in os.environ):
            os.environ[key] = value


def load_env() -> None:
    # The project .env is the authoritative local configuration and wins over
    # inherited environment variables; extra files follow the usual rule of
    # never overriding what is already set.
    load_env_file(REPO_ROOT / ".env", override=True)
    extra = os.environ.get("SQUADRON_ENV_FILE", "").strip()
    if extra:
        load_env_file(Path(extra), override=False)


def detect_provider(explicit: str | None) -> str:
    requested = (explicit or os.environ.get("SQUADRON_PROVIDER") or "").strip().lower()
    if requested:
        if requested not in PROVIDER_ENV_KEYS:
            raise ConfigError(
                f"unknown provider \"{requested}\" (expected one of: "
                f"{', '.join(PROVIDER_ENV_KEYS)})"
            )
        return requested
    for provider, key in PROVIDER_ENV_KEYS.items():
        if os.environ.get(key, "").strip():
            return provider
    raise ConfigError(
        "no provider credentials found; set one of: "
        + ", ".join(PROVIDER_ENV_KEYS.values())
    )


@dataclass
class Config:
    provider: str
    model: str
    api_key: str
    workspace: Path
    max_steps: int = 24
    max_tokens: int = 4096
    log_dir: Path = field(default_factory=lambda: Path(".squadron"))
    runner_binary: Path | None = None

    @property
    def env_key(self) -> str:
        return PROVIDER_ENV_KEYS[self.provider]


class ConfigError(Exception):
    pass


def load_config(
    provider: str | None = None,
    model: str | None = None,
    workspace: str | None = None,
    max_steps: int | None = None,
    log_dir: str | None = None,
    runner: str | None = None,
) -> Config:
    load_env()

    resolved_provider = detect_provider(provider)
    api_key = os.environ.get(PROVIDER_ENV_KEYS[resolved_provider], "").strip()
    if not api_key:
        raise ConfigError(
            f"{PROVIDER_ENV_KEYS[resolved_provider]} is not set for provider "
            f"{resolved_provider}"
        )

    resolved_model = (model or os.environ.get("SQUADRON_MODEL", "")).strip()
    if not resolved_model:
        raise ConfigError(
            "no model configured; pass --model or set SQUADRON_MODEL"
        )

    steps = max_steps or int(os.environ.get("SQUADRON_MAX_STEPS", "24") or 24)
    tokens = int(os.environ.get("SQUADRON_MAX_TOKENS", "4096") or 4096)
    workspace_path = Path(workspace or os.getcwd()).resolve()
    if not workspace_path.is_dir():
        raise ConfigError(f"workspace does not exist: {workspace_path}")

    log_path = Path(log_dir).resolve() if log_dir else workspace_path / ".squadron"
    binary = Path(runner).resolve() if runner else None

    return Config(
        provider=resolved_provider,
        model=resolved_model,
        api_key=api_key,
        workspace=workspace_path,
        max_steps=max(1, steps),
        max_tokens=max(256, tokens),
        log_dir=log_path,
        runner_binary=binary,
    )
