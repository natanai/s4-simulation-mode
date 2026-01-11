import json
import os
from pathlib import Path
import tempfile


_TICK_MIN_SECONDS = 1
_TICK_MAX_SECONDS = 120


class SimulationModeSettings:
    def __init__(
        self,
        enabled=False,
        protect_motives=True,
        allow_pregnancy=False,
        allow_death=False,
        auto_unpause=True,
        auto_dialogs=False,
        tick_seconds=10,
        motive_floor=-60,
        motive_bump_to=-10,
    ):
        self.enabled = enabled
        self.protect_motives = protect_motives
        self.allow_pregnancy = allow_pregnancy
        self.allow_death = allow_death
        self.auto_unpause = auto_unpause
        self.auto_dialogs = auto_dialogs
        self.tick_seconds = tick_seconds
        self.motive_floor = motive_floor
        self.motive_bump_to = motive_bump_to


def get_config_path():
    home = Path(os.path.expanduser("~"))
    return (
        home
        / "Documents"
        / "Electronic Arts"
        / "The Sims 4"
        / "mod_data"
        / "simulation-mode"
        / "settings.json"
    )


def _clamp_tick(value):
    return max(_TICK_MIN_SECONDS, min(_TICK_MAX_SECONDS, int(value)))


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    return None


def load_settings(target):
    path = get_config_path()
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return

    if not isinstance(data, dict):
        return

    auto_unpause = _coerce_bool(data.get("auto_unpause"))
    if auto_unpause is not None:
        target.auto_unpause = auto_unpause

    auto_dialogs = _coerce_bool(data.get("auto_dialogs"))
    if auto_dialogs is not None:
        target.auto_dialogs = auto_dialogs

    allow_death = _coerce_bool(data.get("allow_death"))
    if allow_death is not None:
        target.allow_death = allow_death

    allow_pregnancy = _coerce_bool(data.get("allow_pregnancy"))
    if allow_pregnancy is not None:
        target.allow_pregnancy = allow_pregnancy

    tick_value = data.get("tick")
    if tick_value is not None:
        try:
            target.tick_seconds = _clamp_tick(tick_value)
        except Exception:
            pass


def save_settings(target):
    path = get_config_path()
    payload = {
        "auto_unpause": bool(target.auto_unpause),
        "auto_dialogs": bool(target.auto_dialogs),
        "allow_death": bool(target.allow_death),
        "allow_pregnancy": bool(target.allow_pregnancy),
        "tick": _clamp_tick(target.tick_seconds),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        os.replace(temp_name, path)
    except Exception:
        if temp_name:
            try:
                os.unlink(temp_name)
            except Exception:
                pass


settings = SimulationModeSettings()
load_settings(settings)
