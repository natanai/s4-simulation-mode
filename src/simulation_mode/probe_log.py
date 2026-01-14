import os
import time

from simulation_mode.settings import get_config_path

probe_log_error = None


def get_probe_log_path():
    cfg = os.path.abspath(get_config_path())
    folder = os.path.dirname(cfg)
    return os.path.join(folder, "simulation-mode-probe.log")


def _prepare_probe_log_path():
    global probe_log_error
    path = get_probe_log_path()
    if not path:
        probe_log_error = "probe_log_path is empty; skipping probe log write"
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def append_probe_line(line: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    path = _prepare_probe_log_path()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {line}\n")


def log_probe(line: str):
    append_probe_line(line)


def append_probe_block(title, lines):
    if title:
        block_lines = [str(title)]
        if lines:
            block_lines.extend(lines)
    else:
        block_lines = list(lines or [])
    if not block_lines:
        return
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    path = _prepare_probe_log_path()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(f"[{timestamp}] {line}" for line in block_lines))
        handle.write("\n")


def probe_clear():
    path = _prepare_probe_log_path()
    if not path:
        return
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("")
