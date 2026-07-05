"""Tail the demo service's JSON-lines log file (shared docker volume)."""

from pathlib import Path

MAX_LINES = 200


class ServiceLogs:
    def __init__(self, log_path: str):
        self._path = Path(log_path)

    def tail(self, lines: int = 100) -> str:
        lines = max(1, min(int(lines), MAX_LINES))
        if not self._path.exists():
            return f"(log file not found at {self._path})"
        content = self._path.read_text(errors="replace").splitlines()
        tail = content[-lines:]
        return "\n".join(tail) if tail else "(log file is empty)"
