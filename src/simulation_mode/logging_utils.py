from collections import deque
import os
import time

from simulation_mode.settings import get_config_path

_LOG_BUFFER = deque(maxlen=500)


def append_line(line: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    _LOG_BUFFER.append(f"[{timestamp}] {line}")


def append_lines(lines):
    for line in lines:
        append_line(line)


def get_lines():
    return list(_LOG_BUFFER)


def append_log_block(filename: str, header: str, body: str) -> str:
    cfg = os.path.abspath(get_config_path())
    folder = os.path.dirname(cfg)
    path = os.path.join(folder, filename)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "=" * 60,
        f"{header} @ {timestamp}",
    ]
    if body:
        lines.append(body.rstrip("\n"))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
        handle.write("\n")
    return path
