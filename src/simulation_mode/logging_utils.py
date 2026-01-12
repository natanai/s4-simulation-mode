from collections import deque
import time

_LOG_BUFFER = deque(maxlen=500)


def append_line(line: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    _LOG_BUFFER.append(f"[{timestamp}] {line}")


def append_lines(lines):
    for line in lines:
        append_line(line)


def get_lines():
    return list(_LOG_BUFFER)
