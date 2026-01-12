import re
import time
from collections import deque

from interactions.context import (
    InteractionContext,
    InteractionSource,
)
import services

from simulation_mode import clock_utils
from simulation_mode import guardian
from simulation_mode.push_utils import (
    call_push_super_affordance,
    find_affordance_candidates,
    iter_objects,
    make_interaction_context,
    push_best_affordance,
)
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
        "affordance_keywords": [
            "workout",
            "practice",
            "train",
            "jog",
            "ride",
            "cycle",
            "spin",
            "cardio",
            "strength",
            "run",
        ],
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
    "acting": {
        "object_keywords": ["mirror", "computer"],
        "affordance_keywords": ["practice acting", "acting", "research acting", "practice"],
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
    "ActorCareer": ["acting", "charisma"],
}

_WHIM_RULES = {
    "fun": {
        "object_keywords": ["tv", "stereo", "radio", "computer", "console", "game"],
        "affordance_keywords": ["watch", "play", "listen", "dance"],
    },
    "social": {
        "object_keywords": ["phone", "computer"],
        "affordance_keywords": ["chat", "talk", "social", "call", "text"],
    },
    "exercise": {
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
    "admire_art": {
        "object_keywords": ["painting", "sculpture", "art", "museum", "gallery"],
        "affordance_keywords": ["view", "admire", "appraise", "look", "study"],
    },
    "hug": {
        "target_type": "sim",
        "affordance_keywords": ["hug"],
    },
}

_last_check_time = 0.0
_per_sim_last_push_time = {}
_per_sim_push_count_window_start = {}
_per_sim_push_count_in_window = {}

_DEBUG_RING = deque(maxlen=60)

last_director_actions = []
last_director_called_time = 0.0
last_director_run_time = 0.0
last_director_time = 0.0
last_director_debug = []

_LAST_ACTION_DETAILS = None
_LAST_WANT_DETAILS = None

_last_motive_snapshot_by_sim = {}

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
        return False
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


def _choose_career_skill(sim_info):
    if sim_info is None or not settings.director_prefer_career_skills:
        return None
    career_tracker = getattr(sim_info, "career_tracker", None)
    career = None
    if career_tracker is not None:
        career = getattr(career_tracker, "career_current", None) or getattr(
            career_tracker, "current_career", None
        )
        if career is None:
            careers_dict = getattr(career_tracker, "_careers", None)
            if isinstance(careers_dict, dict) and careers_dict:
                candidates = [candidate for candidate in careers_dict.values() if candidate is not None]
                if any(
                    callable(getattr(candidate, "is_work_career", None))
                    for candidate in candidates
                ):
                    work_candidates = [
                        candidate
                        for candidate in candidates
                        if candidate is not None
                        and callable(getattr(candidate, "is_work_career", None))
                        and candidate.is_work_career()
                    ]
                    if work_candidates:
                        candidates = work_candidates
                if candidates:
                    career = max(
                        candidates,
                        key=lambda candidate: (getattr(candidate, "level", 0) or 0),
                    )
    career_name = ""
    career_guid = None
    if career is not None:
        career_name = getattr(getattr(career, "__class__", None), "__name__", "")
        career_guid = getattr(career, "guid64", None)
    if not career_name:
        return None
    lowered = career_name.lower()
    for key, skills in _CAREER_TO_SKILLS.items():
        if key.lower() in lowered:
            for skill_key in skills:
                if _skill_allowed(skill_key):
                    reason = f"career: {career_name}"
                    if career_guid is not None:
                        reason = f"career: {career_name} ({career_guid})"
                    return skill_key, reason
    return None


def _choose_started_skill(sim_info):
    if sim_info is None or not settings.director_fallback_to_started_skills:
        return None
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


def choose_skill_goal(sim_info):
    return _choose_career_skill(sim_info) or _choose_started_skill(sim_info)


def _extract_whim_name(whim):
    for attr in ("name", "display_name", "whim_type", "whim_category"):
        value = getattr(whim, attr, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if value:
            return str(value)
    return str(whim) if whim is not None else ""


def _extract_whim_guid(whim):
    for attr in ("guid64", "whim_guid", "guid", "tuning_guid"):
        value = getattr(whim, attr, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if value:
            return value
    return None


def _slot_flag(slot, attr: str) -> bool:
    value = getattr(slot, attr, None)
    if callable(value):
        try:
            return bool(value())
        except Exception:
            return False
    return bool(value)


def _iter_active_whims_from_tracker(tracker):
    slots = None
    slots_gen = getattr(tracker, "slots_gen", None)
    if callable(slots_gen):
        try:
            slots = list(slots_gen())
        except Exception:
            slots = None
    if slots is None:
        slots = getattr(tracker, "_whim_slots", None)
    if slots is None:
        return None
    active = []
    for slot in slots:
        is_empty = _slot_flag(slot, "is_empty")
        is_locked = _slot_flag(slot, "is_locked")
        if is_empty or is_locked:
            continue
        whim = getattr(slot, "whim", None)
        if whim is None:
            continue
        active.append(whim)
    return active


def get_active_want_targets(sim_info):
    sim = sim_info.get_sim_instance() if sim_info else None
    for source in (sim_info, sim):
        if source is None:
            continue
        tracker = getattr(source, "wants_tracker", None)
        if tracker is None:
            tracker = getattr(source, "wants_and_fears_tracker", None)
        if tracker is None:
            tracker = getattr(source, "whim_tracker", None)
        if tracker is None:
            continue
        active_slots = _iter_active_whims_from_tracker(tracker)
        if active_slots is not None:
            return active_slots
        for attr in ("get_active_wants", "get_wants"):
            getter = getattr(tracker, attr, None)
            if callable(getter):
                try:
                    wants = list(getter())
                except Exception:
                    wants = []
                return [want for want in wants if want]
        active = getattr(tracker, "active_wants", None)
        if active is not None:
            try:
                return list(active)
            except Exception:
                return []
    return []


def _resolve_whim_rule(whim_name: str):
    lowered = (whim_name or "").lower()
    normalized = _norm(whim_name)
    if "hug" in normalized:
        return "hug"
    if "admireart" in normalized or ("admire" in normalized and "art" in normalized):
        return "admire_art"
    if "fun" in lowered or "have fun" in lowered:
        return "fun"
    if "friendly" in lowered or "social" in lowered or "be friendly" in lowered:
        return "social"
    if "exercise" in lowered or "workout" in lowered:
        return "exercise"
    return None


def _select_want_targets(sim_info):
    wants = get_active_want_targets(sim_info)
    if not wants:
        return [], "WANT unavailable (no active wants or wants disabled)"
    non_social_rules = []
    social_rules = []
    for want in wants:
        want_name = _extract_whim_name(want)
        rule_key = _resolve_whim_rule(want_name)
        if rule_key is None:
            continue
        if rule_key in {"social", "hug"}:
            social_rules.append((rule_key, want_name, want))
            continue
        non_social_rules.append((rule_key, want_name, want))
    candidates = non_social_rules + social_rules
    if not candidates:
        return [], "WANT no supported target"
    return candidates, None


def _same_lot(sim, other_sim):
    if sim is None or other_sim is None:
        return False
    sim_zone = getattr(sim, "zone_id", None)
    other_zone = getattr(other_sim, "zone_id", None)
    if sim_zone is None or other_zone is None:
        return True
    return sim_zone == other_zone


def _find_target_sim(sim):
    sim_info = getattr(sim, "sim_info", None)
    household = getattr(sim_info, "household", None) if sim_info is not None else None
    if household is not None:
        try:
            for other_info in list(household):
                if other_info is None or other_info is sim_info:
                    continue
                other_sim = other_info.get_sim_instance()
                if other_sim is None:
                    continue
                if _same_lot(sim, other_sim):
                    return other_sim
        except Exception:
            pass

    sim_manager = None
    try:
        sim_manager = services.sim_info_manager()
    except Exception:
        sim_manager = None
    if sim_manager is None:
        return None
    try:
        for other_info in sim_manager.get_all():
            if other_info is None or other_info is sim_info:
                continue
            other_sim = other_info.get_sim_instance()
            if other_sim is None:
                continue
            if _same_lot(sim, other_sim):
                return other_sim
    except Exception:
        return None
    return None


def _push_want(sim, rule_key, want_name, force=False):
    global _LAST_WANT_DETAILS
    if rule_key == "social" and not settings.director_allow_social_goals:
        return False, "WANT social disabled"
    rule = _WHIM_RULES.get(rule_key)
    if rule is None:
        return False, "WANT no rule"
    if rule.get("target_type") == "sim":
        target_sim = _find_target_sim(sim)
        if target_sim is None:
            return False, f"WANT {rule_key} no target sim"
        pushed, aff_name, reason = push_best_affordance(
            sim,
            target_sim,
            rule.get("affordance_keywords", []),
            force=force,
        )
        if not pushed:
            if reason == "no affordance candidates":
                return False, f"WANT {rule_key} no affordance candidates"
            return False, f"WANT {rule_key} no affordance candidates"
        _LAST_WANT_DETAILS = (
            want_name,
            _get_object_label(target_sim),
            aff_name or "unknown",
        )
        return True, f"WANT {want_name}"

    target_obj = _find_target_object(sim, rule)
    if target_obj is None:
        return False, f"WANT {rule_key} no object match"
    candidates = find_affordance_candidates(
        target_obj, rule.get("affordance_keywords", []), sim=sim
    )
    if not candidates:
        return False, f"WANT {rule_key} no affordance candidates"
    affordance = candidates[0]
    pushed = _push_affordance(sim, target_obj, affordance, reason=rule_key, force=force)
    if pushed:
        _LAST_WANT_DETAILS = (
            want_name,
            _get_object_label(target_obj),
            _get_affordance_label(affordance),
        )
    return pushed, f"WANT {want_name}"


def _get_object_label(obj):
    parts = [getattr(obj.__class__, "__name__", None)]
    definition = getattr(obj, "definition", None)
    if definition is not None:
        name = getattr(definition, "name", None)
        if name:
            parts.append(name)
    parts.append(str(obj))
    return " ".join(part for part in parts if part).lower()


def _get_affordance_label(affordance):
    return (
        getattr(affordance, "__name__", None)
        or getattr(affordance, "__qualname__", None)
        or getattr(type(affordance), "__name__", None)
        or str(affordance)
    ).lower()


def _aff_label(affordance):
    try:
        return getattr(affordance, "__name__", None) or str(affordance)
    except Exception:
        return "<aff?>"


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
    for obj in iter_objects():
        try:
            in_inventory = getattr(obj, "is_in_inventory", None)
            if in_inventory is True:
                continue
            if callable(in_inventory) and in_inventory():
                continue
            hidden = getattr(obj, "is_hidden", None)
            if hidden is True:
                continue
            if callable(hidden) and hidden():
                continue
            if getattr(obj, "is_deleted", False):
                continue
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


def _push_affordance(sim, target_obj, affordance, reason=None, force=False):
    global _LAST_ACTION_DETAILS
    try:
        src = InteractionSource.PIE_MENU if force else InteractionSource.SCRIPT
    except Exception:
        src = InteractionContext.SOURCE_PIE_MENU if force else InteractionContext.SOURCE_SCRIPT
    context, client_attached = make_interaction_context(sim, force=force, source=src)
    success, failure_reason, sig_names = call_push_super_affordance(
        sim, affordance, target_obj, context
    )
    if success:
        _LAST_ACTION_DETAILS = (
            _get_object_label(target_obj),
            _get_affordance_label(affordance),
        )
        return True

    sim_name = getattr(sim, "full_name", None)
    if callable(sim_name):
        try:
            sim_name = sim_name()
        except Exception:
            sim_name = None
    sim_name = sim_name or getattr(sim, "first_name", None) or "Sim"
    target_class = getattr(target_obj.__class__, "__name__", "unknown")
    aff_label = _get_affordance_label(affordance)
    failure_reason = failure_reason or "unknown failure"
    _append_debug(
        f"{sim_name}: push failed obj_class={target_class} "
        f"aff={aff_label} reason={failure_reason} client_attached={client_attached} "
        f"sig_names={sig_names}"
    )
    return False


def try_push_skill_interaction(sim, skill_key, force=False):
    sim_name = getattr(sim, "full_name", None)
    if callable(sim_name):
        try:
            sim_name = sim_name()
        except Exception:
            sim_name = None
    sim_name = sim_name or getattr(sim, "first_name", None) or "Sim"
    rule = _SKILL_RULES.get(skill_key)
    if rule is None:
        _append_debug(f"{sim_name}: FAIL no rule for skill={skill_key}")
        return False
    target_obj = _find_target_object(sim, rule)
    if target_obj is None:
        count = len(iter_objects())
        _append_debug(
            f"{sim_name}: FAIL no object for skill={skill_key} "
            f"(iter_objects={count}) keywords={rule.get('object_keywords')}"
        )
        return False
    candidates = find_affordance_candidates(
        target_obj, rule.get("affordance_keywords", []), sim=sim
    )
    if not candidates:
        _append_debug(
            f"{sim_name}: FAIL no affordance for skill={skill_key} "
            f"object={_get_object_label(target_obj)} keywords={rule.get('affordance_keywords')}"
        )
        return False
    for affordance in candidates[:8]:
        if _push_affordance(sim, target_obj, affordance, reason=skill_key, force=force):
            _append_debug(
                f"{sim_name}: SUCCESS push skill={skill_key} aff={_aff_label(affordance)}"
            )
            return True
        _append_debug(
            f"{sim_name}: candidate failed skill={skill_key} aff={_aff_label(affordance)}"
        )
    _append_debug(f"{sim_name}: FAIL all candidates failed for skill={skill_key}")
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


def _dbg(message):
    _DEBUG_RING.append(message)
    last_director_debug[:] = list(_DEBUG_RING)
    settings.last_director_debug = "\n".join(_DEBUG_RING)


def _append_debug(message):
    _dbg(message)


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


def get_motive_snapshot_for_sim(sim_info):
    if sim_info is None:
        return []
    sim_id = _sim_identifier(sim_info)
    snapshot = _last_motive_snapshot_by_sim.get(sim_id)
    if snapshot is None:
        snapshot = _get_motive_snapshot(sim_info)
    return snapshot or []


def _evaluate(now: float, force: bool = False):
    global last_director_time
    _DEBUG_RING.clear()
    last_director_debug[:] = []
    settings.last_director_debug = ""
    actions_before = len(last_director_actions)
    household = services.active_household()
    if household is None:
        _dbg("Director ran: no active household")
        return
    try:
        sim_infos = list(household)
    except Exception:
        try:
            sim_infos = list(household.sim_infos)
        except Exception:
            _dbg("Director ran: unable to read household sims")
            return
    if not sim_infos:
        _dbg("Director ran: no eligible sims")
        return

    for sim_info in sim_infos:
        try:
            sim_name = _sim_display_name(sim_info)
            sim = sim_info.get_sim_instance()
            if sim is None:
                _dbg(f"{sim_name}: SKIP no sim instance")
                continue

            min_motive = None
            snapshot = _get_motive_snapshot(sim_info)
            if snapshot:
                min_motive = _safe_min_motive(snapshot)
                sim_id = _sim_identifier(sim_info)
                _last_motive_snapshot_by_sim[sim_id] = list(snapshot)
                green_count = 0
                for _key, value in snapshot:
                    if guardian.motive_is_green(value, settings.director_green_motive_percent):
                        green_count += 1
                if green_count < settings.director_green_min_commodities:
                    if settings.director_use_guardian_when_low and settings.guardian_enabled:
                        success, debug_message = guardian.push_self_care(
                            sim_info, now, settings.director_green_motive_percent
                        )
                        if success:
                            care_details = guardian.last_care_details() or ("unknown", "unknown")
                            motive_key, interaction = care_details
                            action = f"{sim_name}: CARE {motive_key} via {interaction}"
                            _append_action(action)
                            last_director_time = now
                        else:
                            _dbg(f"{sim_name}: CARE {debug_message}")
                    else:
                        _dbg(f"{sim_name}: CARE disabled (gate)")
                    continue
            else:
                _dbg(f"{sim_name}: SKIP motives unreadable (no motive stats found)")
                continue

            if _is_sim_busy(sim):
                _dbg(f"{sim_name}: SKIP busy")
                continue

            if not _can_push_for_sim(sim_id, now):
                _dbg(f"{sim_name}: SKIP cooldown")
                continue

            want_targets, want_reason = _select_want_targets(sim_info)
            goal = None
            if want_targets:
                pushed_any = False
                for want_key, want_name, want_obj in want_targets:
                    debug_name = getattr(want_obj, "__name__", None) or str(want_obj)
                    _dbg(f"{sim_name}: WHIM picked __name__={debug_name}")
                    if want_key == "social":
                        if not settings.director_allow_social_goals:
                            _dbg(f"{sim_name}: WANT social disabled")
                            continue
                    if want_key == "hug":
                        if (
                            hasattr(settings, "director_allow_social_wants")
                            and not settings.director_allow_social_wants
                        ):
                            _dbg(f"{sim_name}: WANT hug disabled")
                            continue
                    pushed, want_message = _push_want(sim, want_key, want_name, force=force)
                    if pushed:
                        _record_push(sim_id, now)
                        action = f"{sim_name} -> WANT {want_name}"
                        _append_action(action)
                        last_director_time = now
                        pushed_any = True
                        break
                    _dbg(f"{sim_name}: {want_message}")
                if pushed_any:
                    continue
            elif want_reason:
                _dbg(f"{sim_name}: {want_reason}")

            if goal is None:
                goal = choose_skill_goal(sim_info)
            if goal is None:
                _dbg(f"{sim_name}: NO GOAL")
                continue
            skill_key, reason = goal

            rule = _SKILL_RULES.get(skill_key)
            if rule is None:
                _dbg(f"{sim_name}: FAIL no rule for skill={skill_key}")
                continue
            target_obj = _find_target_object(sim, rule)
            if target_obj is None:
                _dbg(f"{sim_name}: FAIL no object for skill={skill_key}")
                continue
            candidates = find_affordance_candidates(
                target_obj, rule.get("affordance_keywords", []), sim=sim
            )
            if not candidates:
                _dbg(
                    f"{sim_name}: FAIL no affordance on object={_get_object_label(target_obj)} "
                    f"for skill={skill_key}"
                )
                continue
            affordance = candidates[0]
            try:
                pushed = _push_affordance(sim, target_obj, affordance, reason=skill_key, force=force)
            except Exception as exc:
                _dbg(
                    f"{sim_name}: EXC push skill={skill_key} target={_get_object_label(target_obj)} "
                    f"aff={_aff_label(affordance)} err={repr(exc)}"
                )
                continue
            if pushed:
                _record_push(sim_id, now)
                _record_action(sim_info, skill_key, reason, now)
            else:
                _dbg(
                    f"{sim_name}: FAIL push returned False skill={skill_key} "
                    f"target={_get_object_label(target_obj)} aff={_aff_label(affordance)}"
                )
        except Exception:
            continue
    if not last_director_debug and len(last_director_actions) == actions_before:
        _dbg("Director ran: no eligible sims")


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
    _evaluate(now, force=False)


def run_now(now: float, force: bool = False):
    global _last_check_time, last_director_called_time, last_director_run_time
    if not settings.enabled or not settings.director_enabled:
        return
    try:
        if clock_utils.is_paused():
            return
    except Exception:
        return
    last_director_called_time = now
    if not force and now - _last_check_time < settings.director_check_seconds:
        _DEBUG_RING.clear()
        last_director_debug[:] = []
        settings.last_director_debug = ""
        _dbg("Director ran: throttled")
        return
    _last_check_time = now
    last_director_run_time = now
    _evaluate(now, force=force)


def push_skill_now(sim, skill_key: str, now: float) -> bool:
    global last_director_time
    if not settings.enabled or not settings.director_enabled:
        _dbg(f"Sim: FAIL director disabled for skill={skill_key}")
        return False
    try:
        if clock_utils.is_paused():
            _dbg(f"Sim: FAIL clock paused for skill={skill_key}")
            return False
    except Exception:
        _dbg(f"Sim: FAIL clock paused for skill={skill_key}")
        return False
    sim_name = getattr(sim, "full_name", None)
    if callable(sim_name):
        try:
            sim_name = sim_name()
        except Exception:
            sim_name = None
    sim_name = sim_name or getattr(sim, "first_name", None) or "Sim"
    if try_push_skill_interaction(sim, skill_key, force=True):
        action = f"{sim_name} -> {skill_key} (forced)"
        _append_action(action)
        last_director_time = now
        return True
    _dbg(f"{sim_name}: FAIL forced push skill={skill_key}")
    return False
