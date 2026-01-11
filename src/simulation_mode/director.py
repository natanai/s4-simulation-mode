import re
import time

from interactions.context import InteractionContext, QueueInsertStrategy, InteractionBucketType
import interactions.priority as priority
import services

from simulation_mode import clock_utils
from simulation_mode import guardian
from simulation_mode.settings import settings

_SKILL_RULES = {
    "programming": {
        "object_keywords": ["computer"],
        "affordance_keywords": [
            "practice programming",
            "program",
            "hack",
            "freelance",
            "browse web",
            "web",
        ],
    },
    "video_gaming": {
        "object_keywords": ["computer", "console"],
        "affordance_keywords": ["play game", "play", "gaming"],
    },
    "writing": {
        "object_keywords": ["computer"],
        "affordance_keywords": ["write", "practice writing"],
    },
    "cooking": {
        "object_keywords": ["fridge", "refriger", "stove", "oven"],
        "affordance_keywords": ["cook", "have quick meal", "quick meal", "prepare"],
    },
    "fitness": {
        "object_keywords": [
            "treadmill",
            "punch",
            "weights",
            "workout",
            "basketball",
            "pullup",
            "exercise",
            "bike",
            "bicycle",
            "stationary",
        ],
        "affordance_keywords": ["workout", "practice", "train", "jog"],
    },
    "logic": {
        "object_keywords": ["chess", "telescope", "microscope"],
        "affordance_keywords": ["play chess", "use", "research", "practice logic"],
    },
    "painting": {
        "object_keywords": ["easel"],
        "affordance_keywords": ["paint", "practice painting"],
    },
    "guitar": {
        "object_keywords": ["guitar"],
        "affordance_keywords": ["practice", "play"],
    },
    "piano": {
        "object_keywords": ["piano", "keyboard"],
        "affordance_keywords": ["practice", "play"],
    },
    "violin": {
        "object_keywords": ["violin"],
        "affordance_keywords": ["practice", "play"],
    },
    "charisma": {
        "object_keywords": ["mirror"],
        "affordance_keywords": ["practice speech", "practice", "psych up", "pep talk"],
    },
    "mischief": {
        "object_keywords": ["computer"],
        "affordance_keywords": ["troll", "mischief", "prank"],
    },
}

_CAREER_TO_SKILLS = {
    "TechGuru": ["programming", "video_gaming"],
    "Writer": ["writing"],
    "Culinary": ["cooking"],
    "Athlete": ["fitness"],
    "Painter": ["painting"],
    "Entertainer": ["guitar", "piano", "violin"],
    "Criminal": ["mischief", "fitness"],
    "Astronaut": ["fitness", "logic"],
    "Scientist": ["logic"],
    "Business": ["charisma"],
}

_last_check_time = 0.0
_per_sim_last_push_time = {}
_per_sim_push_count_window_start = {}
_per_sim_push_count_in_window = {}

last_director_actions = []
last_director_called_time = 0.0
last_director_run_time = 0.0
last_director_time = 0.0
last_director_debug = []

_LAST_ACTION_DETAILS = None

_WINDOW_SECONDS = 3600
_BUSY_BUFFER = 10


def _norm(s: str) -> str:
    s = (s or "").lower()
    return re.sub(r"[\s_\-]+", "", s)


def _sim_identifier(sim_info):
    sim_id = getattr(sim_info, "sim_id", None)
    return sim_id or id(sim_info)


def _is_sim_busy(sim):
    queue = getattr(sim, "queue", None)
    if queue is None:
        return None
    try:
        running = getattr(queue, "running", None)
        if running:
            return True
    except Exception:
        pass
    try:
        queued = getattr(queue, "_queue", None)
        if queued is not None and hasattr(queued, "__len__"):
            return len(queued) > 0
    except Exception:
        pass
    return False


def _get_motive_snapshot(sim_info):
    try:
        return guardian._motive_snapshot(sim_info)
    except Exception:
        return []


def _safe_min_motive(snapshot):
    min_value = None
    for _key, value in snapshot:
        if min_value is None or value < min_value:
            min_value = value
    return min_value


def _can_push_for_sim(sim_id, now):
    cooldown = settings.director_per_sim_cooldown_seconds
    last_push = _per_sim_last_push_time.get(sim_id)
    if last_push is not None and now - last_push < cooldown:
        return False

    window_start = _per_sim_push_count_window_start.get(sim_id)
    if window_start is None or now - window_start >= _WINDOW_SECONDS:
        _per_sim_push_count_window_start[sim_id] = now
        _per_sim_push_count_in_window[sim_id] = 0

    max_pushes = settings.director_max_pushes_per_sim_per_hour
    if max_pushes > 0 and _per_sim_push_count_in_window.get(sim_id, 0) >= max_pushes:
        return False
    return True


def _record_push(sim_id, now):
    _per_sim_last_push_time[sim_id] = now
    window_start = _per_sim_push_count_window_start.get(sim_id)
    if window_start is None or now - window_start >= _WINDOW_SECONDS:
        _per_sim_push_count_window_start[sim_id] = now
        _per_sim_push_count_in_window[sim_id] = 0
    _per_sim_push_count_in_window[sim_id] = _per_sim_push_count_in_window.get(sim_id, 0) + 1


def _skill_allowed(skill_key):
    allow_list = settings.director_skill_allow_list
    block_list = settings.director_skill_block_list
    if allow_list and skill_key not in allow_list:
        return False
    if block_list and skill_key in block_list:
        return False
    return True


def _extract_skill_name(skill):
    return (
        getattr(skill, "__name__", None)
        or getattr(skill, "__qualname__", None)
        or getattr(type(skill), "__name__", None)
        or str(skill)
    )


def _skill_level_from_tracker(skill_tracker, skill):
    getter = getattr(skill_tracker, "get_skill_level", None)
    if callable(getter):
        try:
            return getter(skill)
        except Exception:
            return None
    return None


def _skill_max_from_tracker(skill_tracker, skill):
    for attr in ("get_max_level", "get_skill_max_level"):
        getter = getattr(skill_tracker, attr, None)
        if callable(getter):
            try:
                return getter(skill)
            except Exception:
                return None
    return None


def _skill_level_from_skill(skill):
    for attr in ("skill_level", "level", "user_value"):
        value = getattr(skill, attr, None)
        if value is None:
            continue
        try:
            return value() if callable(value) else value
        except Exception:
            continue
    getter = getattr(skill, "get_user_value", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return None


def _skill_max_from_skill(skill):
    for attr in ("max_level",):
        value = getattr(skill, attr, None)
        if value is None:
            continue
        try:
            return value() if callable(value) else value
        except Exception:
            continue
    getter = getattr(skill, "get_max_level", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return None


def choose_skill_goal(sim_info):
    if sim_info is None:
        return None

    if settings.director_prefer_career_skills:
        career_tracker = getattr(sim_info, "career_tracker", None)
        career = None
        if career_tracker is not None:
            career = getattr(career_tracker, "career_current", None)
            if career is None:
                career = getattr(career_tracker, "current_career", None)
        career_name = ""
        career_guid = None
        if career is not None:
            career_name = getattr(getattr(career, "__class__", None), "__name__", "")
            career_guid = getattr(career, "guid64", None)
        if career_name:
            lowered = career_name.lower()
            for key, skills in _CAREER_TO_SKILLS.items():
                if key.lower() in lowered:
                    for skill_key in skills:
                        if _skill_allowed(skill_key):
                            reason = f"career: {career_name}"
                            if career_guid is not None:
                                reason = f"career: {career_name} ({career_guid})"
                            return skill_key, reason

    if settings.director_fallback_to_started_skills:
        skill_tracker = getattr(sim_info, "skill_tracker", None)
        get_skills = None
        if skill_tracker is not None:
            get_skills = getattr(skill_tracker, "get_all_skills", None)
            if get_skills is None:
                get_skills = getattr(skill_tracker, "get_all_skill_types", None)
        if callable(get_skills):
            try:
                skills = list(get_skills())
            except Exception:
                skills = []
            for skill in skills:
                try:
                    name = _extract_skill_name(skill)
                    if not name:
                        continue
                    lowered = name.lower()
                    matched_key = None
                    for key in _SKILL_RULES:
                        if key in lowered:
                            matched_key = key
                            break
                    if matched_key is None:
                        continue
                    if not _skill_allowed(matched_key):
                        continue

                    level = _skill_level_from_skill(skill)
                    if level is None and skill_tracker is not None:
                        level = _skill_level_from_tracker(skill_tracker, skill)
                    max_level = _skill_max_from_skill(skill)
                    if max_level is None and skill_tracker is not None:
                        max_level = _skill_max_from_tracker(skill_tracker, skill)

                    if level is None or max_level is None:
                        continue
                    if level > 0 and level < max_level:
                        return matched_key, "started skill"
                except Exception:
                    continue

    return None


def _iter_objects():
    try:
        object_manager = services.object_manager()
        if object_manager is None:
            return []
        get_objects = getattr(object_manager, "get_objects", None)
        if callable(get_objects):
            return list(get_objects())
    except Exception:
        return []
    return []


def _get_object_label(obj):
    parts = [getattr(obj.__class__, "__name__", None)]
    definition = getattr(obj, "definition", None)
    if definition is not None:
        name = getattr(definition, "name", None)
        if name:
            parts.append(name)
    parts.append(str(obj))
    return " ".join(part for part in parts if part).lower()


def _iter_super_affordances(obj):
    affordances = getattr(obj, "super_affordances", None)
    if affordances is None:
        affordances = getattr(obj, "_super_affordances", None)
    return affordances or []


def _get_affordance_label(affordance):
    return (
        getattr(affordance, "__name__", None)
        or getattr(affordance, "__qualname__", None)
        or getattr(type(affordance), "__name__", None)
        or str(affordance)
    ).lower()


def _distance(sim, obj):
    sim_pos = getattr(sim, "position", None)
    obj_pos = getattr(obj, "position", None)
    if sim_pos is None or obj_pos is None:
        return None
    if all(hasattr(sim_pos, axis) for axis in ("x", "y", "z")) and all(
        hasattr(obj_pos, axis) for axis in ("x", "y", "z")
    ):
        try:
            dx = sim_pos.x - obj_pos.x
            dy = sim_pos.y - obj_pos.y
            dz = sim_pos.z - obj_pos.z
            return (dx * dx + dy * dy + dz * dz) ** 0.5
        except Exception:
            return None
    try:
        delta = sim_pos - obj_pos
        magnitude = getattr(delta, "magnitude", None)
        return magnitude() if callable(magnitude) else magnitude
    except Exception:
        return None


def _find_target_object(sim, rule):
    keywords = rule.get("object_keywords", [])
    if not keywords:
        return None
    best = None
    best_distance = None
    for obj in _iter_objects():
        try:
            label = _get_object_label(obj)
            norm_label = _norm(label)
            if not any(_norm(keyword) in norm_label for keyword in keywords):
                continue
            distance = _distance(sim, obj)
            if distance is None:
                if best is None:
                    best = obj
                continue
            if best_distance is None or distance < best_distance:
                best = obj
                best_distance = distance
        except Exception:
            continue
    return best


def _find_affordance(obj, rule):
    keywords = rule.get("affordance_keywords", [])
    if not keywords:
        return None
    affordances = _iter_super_affordances(obj)
    if not affordances:
        return None
    for keyword in keywords:
        for affordance in affordances:
            try:
                name = _get_affordance_label(affordance)
                if _norm(keyword) in _norm(name):
                    return affordance
            except Exception:
                continue
    return None


def _push_affordance(sim, target_obj, affordance):
    global _LAST_ACTION_DETAILS
    context = InteractionContext(
        sim,
        InteractionContext.SOURCE_SCRIPT,
        priority.Priority.High,
        insert_strategy=QueueInsertStrategy.NEXT,
        bucket=InteractionBucketType.DEFAULT,
    )
    result = sim.push_super_affordance(affordance, target_obj, context)
    if result:
        _LAST_ACTION_DETAILS = (
            _get_object_label(target_obj),
            _get_affordance_label(affordance),
        )
    return bool(result)


def try_push_skill_interaction(sim, skill_key):
    rule = _SKILL_RULES.get(skill_key)
    if rule is None:
        return False
    try:
        target_obj = _find_target_object(sim, rule)
        if target_obj is None:
            return False
        affordance = _find_affordance(target_obj, rule)
        if affordance is None:
            return False
        return _push_affordance(sim, target_obj, affordance)
    except Exception:
        return False


def _sim_display_name(sim_info):
    sim_name = getattr(sim_info, "first_name", None)
    if callable(sim_name):
        try:
            sim_name = sim_name()
        except Exception:
            sim_name = None
    if not sim_name:
        sim_name = getattr(sim_info, "full_name", None)
        if callable(sim_name):
            try:
                sim_name = sim_name()
            except Exception:
                sim_name = None
    if not sim_name:
        sim_name = "Sim"
    return sim_name


def _append_debug(message):
    last_director_debug.append(message)
    if len(last_director_debug) > 50:
        last_director_debug[:] = last_director_debug[-50:]


def _append_action(action):
    last_director_actions.append(action)
    if len(last_director_actions) > 20:
        last_director_actions[:] = last_director_actions[-20:]


def _record_action(sim_info, skill_key, reason, now):
    global last_director_time
    sim_name = _sim_display_name(sim_info)
    object_label = "unknown"
    affordance_label = "unknown"
    if _LAST_ACTION_DETAILS:
        object_label, affordance_label = _LAST_ACTION_DETAILS
    action = f"{sim_name} -> {skill_key} ({reason}) via {object_label}:{affordance_label}"
    _append_action(action)
    last_director_time = now


def _evaluate(now: float):
    last_director_debug[:] = []
    household = services.active_household()
    if household is None:
        return
    try:
        sim_infos = list(household)
    except Exception:
        try:
            sim_infos = list(household.sim_infos)
        except Exception:
            return

    for sim_info in sim_infos:
        try:
            sim_name = _sim_display_name(sim_info)
            sim = sim_info.get_sim_instance()
            if sim is None:
                _append_debug(f"{sim_name}: SKIP no sim instance")
                continue

            min_motive = None
            snapshot = _get_motive_snapshot(sim_info)
            if snapshot:
                min_motive = _safe_min_motive(snapshot)
                if min_motive is not None and min_motive < settings.director_min_safe_motive:
                    _append_debug(f"{sim_name}: SKIP motives unsafe")
                    continue
            else:
                _append_debug(f"{sim_name}: SKIP motives unreadable (no motive stats found)")
                continue

            busy_state = _is_sim_busy(sim)
            if busy_state is True:
                _append_debug(f"{sim_name}: SKIP busy")
                continue
            if busy_state is None and min_motive is not None:
                if min_motive < settings.director_min_safe_motive + _BUSY_BUFFER:
                    _append_debug(f"{sim_name}: SKIP busy")
                    continue

            sim_id = _sim_identifier(sim_info)
            if not _can_push_for_sim(sim_id, now):
                _append_debug(f"{sim_name}: SKIP cooldown")
                continue

            goal = choose_skill_goal(sim_info)
            if goal is None:
                _append_debug(f"{sim_name}: NO GOAL")
                continue
            skill_key, reason = goal

            rule = _SKILL_RULES.get(skill_key)
            if rule is None:
                _append_debug(f"{sim_name}: FAIL no rule for skill={skill_key}")
                continue
            target_obj = _find_target_object(sim, rule)
            if target_obj is None:
                _append_debug(f"{sim_name}: FAIL no object for skill={skill_key}")
                continue
            affordance = _find_affordance(target_obj, rule)
            if affordance is None:
                _append_debug(
                    f"{sim_name}: FAIL no affordance on object={_get_object_label(target_obj)} "
                    f"for skill={skill_key}"
                )
                continue
            try:
                pushed = _push_affordance(sim, target_obj, affordance)
            except Exception:
                _append_debug(f"{sim_name}: FAIL push_super_affordance exception")
                continue
            if pushed:
                _record_push(sim_id, now)
                _record_action(sim_info, skill_key, reason, now)
            else:
                _append_debug(f"{sim_name}: FAIL push_super_affordance false")
        except Exception:
            continue


def on_tick(now: float):
    global _last_check_time, last_director_called_time, last_director_run_time
    if not settings.enabled or not settings.director_enabled:
        return
    try:
        if clock_utils.is_paused():
            return
    except Exception:
        return
    last_director_called_time = now
    if now - _last_check_time < settings.director_check_seconds:
        return
    _last_check_time = now
    last_director_run_time = now
    _evaluate(now)


def run_now(now: float):
    global last_director_called_time, last_director_run_time
    if not settings.enabled or not settings.director_enabled:
        return
    try:
        if clock_utils.is_paused():
            return
    except Exception:
        return
    last_director_called_time = now
    last_director_run_time = now
    _evaluate(now)


def push_skill_now(sim, skill_key: str, now: float) -> bool:
    global last_director_time
    if not settings.enabled or not settings.director_enabled:
        _append_debug(f"Sim: FAIL director disabled for skill={skill_key}")
        return False
    try:
        if clock_utils.is_paused():
            _append_debug(f"Sim: FAIL clock paused for skill={skill_key}")
            return False
    except Exception:
        _append_debug(f"Sim: FAIL clock paused for skill={skill_key}")
        return False
    sim_name = getattr(sim, "full_name", None)
    if callable(sim_name):
        try:
            sim_name = sim_name()
        except Exception:
            sim_name = None
    sim_name = sim_name or getattr(sim, "first_name", None) or "Sim"
    if try_push_skill_interaction(sim, skill_key):
        action = f"{sim_name} -> {skill_key} (forced)"
        _append_action(action)
        last_director_time = now
        return True
    _append_debug(f"{sim_name}: FAIL forced push skill={skill_key}")
    return False
