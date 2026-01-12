import os
from pathlib import Path
import traceback

import sims4.log


_TICK_MIN_SECONDS = 1
_TICK_MAX_SECONDS = 120

logger = sims4.log.Logger("SimulationMode")


KNOWN_DEFAULTS = [
    ("enabled", "false"),
    ("auto_unpause", "true"),
    ("allow_death", "false"),
    ("allow_pregnancy", "false"),
    ("tick_seconds", "5"),
    ("guardian_enabled", "true"),
    ("guardian_check_seconds", "20"),
    ("guardian_min_motive", "-25"),
    ("guardian_red_motive", "-50"),
    ("guardian_per_sim_cooldown_seconds", "60"),
    ("guardian_max_pushes_per_sim_per_hour", "30"),
    ("director_enabled", "true"),
    ("director_check_seconds", "90"),
    ("director_min_safe_motive", "-10"),
    ("director_green_motive_percent", "0.50"),
    ("director_green_min_commodities", "6"),
    ("director_allow_social_goals", "false"),
    ("director_allow_social_wants", "true"),
    ("director_use_guardian_when_low", "true"),
    ("director_per_sim_cooldown_seconds", "300"),
    ("director_max_pushes_per_sim_per_hour", "12"),
    ("director_prefer_career_skills", "true"),
    ("director_fallback_to_started_skills", "true"),
    ("director_skill_allow_list", ""),
    ("director_skill_block_list", ""),
    ("integrate_better_autonomy_trait", "false"),
    ("better_autonomy_trait_id", "3985292068"),
]


def _build_default_template_text():
    defaults = dict(KNOWN_DEFAULTS)
    lines = []
    lines.append("# Simulation Mode (The Sims 4) — Settings")
    lines.append("# Edit this file, then in-game run: simulation reload")
    lines.append("")
    lines.append("enabled={}".format(defaults["enabled"]))
    lines.append("auto_unpause={}".format(defaults["auto_unpause"]))
    lines.append("allow_death={}".format(defaults["allow_death"]))
    lines.append("allow_pregnancy={}".format(defaults["allow_pregnancy"]))
    lines.append("tick_seconds={}".format(defaults["tick_seconds"]))
    lines.append("")
    lines.append("# Self-Care Guardian (no cheating; pushes real interactions)")
    lines.append("guardian_enabled={}".format(defaults["guardian_enabled"]))
    lines.append("guardian_check_seconds={}".format(defaults["guardian_check_seconds"]))
    lines.append("guardian_min_motive={}".format(defaults["guardian_min_motive"]))
    lines.append("guardian_red_motive={}".format(defaults["guardian_red_motive"]))
    lines.append(
        "guardian_per_sim_cooldown_seconds={}".format(
            defaults["guardian_per_sim_cooldown_seconds"]
        )
    )
    lines.append(
        "guardian_max_pushes_per_sim_per_hour={}".format(
            defaults["guardian_max_pushes_per_sim_per_hour"]
        )
    )
    lines.append("")
    lines.append("# Life Director (skill progression; no cheating; pushes real interactions)")
    lines.append("director_enabled={}".format(defaults["director_enabled"]))
    lines.append("director_check_seconds={}".format(defaults["director_check_seconds"]))
    lines.append("director_min_safe_motive={}".format(defaults["director_min_safe_motive"]))
    lines.append(
        "director_green_motive_percent={}".format(defaults["director_green_motive_percent"])
    )
    lines.append(
        "director_green_min_commodities={}".format(defaults["director_green_min_commodities"])
    )
    lines.append(
        "director_allow_social_goals={}".format(defaults["director_allow_social_goals"])
    )
    lines.append(
        "director_allow_social_wants={}".format(defaults["director_allow_social_wants"])
    )
    lines.append(
        "director_use_guardian_when_low={}".format(defaults["director_use_guardian_when_low"])
    )
    lines.append(
        "director_per_sim_cooldown_seconds={}".format(
            defaults["director_per_sim_cooldown_seconds"]
        )
    )
    lines.append(
        "director_max_pushes_per_sim_per_hour={}".format(
            defaults["director_max_pushes_per_sim_per_hour"]
        )
    )
    lines.append(
        "director_prefer_career_skills={}".format(
            defaults["director_prefer_career_skills"]
        )
    )
    lines.append(
        "director_fallback_to_started_skills={}".format(
            defaults["director_fallback_to_started_skills"]
        )
    )
    lines.append("director_skill_allow_list={}".format(defaults["director_skill_allow_list"]))
    lines.append("director_skill_block_list={}".format(defaults["director_skill_block_list"]))
    lines.append("")
    lines.append(
        "# Optional integration if you ALSO installed a mod that defines this trait ID"
    )
    lines.append(
        "integrate_better_autonomy_trait={}".format(
            defaults["integrate_better_autonomy_trait"]
        )
    )
    lines.append("better_autonomy_trait_id={}".format(defaults["better_autonomy_trait_id"]))
    lines.append("")
    return "\n".join(lines)


_DEFAULT_TEMPLATE = _build_default_template_text()


def _read_existing_keys(path):
    keys = set()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith(";"):
                    continue
                if "=" not in line:
                    continue
                key = line.split("=", 1)[0].strip()
                if key:
                    keys.add(key)
    except Exception:
        pass
    return keys


def _append_missing_keys(path):
    existing = _read_existing_keys(path)
    missing = []
    for key, default_str in KNOWN_DEFAULTS:
        if key not in existing:
            missing.append((key, default_str))
    if not missing:
        return

    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n")
            handle.write("# --- Added by SimulationMode upgrade (missing keys) ---\n")
            any_director = any(key.startswith("director_") for key, _ in missing)
            any_guardian = any(key.startswith("guardian_") for key, _ in missing)
            any_core = any(
                key
                in (
                    "enabled",
                    "auto_unpause",
                    "allow_death",
                    "allow_pregnancy",
                    "tick_seconds",
                )
                for key, _ in missing
            )
            any_trait = any(
                key.startswith("integrate_") or key.startswith("better_autonomy_")
                for key, _ in missing
            )

            if any_core:
                handle.write("# Core\n")
                for key, value in missing:
                    if key in (
                        "enabled",
                        "auto_unpause",
                        "allow_death",
                        "allow_pregnancy",
                        "tick_seconds",
                    ):
                        handle.write("{}={}\n".format(key, value))
                handle.write("\n")

            if any_guardian:
                handle.write("# Self-Care Guardian (no cheating; pushes real interactions)\n")
                for key, value in missing:
                    if key.startswith("guardian_"):
                        handle.write("{}={}\n".format(key, value))
                handle.write("\n")

            if any_director:
                handle.write(
                    "# Life Director (skill progression; no cheating; pushes real interactions)\n"
                )
                for key, value in missing:
                    if key.startswith("director_"):
                        handle.write("{}={}\n".format(key, value))
                handle.write("\n")

            if any_trait:
                handle.write(
                    "# Optional integration if you ALSO installed a mod that defines this trait ID\n"
                )
                for key, value in missing:
                    if key.startswith("integrate_") or key.startswith("better_autonomy_"):
                        handle.write("{}={}\n".format(key, value))
                handle.write("\n")
    except Exception:
        return


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
        director_enabled=True,
        director_check_seconds=90,
        director_min_safe_motive=-10,
        director_green_motive_percent=0.5,
        director_green_min_commodities=6,
        director_allow_social_goals=False,
        director_allow_social_wants=True,
        director_use_guardian_when_low=True,
        director_per_sim_cooldown_seconds=300,
        director_max_pushes_per_sim_per_hour=12,
        director_prefer_career_skills=True,
        director_fallback_to_started_skills=True,
        director_skill_allow_list=None,
        director_skill_block_list=None,
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
        self.director_enabled = director_enabled
        self.director_check_seconds = director_check_seconds
        self.director_min_safe_motive = director_min_safe_motive
        self.director_green_motive_percent = director_green_motive_percent
        self.director_green_min_commodities = director_green_min_commodities
        self.director_allow_social_goals = director_allow_social_goals
        self.director_allow_social_wants = director_allow_social_wants
        self.director_use_guardian_when_low = director_use_guardian_when_low
        self.director_per_sim_cooldown_seconds = director_per_sim_cooldown_seconds
        self.director_max_pushes_per_sim_per_hour = director_max_pushes_per_sim_per_hour
        self.director_prefer_career_skills = director_prefer_career_skills
        self.director_fallback_to_started_skills = director_fallback_to_started_skills
        self.director_skill_allow_list = director_skill_allow_list or []
        self.director_skill_block_list = director_skill_block_list or []
        self.integrate_better_autonomy_trait = integrate_better_autonomy_trait
        self.better_autonomy_trait_id = better_autonomy_trait_id


def _get_user_mod_subfolder_path():
    try:
        import paths
        base = getattr(paths, "USER_MODS_PATH", None)
        if base:
            return os.path.join(os.fspath(base), "SimulationMode")
    except Exception:
        pass

    try:
        here = os.path.abspath(__file__)
        return os.path.dirname(os.path.dirname(os.path.dirname(here)))
    except Exception:
        return os.getcwd()


def get_config_path():
    base = _get_user_mod_subfolder_path()
    return os.path.join(base, "simulation-mode.txt")


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


def _parse_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    text = str(value)
    return [item.strip().lower() for item in text.split(",") if item.strip()]


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


def _log_exception(message: str):
    try:
        logger.exception(message)
    except Exception:
        try:
            logger.warn(f"{message}: {traceback.format_exc()}")
        except Exception:
            pass


def load_settings(target):
    path = Path(get_config_path())
    if not path.exists():
        _ensure_template(path)
    else:
        _append_missing_keys(path)
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
            elif key == "director_enabled":
                if isinstance(value, bool):
                    target.director_enabled = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "director_check_seconds":
                try:
                    target.director_check_seconds = max(1, int(value))
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "director_min_safe_motive":
                try:
                    target.director_min_safe_motive = int(value)
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "director_green_motive_percent":
                try:
                    target.director_green_motive_percent = max(0.0, min(1.0, float(value)))
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "director_green_min_commodities":
                try:
                    target.director_green_min_commodities = max(0, int(value))
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "director_allow_social_goals":
                if isinstance(value, bool):
                    target.director_allow_social_goals = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "director_allow_social_wants":
                if isinstance(value, bool):
                    target.director_allow_social_wants = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "director_use_guardian_when_low":
                if isinstance(value, bool):
                    target.director_use_guardian_when_low = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "director_per_sim_cooldown_seconds":
                try:
                    target.director_per_sim_cooldown_seconds = max(0, int(value))
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "director_max_pushes_per_sim_per_hour":
                try:
                    target.director_max_pushes_per_sim_per_hour = max(0, int(value))
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "director_prefer_career_skills":
                if isinstance(value, bool):
                    target.director_prefer_career_skills = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "director_fallback_to_started_skills":
                if isinstance(value, bool):
                    target.director_fallback_to_started_skills = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "director_skill_allow_list":
                target.director_skill_allow_list = _parse_list(raw_value)
            elif key == "director_skill_block_list":
                target.director_skill_block_list = _parse_list(raw_value)
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
    path = Path(get_config_path())
    _ensure_template(path)


settings = SimulationModeSettings()
try:
    load_settings(settings)
except Exception:
    _log_exception("Settings load failed; continuing with defaults")
