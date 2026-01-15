import json
import os
import time

from simulation_mode.settings import get_config_path, settings


def get_story_log_path():
    try:
        cfg = os.path.abspath(get_config_path())
        folder = os.path.dirname(cfg)
        filename = settings.story_log_filename or "simulation-mode-story.log"
        return os.path.join(folder, filename)
    except Exception:
        return ""


def _prepare_story_log_path():
    try:
        path = get_story_log_path()
        if not path:
            return None
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path
    except Exception:
        return None


def _format_sim_name(sim_info):
    if sim_info is None:
        return None
    try:
        name = getattr(sim_info, "first_name", None)
        if name:
            return str(name).strip()
        full_name = getattr(sim_info, "full_name", None)
        if callable(full_name):
            name = full_name()
            if name:
                return str(name).strip()
        return str(sim_info).strip()
    except Exception:
        return None


def append_event(event_type: str, sim_info=None, **details):
    try:
        if not settings.story_log_enabled:
            return
        path = _prepare_story_log_path()
        if not path:
            return
        sim_id = None
        if sim_info is not None:
            sim_id = getattr(sim_info, "sim_id", None) or id(sim_info)
        payload = {
            "ts": time.time(),
            "type": event_type,
            "sim_id": sim_id,
            "sim_name": _format_sim_name(sim_info),
            "details": details,
        }
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")
    except Exception:
        return


def tail(n=20):
    try:
        path = get_story_log_path()
        if not path or not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
        return [line.rstrip("\n") for line in lines[-n:]]
    except Exception:
        return []


def clear():
    try:
        path = _prepare_story_log_path()
        if not path:
            return
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("")
    except Exception:
        return
