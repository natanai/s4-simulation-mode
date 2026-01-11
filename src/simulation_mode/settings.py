import os
from pathlib import Path

import sims4.log
import paths


_TICK_MIN_SECONDS = 1
_TICK_MAX_SECONDS = 120

logger = sims4.log.Logger("SimulationMode")


_DEFAULT_TEMPLATE = """# Simulation Mode (The Sims 4) — Settings
# Edit this file, then in-game run: simulation reload
enabled=false
auto_unpause=true
allow_death=false
allow_pregnancy=false
tick_seconds=5

# Self-Care Guardian (no cheating; pushes real interactions)
guardian_enabled=true
guardian_check_seconds=20
guardian_min_motive=-25
guardian_red_motive=-50
guardian_per_sim_cooldown_seconds=60
guardian_max_pushes_per_sim_per_hour=30

# Optional integration if you ALSO installed a mod that defines this trait ID
integrate_better_autonomy_trait=false
better_autonomy_trait_id=3985292068
"""


class SimulationModeSettings:
    def __init__(
        self,
        enabled=False,
        auto_unpause=True,
        allow_death=False,
        allow_pregnancy=False,
        tick_seconds=5,
        guardian_enabled=True,
        guardian_check_seconds=20,
        guardian_min_motive=-25,
        guardian_red_motive=-50,
        guardian_per_sim_cooldown_seconds=60,
        guardian_max_pushes_per_sim_per_hour=30,
        integrate_better_autonomy_trait=False,
        better_autonomy_trait_id=3985292068,
    ):
        self.enabled = enabled
        self.auto_unpause = auto_unpause
        self.allow_death = allow_death
        self.allow_pregnancy = allow_pregnancy
        self.tick_seconds = tick_seconds
        self.guardian_enabled = guardian_enabled
        self.guardian_check_seconds = guardian_check_seconds
        self.guardian_min_motive = guardian_min_motive
        self.guardian_red_motive = guardian_red_motive
        self.guardian_per_sim_cooldown_seconds = guardian_per_sim_cooldown_seconds
        self.guardian_max_pushes_per_sim_per_hour = guardian_max_pushes_per_sim_per_hour
        self.integrate_better_autonomy_trait = integrate_better_autonomy_trait
        self.better_autonomy_trait_id = better_autonomy_trait_id


def _get_user_mods_path():
    base = getattr(paths, "USER_MODS_PATH", None)
    if base is None:
        user_data = getattr(paths, "USER_DATA_PATH")
        base = os.path.join(user_data, "Mods")
    return os.fspath(base)


def get_config_path():
    base = _get_user_mods_path()
    config_dir = Path(base) / "SimulationMode"
    os.makedirs(config_dir, exist_ok=True)
    return config_dir / "simulation-mode.txt"


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


def _parse_value(value):
    bool_value = _coerce_bool(value)
    if bool_value is not None:
        return bool_value
    try:
        return int(value)
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return value


def _ensure_template(path: Path):
    if path.exists():
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            handle.write(_DEFAULT_TEMPLATE)
        return True
    except Exception as exc:
        logger.warn(f"Failed to write default settings template: {exc}")
        return False


def _strip_inline_comment(line: str):
    for index, char in enumerate(line):
        if char == "#" and index > 0 and line[index - 1].isspace():
            return line[:index].rstrip()
    return line


def _parse_lines(lines):
    parsed = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith(";"):
            continue
        content = _strip_inline_comment(raw_line).strip()
        if not content:
            continue
        if "=" not in content:
            continue
        key, value = content.split("=", 1)
        key = key.strip().lower()
        if not key:
            continue
        parsed[key] = value.strip()
    return parsed


_UNKNOWN_KEYS_LOGGED = set()


def _log_unknown_key(key):
    if key in _UNKNOWN_KEYS_LOGGED:
        return
    _UNKNOWN_KEYS_LOGGED.add(key)
    logger.warn(f"Unknown settings key ignored: {key}")


def _log_invalid_value(key, value):
    logger.warn(f"Invalid value for {key}: {value}")


def load_settings(target):
    path = get_config_path()
    _ensure_template(path)
    if not path.exists():
        return
    try:
        contents = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warn(f"Failed to read settings file: {exc}")
        return

    data = _parse_lines(contents.splitlines())
    for key, raw_value in data.items():
        value = _parse_value(raw_value)
        try:
            if key == "enabled":
                if isinstance(value, bool):
                    target.enabled = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "auto_unpause":
                if isinstance(value, bool):
                    target.auto_unpause = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "allow_death":
                if isinstance(value, bool):
                    target.allow_death = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "allow_pregnancy":
                if isinstance(value, bool):
                    target.allow_pregnancy = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "tick_seconds":
                try:
                    target.tick_seconds = _clamp_tick(value)
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "guardian_enabled":
                if isinstance(value, bool):
                    target.guardian_enabled = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "guardian_check_seconds":
                try:
                    target.guardian_check_seconds = max(1, int(value))
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "guardian_min_motive":
                try:
                    target.guardian_min_motive = int(value)
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "guardian_red_motive":
                try:
                    target.guardian_red_motive = int(value)
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "guardian_per_sim_cooldown_seconds":
                try:
                    target.guardian_per_sim_cooldown_seconds = max(0, int(value))
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "guardian_max_pushes_per_sim_per_hour":
                try:
                    target.guardian_max_pushes_per_sim_per_hour = max(0, int(value))
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "integrate_better_autonomy_trait":
                if isinstance(value, bool):
                    target.integrate_better_autonomy_trait = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "better_autonomy_trait_id":
                try:
                    target.better_autonomy_trait_id = int(value)
                except Exception:
                    _log_invalid_value(key, raw_value)
            else:
                _log_unknown_key(key)
        except Exception as exc:
            logger.warn(f"Failed to apply setting {key}: {exc}")


def save_settings(_target):
    path = get_config_path()
    _ensure_template(path)


settings = SimulationModeSettings()
load_settings(settings)
