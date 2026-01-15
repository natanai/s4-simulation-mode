import os
import re
from pathlib import Path
import traceback

import sims4.log


_TICK_MIN_SECONDS = 1
_TICK_MAX_SECONDS = 120

logger = sims4.log.Logger("SimulationMode")


KNOWN_DEFAULTS = [
    ("enabled", "true"),
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
    ("director_enable_wants", "false"),
    ("director_use_guardian_when_low", "true"),
    ("director_per_sim_cooldown_seconds", "300"),
    ("director_max_pushes_per_sim_per_hour", "12"),
    ("director_prefer_career_skills", "true"),
    ("director_fallback_to_started_skills", "true"),
    ("director_skill_allow_list", ""),
    ("director_skill_block_list", ""),
    ("collect_log_filename", "simulation-mode-collect.log"),
    ("story_log_enabled", "true"),
    ("story_log_filename", "simulation-mode-story.log"),
    ("capabilities_filename", "simulation-mode-capabilities.json"),
    ("capabilities_auto_build_on_enable", "true"),
    ("catalog_include_sims", "false"),
    ("catalog_include_non_autonomous", "false"),
    ("catalog_max_records", "50000"),
    ("catalog_max_objects", "2000"),
    ("catalog_max_affordances_per_object", "200"),
    ("catalog_collect_sample_objects", "150"),
    ("catalog_collect_sample_affordances_per_object", "60"),
    ("catalog_collect_top_auto_n", "40"),
    ("aff_meta_substrings", ""),
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
        "director_enable_wants={}".format(defaults["director_enable_wants"])
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
    lines.append("collect_log_filename={}".format(defaults["collect_log_filename"]))
    lines.append("story_log_enabled={}".format(defaults["story_log_enabled"]))
    lines.append("story_log_filename={}".format(defaults["story_log_filename"]))
    lines.append("capabilities_filename={}".format(defaults["capabilities_filename"]))
    lines.append(
        "capabilities_auto_build_on_enable={}".format(
            defaults["capabilities_auto_build_on_enable"]
        )
    )
    lines.append("")
    lines.append("# catalog defaults (Build 56)")
    lines.append("catalog_include_sims={}".format(defaults["catalog_include_sims"]))
    lines.append(
        "catalog_include_non_autonomous={}".format(
            defaults["catalog_include_non_autonomous"]
        )
    )
    lines.append("catalog_max_records={}".format(defaults["catalog_max_records"]))
    lines.append("catalog_max_objects={}".format(defaults["catalog_max_objects"]))
    lines.append(
        "catalog_max_affordances_per_object={}".format(
            defaults["catalog_max_affordances_per_object"]
        )
    )
    lines.append("")
    lines.append("# collect-integrated sampling caps (Build 56)")
    lines.append(
        "catalog_collect_sample_objects={}".format(
            defaults["catalog_collect_sample_objects"]
        )
    )
    lines.append(
        "catalog_collect_sample_affordances_per_object={}".format(
            defaults["catalog_collect_sample_affordances_per_object"]
        )
    )
    lines.append(
        "catalog_collect_top_auto_n={}".format(defaults["catalog_collect_top_auto_n"])
    )
    lines.append("")
    lines.append("# affordance meta probe list (pipe-delimited)")
    lines.append("aff_meta_substrings={}".format(defaults["aff_meta_substrings"]))
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
        lines_to_append = []
        core_keys = {
            "enabled",
            "auto_unpause",
            "allow_death",
            "allow_pregnancy",
            "tick_seconds",
        }
        story_keys = {
            "story_log_enabled",
            "story_log_filename",
            "collect_log_filename",
        }
        capability_keys = {
            "capabilities_filename",
            "capabilities_auto_build_on_enable",
        }
        catalog_defaults = {
            "catalog_include_sims",
            "catalog_include_non_autonomous",
            "catalog_max_records",
            "catalog_max_objects",
            "catalog_max_affordances_per_object",
        }
        collect_caps = {
            "catalog_collect_sample_objects",
            "catalog_collect_sample_affordances_per_object",
            "catalog_collect_top_auto_n",
        }
        aff_meta = {"aff_meta_substrings"}

        handled = set()

        def _append_group(header, predicate):
            added = False
            for key, value in missing:
                if key in handled:
                    continue
                if predicate(key):
                    if not added:
                        lines_to_append.append(header)
                        added = True
                    lines_to_append.append(f"{key}={value}")
                    handled.add(key)
            if added:
                lines_to_append.append("")

        _append_group("# Core", lambda key: key in core_keys)
        _append_group(
            "# Self-Care Guardian (no cheating; pushes real interactions)",
            lambda key: key.startswith("guardian_"),
        )
        _append_group(
            "# Life Director (skill progression; no cheating; pushes real interactions)",
            lambda key: key.startswith("director_"),
        )
        _append_group("# Logging", lambda key: key in story_keys)
        _append_group("# Capability kernel", lambda key: key in capability_keys)
        _append_group(
            "# Optional integration if you ALSO installed a mod that defines this trait ID",
            lambda key: key.startswith("integrate_") or key.startswith("better_autonomy_"),
        )
        _append_group("# catalog defaults (Build 56)", lambda key: key in catalog_defaults)
        _append_group(
            "# collect-integrated sampling caps (Build 56)", lambda key: key in collect_caps
        )
        _append_group(
            "# affordance meta probe list (pipe-delimited)", lambda key: key in aff_meta
        )

        misc = [(key, value) for key, value in missing if key not in handled]
        if misc:
            lines_to_append.append("# Misc")
            for key, value in misc:
                lines_to_append.append(f"{key}={value}")
                handled.add(key)
            lines_to_append.append("")

        if not lines_to_append:
            return

        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n")
            handle.write("# --- Added by SimulationMode upgrade (missing keys) ---\n")
            handle.write("\n".join(lines_to_append).rstrip())
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
        director_enable_wants=False,
        director_use_guardian_when_low=True,
        director_per_sim_cooldown_seconds=300,
        director_max_pushes_per_sim_per_hour=12,
        director_prefer_career_skills=True,
        director_fallback_to_started_skills=True,
        director_skill_allow_list=None,
        director_skill_block_list=None,
        collect_log_filename="simulation-mode-collect.log",
        story_log_enabled=True,
        story_log_filename="simulation-mode-story.log",
        capabilities_filename="simulation-mode-capabilities.json",
        capabilities_auto_build_on_enable=True,
        catalog_include_sims=False,
        catalog_include_non_autonomous=False,
        catalog_max_records=50000,
        catalog_max_objects=2000,
        catalog_max_affordances_per_object=200,
        catalog_collect_sample_objects=150,
        catalog_collect_sample_affordances_per_object=60,
        catalog_collect_top_auto_n=40,
        aff_meta_substrings="",
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
        self.director_enable_wants = director_enable_wants
        self.director_use_guardian_when_low = director_use_guardian_when_low
        self.director_per_sim_cooldown_seconds = director_per_sim_cooldown_seconds
        self.director_max_pushes_per_sim_per_hour = director_max_pushes_per_sim_per_hour
        self.director_prefer_career_skills = director_prefer_career_skills
        self.director_fallback_to_started_skills = director_fallback_to_started_skills
        self.director_skill_allow_list = director_skill_allow_list or []
        self.director_skill_block_list = director_skill_block_list or []
        self.collect_log_filename = collect_log_filename
        self.story_log_enabled = story_log_enabled
        self.story_log_filename = story_log_filename
        self.capabilities_filename = capabilities_filename
        self.capabilities_auto_build_on_enable = capabilities_auto_build_on_enable
        self.catalog_include_sims = catalog_include_sims
        self.catalog_include_non_autonomous = catalog_include_non_autonomous
        self.catalog_max_records = catalog_max_records
        self.catalog_max_objects = catalog_max_objects
        self.catalog_max_affordances_per_object = catalog_max_affordances_per_object
        self.catalog_collect_sample_objects = catalog_collect_sample_objects
        self.catalog_collect_sample_affordances_per_object = (
            catalog_collect_sample_affordances_per_object
        )
        self.catalog_collect_top_auto_n = catalog_collect_top_auto_n
        self.aff_meta_substrings = aff_meta_substrings
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
    aliases = {
        "enable": "enabled",
        "include_sim": "catalog_include_sims",
        "include_sims": "catalog_include_sims",
        "include_non_autonomous": "catalog_include_non_autonomous",
    }
    for key, raw_value in data.items():
        key = aliases.get(key, key)
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
            elif key == "director_enable_wants":
                if isinstance(value, bool):
                    target.director_enable_wants = value
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
            elif key == "collect_log_filename":
                value = str(raw_value).strip()
                target.collect_log_filename = (
                    value if value else "simulation-mode-collect.log"
                )
            elif key == "story_log_enabled":
                if isinstance(value, bool):
                    target.story_log_enabled = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "story_log_filename":
                value = str(raw_value).strip()
                target.story_log_filename = value if value else "simulation-mode-story.log"
            elif key == "capabilities_filename":
                value = str(raw_value).strip()
                target.capabilities_filename = (
                    value if value else "simulation-mode-capabilities.json"
                )
            elif key == "capabilities_auto_build_on_enable":
                if isinstance(value, bool):
                    target.capabilities_auto_build_on_enable = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "catalog_include_sims":
                if isinstance(value, bool):
                    target.catalog_include_sims = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "catalog_include_non_autonomous":
                if isinstance(value, bool):
                    target.catalog_include_non_autonomous = value
                else:
                    _log_invalid_value(key, raw_value)
            elif key == "catalog_max_records":
                try:
                    target.catalog_max_records = max(1, int(value))
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "catalog_max_objects":
                try:
                    target.catalog_max_objects = max(1, int(value))
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "catalog_max_affordances_per_object":
                try:
                    target.catalog_max_affordances_per_object = max(1, int(value))
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "catalog_collect_sample_objects":
                try:
                    target.catalog_collect_sample_objects = max(1, int(value))
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "catalog_collect_sample_affordances_per_object":
                try:
                    target.catalog_collect_sample_affordances_per_object = max(1, int(value))
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "catalog_collect_top_auto_n":
                try:
                    target.catalog_collect_top_auto_n = max(1, int(value))
                except Exception:
                    _log_invalid_value(key, raw_value)
            elif key == "aff_meta_substrings":
                target.aff_meta_substrings = str(raw_value)
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


def persist_setting(key: str, value) -> bool:
    path = Path(get_config_path())
    try:
        if not path.exists():
            _ensure_template(path)
        if path.exists():
            contents = path.read_text(encoding="utf-8")
            lines = contents.splitlines()
        else:
            lines = []
        if isinstance(value, bool):
            serialized = "true" if value else "false"
        elif isinstance(value, int) and not isinstance(value, bool):
            serialized = str(value)
        else:
            serialized = str(value)
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        replaced = False
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith(";"):
                continue
            if pattern.match(line):
                lines[index] = f"{key}={serialized}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{key}={serialized}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def get_bool(key, default):
    try:
        value = getattr(settings, key)
    except Exception:
        return default
    if isinstance(value, bool):
        return value
    coerced = _coerce_bool(value)
    if coerced is not None:
        return coerced
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def get_int(key, default):
    try:
        value = getattr(settings, key)
    except Exception:
        return default
    try:
        return int(value)
    except Exception:
        return default


def get_str(key, default):
    try:
        value = getattr(settings, key)
    except Exception:
        return default
    if value is None:
        return default
    return str(value)


settings = SimulationModeSettings()
try:
    load_settings(settings)
except Exception:
    _log_exception("Settings load failed; continuing with defaults")
