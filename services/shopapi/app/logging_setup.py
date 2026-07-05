"""JSON-lines logging to a shared file (read by the SRE agent) and stdout."""

import json
import os
import sys
import threading
from datetime import datetime, timezone

LOG_PATH = os.environ.get("LOG_PATH", "/var/log/shopapi/app.log")

_lock = threading.Lock()
_file = None


def init() -> None:
    global _file
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        # Truncate on start: demo runs are short, no rotation needed.
        _file = open(LOG_PATH, "w", buffering=1)
    except OSError:
        _file = None  # running outside docker (e.g. tests) — stdout only


def log(level: str, **fields) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "level": level,
        **fields,
    }
    line = json.dumps(record)
    with _lock:
        print(line, file=sys.stdout, flush=True)
        if _file is not None:
            _file.write(line + "\n")
