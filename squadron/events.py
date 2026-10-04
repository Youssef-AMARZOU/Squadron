"""Append-only JSONL event stream consumed by the dashboard."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class EventLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._handle = self.path.open("a", encoding="utf-8")

    def emit(self, kind: str, agent: str, **data: object) -> None:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "kind": kind,
            "agent": agent,
            "data": data,
        }
        line = json.dumps(row, ensure_ascii=False)
        with self._lock:
            self._handle.write(line + "\n")
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            if not self._handle.closed:
                self._handle.close()

    def __enter__(self) -> "EventLog":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
