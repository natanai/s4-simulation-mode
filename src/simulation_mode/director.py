import inspect
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
from simulation_mode import probe_log
from simulation_mode.push_utils import (
    affordance_name,
    call_push_super_affordance,
    find_affordance_candidates,
    is_picker_affordance,
    iter_objects,
    iter_super_affordances,
    make_interaction_context,
)
from simulation_mode.settings import settings
from simulation_mode import skills

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
    "paint": {
        "object_keywords": ["easel"],
        "affordance_keywords": ["paint"],
    },
    "trivia_box": {
        "object_keywords": ["triviabox", "trivia"],
        "affordance_keywords": ["play", "trivia"],
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
_LAST_CAREER_PROBE = []

_WINDOW_SECONDS = 3600
_BUSY_BUFFER = 10

_CACHED_WHIM_MANAGER = None
_CACHED_WHIM_TYPE_NAME = None


def _norm(s: str) -> str:
    s = (s or "").lower()
    return re.sub(r"[\s_\-]+", "", s)


def _sim_identifier(sim_info):
    sim_id = getattr(sim_info, "sim_id", None)
    return sim_id or id(sim_info)


def _get_current_interaction(sim):
    if sim is None:
        return None, "sim_none"
    for attr in ("get_current_interaction", "get_running_interaction", "get_current_super_interaction"):
        getter = getattr(sim, attr, None)
        if callable(getter):
            try:
                interaction = getter()
            except Exception:
                interaction = None
            if interaction is not None:
                return interaction, attr
    queue = getattr(sim, "queue", None)
    if queue is None:
        return None, "queue_none"
    for attr in ("current_interaction", "_current_interaction", "running"):
        interaction = getattr(queue, attr, None)
        if interaction is None:
            continue
        if isinstance(interaction, (list, tuple)):
            if interaction:
                return interaction[0], f"queue.{attr}[0]"
            continue
        return interaction, f"queue.{attr}"
    return None, "no_current_interaction"


def _interaction_is_idle(interaction):
    for attr in ("is_idle", "is_idle_interaction", "is_sim_idle"):
        value = getattr(interaction, attr, None)
        if callable(value):
            try:
                return bool(value())
            except Exception:
                continue
        if value is not None:
            return bool(value)
    return False


def _interaction_is_cancelable(interaction):
    for attr in ("can_cancel", "can_be_canceled", "can_be_cancelled", "is_cancelable"):
        value = getattr(interaction, attr, None)
        if callable(value):
            try:
                return bool(value())
            except Exception:
                continue
        if value is not None:
            return bool(value)
    return None


def _queue_size(sim):
    queue = getattr(sim, "queue", None)
    if queue is None:
        return None
    for attr in ("_queue", "queue"):
        value = getattr(queue, attr, None)
        if value is None:
            continue
        try:
            return len(value)
        except Exception:
            continue
    return None


def _is_sim_busy(sim):
    queue = getattr(sim, "queue", None)
    running = getattr(queue, "running", None) if queue is not None else None
    if running is not None:
        detail = f"running interaction queue.running type={type(running).__name__}"
        return True, detail
    interaction, source = _get_current_interaction(sim)
    if interaction is None:
        queue_len = _queue_size(sim)
        detail = f"no active interaction (queue_len={queue_len})"
        return False, detail
    if _interaction_is_idle(interaction):
        queue_len = _queue_size(sim)
        detail = f"current interaction idle source={source} queue_len={queue_len}"
        return False, detail
    detail = f"active interaction source={source} type={type(interaction).__name__}"
    return True, detail


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


def _get_instantiated_sims_for_director():
    sims = []
    try:
        active_sim = services.get_active_sim()
    except Exception:
        active_sim = None
    if active_sim is not None:
        sims.append(active_sim)

    try:
        sim_infos = list(services.sim_info_manager().get_all())
    except Exception:
        sim_infos = []

    current_zone_id = None
    try:
        current_zone_id = services.current_zone_id()
    except Exception:
        pass

    for sim_info in sim_infos:
        if sim_info is None:
            continue
        if active_sim is not None and getattr(active_sim, "sim_info", None) is sim_info:
            continue
        sim = None
        try:
            get_inst = getattr(sim_info, "get_sim_instance", None)
            if callable(get_inst):
                try:
                    sim = get_inst()
                except TypeError:
                    sim = get_inst()
        except Exception:
            sim = None
        if sim is None:
            continue

        try:
            zid = getattr(sim, "zone_id", None)
            if current_zone_id is not None and zid == current_zone_id:
                sims.append(sim)
            else:
                sims.append(sim)
        except Exception:
            sims.append(sim)

    seen = set()
    out = []
    for sim in sims:
        sid = id(sim)
        if sid in seen:
            continue
        seen.add(sid)
        out.append(sim)
    return out


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


def _trim_repr(value, limit=200):
    try:
        text = repr(value)
    except Exception as exc:
        text = f"<repr failed: {exc}>"
    if text is None:
        return ""
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def _skill_key_from_name(name):
    return skills.skill_key_from_name(name)


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


def _skill_level_for_key(sim_info, skill_key):
    level, _max_level = _skill_level_and_max_for_key(sim_info, skill_key)
    return level


def _skill_level_and_max_for_key(sim_info, skill_key):
    if sim_info is None or not skill_key:
        return None, None
    skill_tracker = getattr(sim_info, "skill_tracker", None)
    get_skills = None
    if skill_tracker is not None:
        get_skills = getattr(skill_tracker, "get_all_skills", None)
        if get_skills is None:
            get_skills = getattr(skill_tracker, "get_all_skill_types", None)
    if not callable(get_skills):
        return None, None
    try:
        skills = list(get_skills())
    except Exception:
        return None, None
    for skill in skills:
        name = _extract_skill_name(skill)
        if not name:
            continue
        if _skill_key_from_name(name) != skill_key:
            continue
        level = _skill_level_from_skill(skill)
        if level is None and skill_tracker is not None:
            level = _skill_level_from_tracker(skill_tracker, skill)
        max_level = _skill_max_from_skill(skill)
        if max_level is None and skill_tracker is not None:
            max_level = _skill_max_from_tracker(skill_tracker, skill)
        return level, max_level
    return None, None


def _iter_careers(sim_info):
    if sim_info is None:
        return []
    career_tracker = getattr(sim_info, "career_tracker", None)
    if career_tracker is None:
        return []
    careers = []
    career = getattr(career_tracker, "career_current", None) or getattr(
        career_tracker, "current_career", None
    )
    if career is not None:
        careers.append(career)
    careers_dict = getattr(career_tracker, "_careers", None)
    if isinstance(careers_dict, dict):
        for candidate in careers_dict.values():
            if candidate is not None and candidate not in careers:
                careers.append(candidate)
    if careers:
        work_candidates = []
        for candidate in careers:
            is_work = getattr(candidate, "is_work_career", None)
            if callable(is_work):
                try:
                    if is_work():
                        work_candidates.append(candidate)
                except Exception:
                    continue
        if work_candidates:
            return work_candidates
    return careers


def _iter_skill_tuning_values(value):
    if value is None:
        return []
    if isinstance(value, dict):
        items = list(value.values())
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]
    out = []
    for item in items:
        if item is None:
            continue
        out.append(item)
    return out


def _extract_skill_candidates_from_value(value):
    candidates = []
    for item in _iter_skill_tuning_values(value):
        name = _extract_skill_name(item)
        skill_key = _skill_key_from_name(name)
        if skill_key and _skill_allowed(skill_key):
            candidates.append((skill_key, name))
    return candidates


def _get_career_skill_candidates(sim_info):
    global _LAST_CAREER_PROBE
    _LAST_CAREER_PROBE = []
    if sim_info is None or not settings.director_prefer_career_skills:
        return []
    candidates = []
    careers = _iter_careers(sim_info)
    if not careers:
        _LAST_CAREER_PROBE.append("career_probe=no careers found")
        return []
    for career in careers:
        career_name = getattr(getattr(career, "__class__", None), "__name__", "") or "Career"
        career_guid = getattr(career, "guid64", None)
        for obj_label, obj in (
            ("career", career),
            ("career_track", getattr(career, "current_track", None)),
            ("current_level", getattr(career, "current_level_tuning", None)),
            ("next_level", getattr(career, "next_level_tuning", None)),
        ):
            if obj is None:
                continue
            for attr in dir(obj):
                if "skill" not in attr.lower():
                    continue
                try:
                    value = getattr(obj, attr)
                except Exception:
                    continue
                if callable(value):
                    try:
                        value = value()
                    except Exception:
                        continue
                _LAST_CAREER_PROBE.append(
                    f"{career_name}.{obj_label}.{attr} type={type(value).__name__} repr={_trim_repr(value)}"
                )
                for skill_key, skill_name in _extract_skill_candidates_from_value(value):
                    detail = (
                        f"career_requirement:{career_name}.{obj_label}.{attr} skill={skill_name}"
                    )
                    if career_guid is not None:
                        detail = f"{detail} career_id={career_guid}"
                    rationale = f"career_skill: {skill_key} because {detail}"
                    candidates.append((skill_key, rationale))
    if candidates:
        deduped = []
        seen = set()
        for skill_key, rationale in candidates:
            if skill_key in seen:
                continue
            seen.add(skill_key)
            deduped.append((skill_key, rationale))

        def _level_sort(item):
            level = _skill_level_for_key(sim_info, item[0])
            return (level if level is not None else 999, item[0])

        deduped.sort(key=_level_sort)
        return deduped

    fallback = []
    for career in careers:
        career_name = getattr(getattr(career, "__class__", None), "__name__", "") or ""
        career_guid = getattr(career, "guid64", None)
        lowered = career_name.lower()
        for key, skills in _CAREER_TO_SKILLS.items():
            if key.lower() in lowered:
                for skill_key in skills:
                    if not _skill_allowed(skill_key):
                        continue
                    rationale = (
                        f"career_skill: {skill_key} because career requirements not discoverable "
                        f"via tuning probe; using curated career mapping for career id={career_guid}"
                    )
                    fallback.append((skill_key, rationale))
        if fallback:
            break
    if fallback:
        _LAST_CAREER_PROBE.append("career_probe=fallback_mapping_used")
    return fallback


def _choose_career_skill(sim_info):
    if sim_info is None or not settings.director_prefer_career_skills:
        return None
    candidates, _satisfied = _filter_unmet_career_skills(
        sim_info, _get_career_skill_candidates(sim_info)
    )
    for skill_key, rationale in candidates:
        if _skill_allowed(skill_key):
            return skill_key, rationale
    return None


def _filter_unmet_career_skills(sim_info, candidates):
    unmet = []
    satisfied = []
    for skill_key, rationale in candidates:
        level, max_level = _skill_level_and_max_for_key(sim_info, skill_key)
        max_level = max_level if max_level is not None else 10
        if level is not None and level >= max_level:
            satisfied.append(skill_key)
            continue
        unmet.append((skill_key, rationale))
    return unmet, satisfied


def _get_started_skill_candidates(sim_info):
    if sim_info is None or not settings.director_fallback_to_started_skills:
        return []
    skill_tracker = getattr(sim_info, "skill_tracker", None)
    get_skills = None
    if skill_tracker is not None:
        get_skills = getattr(skill_tracker, "get_all_skills", None)
        if get_skills is None:
            get_skills = getattr(skill_tracker, "get_all_skill_types", None)
    if not callable(get_skills):
        return []
    try:
        skills = list(get_skills())
    except Exception:
        skills = []
    candidates = []
    for skill in skills:
        try:
            name = _extract_skill_name(skill)
            if not name:
                continue
            matched_key = _skill_key_from_name(name)
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
            if level is None:
                continue
            if level <= 0 or level >= 10:
                continue
            max_level = max_level if max_level is not None else 10
            if level >= max_level:
                continue
            candidates.append(
                (
                    matched_key,
                    f"started_skill: {matched_key} because level={level}",
                    level,
                )
            )
        except Exception:
            continue
    candidates.sort(key=lambda item: (item[2], item[0]))
    return candidates


def _choose_started_skill(sim_info):
    if sim_info is None or not settings.director_fallback_to_started_skills:
        return None
    candidates = _get_started_skill_candidates(sim_info)
    if candidates:
        skill_key, rationale, _level = candidates[0]
        return skill_key, rationale
    return None


def choose_skill_goal(sim_info):
    return _choose_career_skill(sim_info) or _choose_started_skill(sim_info)


def build_skill_plan(sim_info):
    candidates = []
    selected = None
    if settings.director_prefer_career_skills:
        career_candidates, _satisfied = _filter_unmet_career_skills(
            sim_info, _get_career_skill_candidates(sim_info)
        )
        for skill_key, rationale in career_candidates:
            candidates.append((skill_key, rationale))
        if career_candidates:
            selected = career_candidates[0]
    if selected is None and settings.director_fallback_to_started_skills:
        started_candidates = _get_started_skill_candidates(sim_info)
        for skill_key, rationale, _level in started_candidates:
            candidates.append((skill_key, rationale))
        if started_candidates:
            selected = (started_candidates[0][0], started_candidates[0][1])
    return selected, candidates


def run_skill_plan(sim_info, sim, now, force=False, source="director"):
    wants_reason = None
    want_targets = []
    if not settings.director_enable_wants:
        wants_reason = "disabled_by_setting"
    else:
        want_targets, _want_reason = _select_want_targets(sim_info)
        wants_reason = "no_wants" if not want_targets else "not_attempted_in_this_build"
    _append_debug(f"Director: wants=skipped reason={wants_reason}")

    career_candidates = []
    career_reason = "disabled_by_setting"
    if settings.director_prefer_career_skills:
        raw_candidates = _get_career_skill_candidates(sim_info)
        career_candidates, satisfied = _filter_unmet_career_skills(sim_info, raw_candidates)
        if career_candidates:
            if any("career_probe=fallback_mapping_used" in line for line in _LAST_CAREER_PROBE):
                career_reason = "not_discoverable"
            else:
                career_reason = "found"
        else:
            if satisfied:
                career_reason = "already_satisfied"
            else:
                career_reason = "no_career_skills_found"
    _append_debug(f"Director: career_skills={len(career_candidates)} reason={career_reason}")

    started_candidates = []
    if settings.director_fallback_to_started_skills:
        started_candidates = _get_started_skill_candidates(sim_info)
    _log_started_skill_order(started_candidates)

    for skill_key, rationale in career_candidates:
        level = _skill_level_for_key(sim_info, skill_key)
        attempt = _attempt_skill_candidate(
            sim, skill_key, level, source="career", force=force
        )
        if attempt["success"]:
            _append_debug(
                f"Director: skill_result=success skill={skill_key} source=career"
            )
            return {
                "success": True,
                "skill_key": skill_key,
                "skill_reason": rationale,
                "skill_source": "career",
                "wants_reason": wants_reason,
                "career_reason": career_reason,
                "started_candidates": started_candidates,
            }

    failure_counts = {"no_object": 0, "no_affordance": 0, "push_failed": 0}
    attempted = 0
    for skill_key, rationale, level in started_candidates:
        attempted += 1
        attempt = _attempt_skill_candidate(
            sim, skill_key, level, source="started", force=force
        )
        if attempt["success"]:
            _append_debug(
                f"Director: skill_result=success skill={skill_key} source=started"
            )
            return {
                "success": True,
                "skill_key": skill_key,
                "skill_reason": rationale,
                "skill_source": "started",
                "wants_reason": wants_reason,
                "career_reason": career_reason,
                "started_candidates": started_candidates,
            }
        failure_counts[attempt["result"]] += 1

    _append_debug(
        "Director: skill_result=failure attempted={} no_object={} "
        "no_affordance={} push_failed={}".format(
            attempted,
            failure_counts["no_object"],
            failure_counts["no_affordance"],
            failure_counts["push_failed"],
        )
    )
    return {
        "success": False,
        "skill_key": None,
        "skill_reason": None,
        "skill_source": None,
        "wants_reason": wants_reason,
        "career_reason": career_reason,
        "started_candidates": started_candidates,
        "failure_counts": failure_counts,
        "attempted": attempted,
    }


def _is_proto_message(obj):
    return hasattr(obj, "DESCRIPTOR") and hasattr(obj, "ListFields")


def _get_whim_guid64(whim):
    if whim is None:
        return None
    if _is_proto_message(whim) and hasattr(whim, "whim_guid64"):
        try:
            return int(getattr(whim, "whim_guid64") or 0) or None
        except Exception:
            return None
    for attr in ("guid64", "_guid64", "tuning_id", "_tuning_id", "instance_id"):
        try:
            value = getattr(whim, attr, None)
        except Exception:
            continue
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if value is None:
            continue
        try:
            guid64 = int(value)
        except Exception:
            continue
        if guid64:
            return guid64
    return None


def _get_want_target_sim_id(want_obj):
    if want_obj is None:
        return None
    try:
        if _is_proto_message(want_obj) and hasattr(want_obj, "whim_target_sim"):
            value = getattr(want_obj, "whim_target_sim", 0)
            try:
                value = int(value)
            except Exception:
                return None
            return value if value else None
    except Exception:
        pass
    return None


def _resolve_sim_instance_by_id(sim_id):
    if not sim_id:
        return None
    try:
        mgr = services.sim_info_manager()
    except Exception:
        mgr = None
    sim_info = None

    if mgr is not None:
        for fn_name in ("get", "get_sim_info_by_id", "get_by_id"):
            fn = getattr(mgr, fn_name, None)
            if callable(fn):
                try:
                    sim_info = fn(sim_id)
                except Exception:
                    sim_info = None
                if sim_info is not None:
                    break
        if sim_info is None:
            get_all = getattr(mgr, "get_all", None)
            if callable(get_all):
                try:
                    for info in list(get_all()):
                        if info is None:
                            continue
                        sid = getattr(info, "sim_id", None) or getattr(info, "id", None)
                        if sid == sim_id:
                            sim_info = info
                            break
                except Exception:
                    pass

    if sim_info is None:
        return None

    try:
        sim = sim_info.get_sim_instance()
    except Exception:
        sim = None
    return sim


def try_push_social_interaction(sim, target_sim):
    if sim is None or target_sim is None:
        return False
    affordance = _select_social_affordance(sim, target_sim, prefer_romance=False)
    if affordance is None:
        return False
    context, _client_attached = make_interaction_context(sim, force=False)
    ok, reason, sig = call_push_super_affordance(sim, affordance, target_sim, context)
    if ok:
        _append_debug(
            f"{_sim_display_name(getattr(sim, 'sim_info', None))}: "
            f"WANT social -> pushed {affordance_name(affordance)} on sim_target={target_sim}"
        )
        return True
    _append_debug(
        f"{_sim_display_name(getattr(sim, 'sim_info', None))}: "
        f"WANT social push failed reason={reason} sig={sig}"
    )
    return False


def _collect_social_affordances(sim, target_sim):
    if sim is None or target_sim is None:
        return []
    candidates = []
    getter = getattr(sim, "get_valid_interactions_for_target", None)
    if callable(getter):
        try:
            result = getter(target_sim)
            if result:
                candidates.extend(result)
        except Exception:
            pass
    if not candidates:
        candidates.extend(iter_super_affordances(target_sim, sim=sim))
    return list(dict.fromkeys(candidates))


def _select_social_affordance(sim, target_sim, prefer_romance=False):
    candidates = _collect_social_affordances(sim, target_sim)
    if not candidates:
        return None
    keywords = ["romance", "flirt"] if prefer_romance else ["chat", "talk", "social", "friendly"]
    for aff in candidates:
        try:
            name = affordance_name(aff)
        except Exception:
            continue
        if is_picker_affordance(aff):
            continue
        if _is_blocked_want_affordance(aff):
            continue
        if "mentor" in name or "offer" in name or "picker" in name:
            continue
        if any(keyword in name for keyword in keywords):
            return aff
    return None


def _resolve_whim_tuning_by_guid64(guid64):
    if not guid64:
        return None
    import sims4.resources

    global _CACHED_WHIM_MANAGER
    global _CACHED_WHIM_TYPE_NAME
    if _CACHED_WHIM_MANAGER is not None:
        try:
            inst = _CACHED_WHIM_MANAGER.get(guid64)
        except Exception:
            inst = None
        if inst is not None:
            return inst

    candidates = []
    for name in dir(sims4.resources.Types):
        if not name.isupper():
            continue
        if "WHIM" not in name:
            continue
        candidates.append(name)

    for candidate_name in candidates:
        try:
            type_value = getattr(sims4.resources.Types, candidate_name)
        except Exception:
            continue
        try:
            mgr = services.get_instance_manager(type_value)
        except Exception:
            mgr = None
        if mgr is None:
            continue
        try:
            inst = mgr.get(guid64)
        except Exception:
            inst = None
        if inst is not None:
            _CACHED_WHIM_MANAGER = mgr
            _CACHED_WHIM_TYPE_NAME = candidate_name
            return inst
    return None


def _extract_whim_name(whim):
    if whim is None:
        return ""
    guid64 = _get_whim_guid64(whim)
    tuning = _resolve_whim_tuning_by_guid64(guid64)
    if tuning is not None:
        return getattr(tuning, "__name__", None) or str(tuning)

    if _is_proto_message(whim):
        if hasattr(whim, "whim_name") and getattr(whim, "whim_name"):
            return f"whim_name={whim.whim_name!r}"
        if hasattr(whim, "whim_tooltip") and getattr(whim, "whim_tooltip"):
            return f"whim_tooltip={whim.whim_tooltip!r}"
        if guid64:
            return f"whim_guid64={guid64}"
        try:
            return repr(whim)
        except Exception:
            return ""

    for attr in (
        "name",
        "__name__",
        "display_name",
        "whim_name",
        "whim_tooltip",
    ):
        try:
            value = getattr(whim, attr, None)
        except Exception:
            continue
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if value:
            return str(value)

    try:
        return repr(whim)
    except Exception:
        return ""


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


def _get_active_wants(sim_info):
    """
    Return a list of active want/whim entries for sim_info.
    Must work with both:
      A) tracker exposes get_current_whims() / current_whims / _current_whims
      B) tracker exposes slots_gen() / _whim_slots (older whim-slot style)
    """
    if sim_info is None:
        return []

    tracker = getattr(sim_info, "whim_tracker", None) or getattr(sim_info, "_whim_tracker", None)
    if tracker is None:
        sim = sim_info.get_sim_instance()
        tracker = getattr(sim, "whim_tracker", None) or getattr(sim, "_whim_tracker", None)

    wants = []

    if tracker is not None:
        get_current = getattr(tracker, "get_current_whims", None)
        if callable(get_current):
            try:
                result = get_current()
            except Exception:
                result = None
            if result:
                try:
                    wants = list(result)
                except Exception:
                    wants = [result]
                return wants

        for attr in ("current_whims", "_current_whims", "current_wants", "_current_wants"):
            value = getattr(tracker, attr, None)
            if isinstance(value, (list, tuple)) and value:
                return list(value)

    if tracker is not None:
        slots_gen = getattr(tracker, "slots_gen", None)
        if callable(slots_gen):
            try:
                slots = list(slots_gen())
            except Exception:
                slots = None
        else:
            slots = getattr(tracker, "_whim_slots", None)

        if slots:
            out = []
            for slot in slots:
                is_empty = getattr(slot, "is_empty", False)
                is_locked = getattr(slot, "is_locked", False)
                if is_empty or is_locked:
                    continue
                whim = getattr(slot, "whim", None)
                if whim is not None:
                    out.append(whim)
            if out:
                return out

    for attr in ("current_whims", "_current_whims", "current_wants", "_current_wants"):
        value = getattr(sim_info, attr, None)
        if isinstance(value, (list, tuple)) and value:
            return list(value)

    return []


def get_active_want_targets(sim_info):
    return _get_active_wants(sim_info)


def _resolve_whim_rule(whim_name: str):
    lowered = (whim_name or "").lower()
    normalized = _norm(whim_name)
    if "hug" in normalized:
        return "hug"
    if "paint" in normalized:
        return "paint"
    if "triviabox" in normalized or "woowho" in normalized:
        return "trivia_box"
    if "admireart" in normalized or ("admire" in normalized and "art" in normalized):
        return "admire_art"
    if "fun" in lowered or "have fun" in lowered:
        return "fun"
    if (
        "chat" in lowered
        or "social" in lowered
        or "talk" in lowered
        or "friendly" in lowered
        or "be friendly" in lowered
    ):
        return "social"
    if "exercise" in lowered or "workout" in lowered:
        return "exercise"
    if "clean" in lowered or "wash" in lowered or "laundry" in lowered or "scrub" in lowered:
        return "clean"
    if "skill" in lowered or "level" in lowered or "practice" in lowered or "train" in lowered:
        return "skill"
    if "repair" in lowered or "fix" in lowered:
        return "repair"
    if "cook" in lowered or "meal" in lowered or "eat" in lowered:
        return "cook"
    return None


def _select_want_targets(sim_info):
    wants = _get_active_wants(sim_info)
    if not wants:
        return [], "WANT unavailable (no active wants or wants disabled)"
    sim_name = getattr(sim_info, "full_name", None) if sim_info is not None else None
    if callable(sim_name):
        try:
            sim_name = sim_name()
        except Exception:
            sim_name = None
    sim_name = sim_name or getattr(sim_info, "first_name", None) or "Sim"
    non_social_rules = []
    social_rules = []
    for want in wants:
        guid64 = _get_whim_guid64(want)
        tuning = _resolve_whim_tuning_by_guid64(guid64)
        tuning_name = getattr(tuning, "__name__", None) if tuning is not None else None
        label = tuning_name if tuning_name else _extract_whim_name(want)
        rule_key = _resolve_whim_rule(label)
        want_key = str(guid64) if guid64 else label
        _append_debug(
            f"{sim_name}: WANT label={label} guid64={guid64} "
            f"tuning_type={_CACHED_WHIM_TYPE_NAME} rule={rule_key}"
        )
        if rule_key is None:
            continue
        if rule_key in {"social", "hug"}:
            social_rules.append((want_key, label, want))
            continue
        non_social_rules.append((want_key, label, want))
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


def _resolve_whim_target_sim_instance(target_id):
    if not target_id:
        return None
    try:
        obj_mgr = services.object_manager()
    except Exception:
        obj_mgr = None
    if obj_mgr is None:
        return None
    getter = getattr(obj_mgr, "get", None)
    if not callable(getter):
        return None
    try:
        return getter(target_id)
    except Exception:
        return None


def _is_same_zone(sim_info, other_sim_info):
    if sim_info is None or other_sim_info is None:
        return False
    sim_zone = getattr(sim_info, "zone_id", None)
    other_zone = getattr(other_sim_info, "zone_id", None)
    if sim_zone is None or other_zone is None:
        return True
    return sim_zone == other_zone


def _push_want(sim, rule_key, want_name, want_obj=None, force=False, return_details=False):
    global _LAST_WANT_DETAILS
    details = {"resolution_type": "FAIL"} if return_details else None
    if rule_key == "skill":
        # Generic “level up any skill”: reuse existing skill selection and push
        choice = choose_skill_goal(getattr(sim, "sim_info", None))
        if not choice:
            if return_details:
                details["failure_reason"] = "no skill goal available"
                return False, "WANT no skill goal available", details
            return False, "WANT no skill goal available"
        # choose_skill_goal returns (skill_key, reason)
        if isinstance(choice, (tuple, list)) and len(choice) >= 1:
            skill_key = choice[0]
            reason = choice[1] if len(choice) > 1 else ""
        else:
            skill_key = choice
            reason = ""
        if not skill_key:
            if return_details:
                details["failure_reason"] = "no skill goal available"
                return False, "WANT no skill goal available", details
            return False, "WANT no skill goal available"
        ok = try_push_skill_interaction(
            sim, skill_key, force=True, probe_details=details if return_details else None
        )
        if ok:
            message = (
                f"WANT skill {skill_key} ({reason})" if reason else f"WANT skill {skill_key}"
            )
            if return_details:
                return True, message, details
            return True, message
        message = (
            f"WANT skill push failed for {skill_key} ({reason})"
            if reason
            else f"WANT skill push failed for {skill_key}"
        )
        if return_details:
            details["failure_reason"] = message
            return False, message, details
        return False, message

    if rule_key == "clean":
        ok = try_push_clean_interaction(sim, probe_details=details if return_details else None)
        if return_details:
            if ok:
                details["resolution_type"] = "OTHER_RULE"
                return True, None, details
            details.setdefault("failure_reason", "WANT clean push failed")
            return False, "WANT clean push failed", details
        return (True, None) if ok else (False, "WANT clean push failed")

    if (
        rule_key == "social"
        and hasattr(settings, "director_allow_social_wants")
        and not settings.director_allow_social_wants
    ):
        if return_details:
            details["failure_reason"] = "WANT social disabled"
            return False, "WANT social disabled", details
        return False, "WANT social disabled"
    if rule_key == "social":
        target_sim = None
        target_id = _get_want_target_sim_id(want_obj)
        if target_id:
            target_sim = _resolve_whim_target_sim_instance(target_id)
            if target_sim is None:
                if return_details:
                    details["resolution_type"] = "FAIL"
                    details["failure_reason"] = "whim_target_sim not instanced"
                    details["target_type"] = "sim"
                    details["target_label"] = f"id={target_id}"
                    return False, "WANT social target not instanced", details
                return False, "WANT social target not instanced"
        if target_sim is None:
            target_sim = _find_target_sim(sim)
            if target_sim is None:
                if return_details:
                    details["resolution_type"] = "FAIL"
                    details["failure_reason"] = "no target sim"
                    return False, "WANT social no target sim", details
                return False, "WANT social no target sim"

        prefer_romance = "romance" in (want_name or "").lower() or "flirt" in (
            want_name or ""
        ).lower()
        candidates = _collect_social_affordances(sim, target_sim)
        selected = None
        if candidates:
            keywords = ["romance", "flirt"] if prefer_romance else [
                "chat",
                "talk",
                "social",
                "friendly",
            ]
            for aff in candidates:
                try:
                    name = affordance_name(aff)
                except Exception:
                    continue
                if is_picker_affordance(aff):
                    continue
                if _is_blocked_want_affordance(aff):
                    continue
                if "mentor" in name or "offer" in name or "picker" in name:
                    continue
                if any(keyword in name for keyword in keywords):
                    selected = aff
                    break
        if selected is None:
            if return_details:
                details["resolution_type"] = "FAIL"
                details["target_type"] = "sim"
                details["target_label"] = _get_sim_probe_label(target_sim)
                details["candidate_affordances"] = [
                    affordance_name(aff) for aff in candidates[:30]
                ]
                details["failure_reason"] = "no social affordance match"
                return False, "WANT social no affordance candidates", details
            return False, "WANT social no affordance candidates"
        ok, failure_reason, sig_names, _client_attached = _push_affordance_with_details(
            sim, target_sim, selected, reason=rule_key, force=force
        )
        if return_details:
            details["resolution_type"] = "SOCIAL_TARGETED"
            details["target_type"] = "sim"
            details["target_label"] = _get_sim_probe_label(target_sim)
            details["push_attempts"] = [
                {
                    "affordance_name": affordance_name(selected),
                    "affordance_class": _get_affordance_class_name(selected),
                    "affordance_is_picker": is_picker_affordance(selected),
                    "push_ok": ok,
                    "push_sig_names": list(sig_names or []),
                    "push_reason": failure_reason,
                }
            ]
        if ok:
            if return_details:
                return True, "WANT social", details
            return True, "WANT social"
        if return_details:
            details["failure_reason"] = failure_reason or "push returned False"
            return False, "WANT social push failed", details
        return False, "WANT social push failed"
    rule = _WHIM_RULES.get(rule_key)
    if rule is None:
        if return_details:
            details["failure_reason"] = "WANT no rule"
            return False, "WANT no rule", details
        return False, "WANT no rule"
    if rule.get("target_type") == "sim":
        target_sim = _find_target_sim(sim)
        if target_sim is None:
            if return_details:
                details["failure_reason"] = f"WANT {rule_key} no target sim"
                details["resolution_type"] = "FAIL"
                return False, f"WANT {rule_key} no target sim", details
            return False, f"WANT {rule_key} no target sim"
        candidates = find_affordance_candidates(
            target_sim, rule.get("affordance_keywords", []), sim=sim
        )
        candidates = [
            aff
            for aff in candidates
            if not _is_blocked_want_affordance(aff) and not is_picker_affordance(aff)
        ]
        if not candidates:
            if return_details:
                details["failure_reason"] = f"WANT {rule_key} no affordance candidates"
                details["resolution_type"] = "FAIL"
                details["target_type"] = "sim"
                details["target_label"] = _get_sim_probe_label(target_sim)
                return False, f"WANT {rule_key} no affordance candidates", details
            return False, f"WANT {rule_key} no affordance candidates"
        for affordance in candidates[:8]:
            ok, failure_reason, sig_names, _client_attached = _push_affordance_with_details(
                sim, target_sim, affordance, reason=rule_key, force=force
            )
            if return_details:
                details["resolution_type"] = "OTHER_RULE"
                details["target_type"] = "sim"
                details["target_label"] = _get_sim_probe_label(target_sim)
                details.setdefault("push_attempts", []).append(
                    {
                        "affordance_name": affordance_name(affordance),
                        "affordance_class": _get_affordance_class_name(affordance),
                        "affordance_is_picker": is_picker_affordance(affordance),
                        "push_ok": ok,
                        "push_sig_names": list(sig_names or []),
                        "push_reason": failure_reason,
                    }
                )
            if ok:
                _LAST_WANT_DETAILS = (
                    want_name,
                    _get_object_label(target_sim),
                    _get_affordance_label(affordance),
                )
                if return_details:
                    return True, f"WANT {want_name}", details
                return True, f"WANT {want_name}"
        if return_details:
            details["failure_reason"] = f"WANT {rule_key} no affordance candidates"
            return False, f"WANT {rule_key} no affordance candidates", details
        return False, f"WANT {rule_key} no affordance candidates"

    target_obj = _find_target_object(sim, rule)
    if target_obj is None:
        if return_details:
            details["failure_reason"] = f"WANT {rule_key} no object match"
            details["resolution_type"] = "FAIL"
            return False, f"WANT {rule_key} no object match", details
        return False, f"WANT {rule_key} no object match"
    candidates = find_affordance_candidates(
        target_obj, rule.get("affordance_keywords", []), sim=sim
    )
    candidates = [
        aff
        for aff in candidates
        if not _is_blocked_want_affordance(aff) and not is_picker_affordance(aff)
    ]
    if not candidates:
        if return_details:
            details["failure_reason"] = f"WANT {rule_key} no affordance candidates"
            details["resolution_type"] = "FAIL"
            details["target_type"] = "object"
            details["target_label"] = _get_object_probe_label(target_obj)
            return False, f"WANT {rule_key} no affordance candidates", details
        return False, f"WANT {rule_key} no affordance candidates"
    affordance = candidates[0]
    ok, failure_reason, sig_names, _client_attached = _push_affordance_with_details(
        sim, target_obj, affordance, reason=rule_key, force=force
    )
    if ok:
        _LAST_WANT_DETAILS = (
            want_name,
            _get_object_label(target_obj),
            _get_affordance_label(affordance),
        )
    if return_details:
        details["resolution_type"] = "OTHER_RULE"
        details["target_type"] = "object"
        details["target_label"] = _get_object_probe_label(target_obj)
        details["push_attempts"] = [
            {
                "affordance_name": affordance_name(affordance),
                "affordance_class": _get_affordance_class_name(affordance),
                "affordance_is_picker": is_picker_affordance(affordance),
                "push_ok": ok,
                "push_sig_names": list(sig_names or []),
                "push_reason": failure_reason,
            }
        ]
        if not ok:
            details["failure_reason"] = failure_reason or "push returned False"
        return ok, f"WANT {want_name}", details
    return ok, f"WANT {want_name}"


def _is_blocked_want_affordance(affordance):
    label = _get_affordance_label(affordance)
    return "cheat" in label or "debug" in label


def _try_resolve_wants(sim_info, force=False, now=None):
    if sim_info is None:
        return False, "WANT unavailable (no active wants or wants disabled)"
    sim = sim_info.get_sim_instance()
    if sim is None:
        return False, "WANT unavailable (no active sim)"
    want_targets, want_reason = _select_want_targets(sim_info)
    if not want_targets:
        return False, want_reason or "WANT unavailable (no active wants or wants disabled)"
    sim_name = _sim_display_name(sim_info)
    last_message = want_reason
    for want_key, want_name, _want_obj in want_targets:
        rule_key = _resolve_whim_rule(want_name)
        if rule_key is None:
            continue
        guid64 = _get_whim_guid64(_want_obj)
        tuning = _resolve_whim_tuning_by_guid64(guid64)
        tuning_name = getattr(tuning, "__name__", None) if tuning is not None else None
        whim_target_sim = _get_want_target_sim_id(_want_obj)
        _log_probe(
            "WANT_NOW want_label={} want_guid64={} want_tuning={} whim_target_sim={}".format(
                want_name, guid64, tuning_name, whim_target_sim
            )
        )
        pushed, want_message, details = _push_want(
            sim,
            rule_key,
            want_name,
            want_obj=_want_obj,
            force=force,
            return_details=True,
        )
        resolution_type = details.get("resolution_type", "FAIL")
        _log_probe(f"WANT_NOW resolution_type={resolution_type}")
        if "object_scan_count" in details:
            _log_probe(
                "WANT_NOW object_scan_count={} object_keywords={}".format(
                    details.get("object_scan_count"), details.get("object_keywords")
                )
            )
        target_label = details.get("target_label")
        if target_label:
            _log_probe(
                "WANT_NOW target_type={} target={}".format(
                    details.get("target_type"), target_label
                )
            )
        if details.get("candidate_affordances"):
            _log_probe(
                "WANT_NOW candidate_affordances={}".format(
                    details.get("candidate_affordances")
                )
            )
        for attempt in details.get("push_attempts", []):
            _log_probe(
                "WANT_NOW affordance_name={} affordance_class={} is_picker={} push_sig_names={} push_ok={} push_reason={}".format(
                    attempt.get("affordance_name"),
                    attempt.get("affordance_class"),
                    attempt.get("affordance_is_picker"),
                    attempt.get("push_sig_names"),
                    attempt.get("push_ok"),
                    attempt.get("push_reason"),
                )
            )
        if details.get("failure_reason"):
            _log_probe(f"WANT_NOW failure_reason={details.get('failure_reason')}")
        last_message = want_message
        if pushed:
            sim_id = _sim_identifier(sim_info)
            now = time.time() if now is None else now
            _record_push(sim_id, now)
            action = f"{sim_name} -> WANT {want_name}"
            _append_action(action)
            global last_director_time
            last_director_time = now
            return True, want_message
    return False, last_message or "WANT no supported target"


def _get_object_label(obj):
    parts = [getattr(obj.__class__, "__name__", None)]
    definition = getattr(obj, "definition", None)
    if definition is not None:
        name = getattr(definition, "name", None)
        if name:
            parts.append(name)
    parts.append(str(obj))
    return " ".join(part for part in parts if part).lower()


def _get_object_probe_label(obj):
    if obj is None:
        return "none"
    definition = getattr(obj, "definition", None)
    definition_name = getattr(definition, "name", None) if definition is not None else None
    object_id = getattr(obj, "id", None) or getattr(obj, "object_id", None)
    pieces = []
    if definition_name:
        pieces.append(str(definition_name))
    if object_id is not None:
        pieces.append(f"id={object_id}")
    return " ".join(pieces) or str(obj)


def _get_sim_probe_label(sim):
    if sim is None:
        return "none"
    full_name = getattr(sim, "full_name", None)
    if callable(full_name):
        try:
            full_name = full_name()
        except Exception:
            full_name = None
    full_name = full_name or getattr(sim, "first_name", None) or "Sim"
    sim_id = None
    sim_info = getattr(sim, "sim_info", None)
    if sim_info is not None:
        sim_id = _sim_identifier(sim_info)
    if sim_id is None:
        sim_id = getattr(sim, "id", None) or getattr(sim, "sim_id", None)
    return f"{full_name} id={sim_id}" if sim_id is not None else full_name


def _get_affordance_class_name(affordance):
    if affordance is None:
        return "None"
    if inspect.isclass(affordance):
        return affordance.__name__
    return getattr(type(affordance), "__name__", str(affordance))


def _log_probe(line):
    probe_log.log_probe(line)


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


def _find_target_object(sim, rule, objects=None):
    keywords = rule.get("object_keywords", [])
    if not keywords:
        return None
    best = None
    best_distance = None
    for obj in objects if objects is not None else iter_objects():
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


def _push_affordance_with_details(sim, target_obj, affordance, reason=None, force=False):
    try:
        src = InteractionSource.PIE_MENU if force else InteractionSource.SCRIPT
    except Exception:
        src = InteractionContext.SOURCE_PIE_MENU if force else InteractionContext.SOURCE_SCRIPT
    context, client_attached = make_interaction_context(sim, force=force, source=src)
    ok, failure_reason, sig_names = call_push_super_affordance(
        sim, affordance, target_obj, context
    )
    if ok:
        global _LAST_ACTION_DETAILS
        _LAST_ACTION_DETAILS = (
            _get_object_label(target_obj),
            _get_affordance_label(affordance),
        )
        return True, None, sig_names, client_attached
    failure_reason = failure_reason or "unknown failure"
    sim_name = getattr(sim, "full_name", None)
    if callable(sim_name):
        try:
            sim_name = sim_name()
        except Exception:
            sim_name = None
    sim_name = sim_name or getattr(sim, "first_name", None) or "Sim"
    target_class = getattr(target_obj.__class__, "__name__", "unknown")
    aff_label = _get_affordance_label(affordance)
    _append_debug(
        f"{sim_name}: push failed obj_class={target_class} "
        f"aff={aff_label} reason={failure_reason} client_attached={client_attached} "
        f"sig_names={sig_names}"
    )
    return False, failure_reason, sig_names, client_attached


def try_push_skill_interaction(sim, skill_key, force=False, probe_details=None):
    sim_name = getattr(sim, "full_name", None)
    if callable(sim_name):
        try:
            sim_name = sim_name()
        except Exception:
            sim_name = None
    sim_name = sim_name or getattr(sim, "first_name", None) or "Sim"
    resolution = skills.resolve_skill_action(sim, skill_key)
    rule = skills.SKILL_RULES.get(skill_key)
    reason = resolution.get("reason")
    if reason == "no_rule":
        _append_debug(f"{sim_name}: FAIL no rule for skill={skill_key}")
        if probe_details is not None:
            probe_details["resolution_type"] = "FAIL"
            probe_details["failure_reason"] = "no rule for skill key"
        return False
    if reason == "no_object":
        if probe_details is not None:
            probe_details["resolution_type"] = "FAIL"
            probe_details["object_keywords"] = list(rule.get("object_keywords", [])) if rule else []
            probe_details["failure_reason"] = "no object match"
        _append_debug(f"{sim_name}: FAIL no object for skill={skill_key}")
        return False
    if reason == "no_affordance":
        if probe_details is not None:
            probe_details["resolution_type"] = "FAIL"
            probe_details["failure_reason"] = "no safe affordance candidates"
        _append_debug(f"{sim_name}: FAIL no affordance for skill={skill_key}")
        return False
    target_obj = resolution.get("target_obj")
    affordances = list(resolution.get("affordances") or [])
    if probe_details is not None:
        probe_details["resolution_type"] = "SKILL_OBJECT"
        probe_details["target_label"] = _get_object_probe_label(target_obj)
        probe_details["target_type"] = "object"
        probe_details["push_attempts"] = []
        probe_details["candidate_affordances"] = [
            {
                "affordance_name": affordance_name(aff),
                "affordance_class": _get_affordance_class_name(aff),
                "blocked_reason": None,
                "requires_target_sim": False,
            }
            for aff in affordances
        ]
    if not affordances:
        _append_debug(f"{sim_name}: FAIL no affordance for skill={skill_key}")
        if probe_details is not None:
            probe_details["failure_reason"] = "no safe affordance candidates"
        return False
    for affordance in affordances[:8]:
        ok, failure_reason, sig_names, _client_attached = _push_affordance_with_details(
            sim, target_obj, affordance, reason=skill_key, force=force
        )
        if probe_details is not None:
            probe_details["push_attempts"].append(
                {
                    "affordance_name": affordance_name(affordance),
                    "affordance_class": _get_affordance_class_name(affordance),
                    "affordance_is_picker": is_picker_affordance(affordance),
                    "push_ok": ok,
                    "push_sig_names": list(sig_names or []),
                    "push_reason": failure_reason,
                }
            )
        if ok:
            if probe_details is not None:
                probe_details["chosen_affordance"] = affordance_name(affordance)
                probe_details["chosen_affordance_class"] = _get_affordance_class_name(affordance)
            _append_debug(
                f"{sim_name}: SUCCESS push skill={skill_key} aff={_aff_label(affordance)}"
            )
            return True
        _append_debug(
            f"{sim_name}: candidate failed skill={skill_key} aff={_aff_label(affordance)}"
        )
    _append_debug(f"{sim_name}: FAIL all candidates failed for skill={skill_key}")
    if probe_details is not None:
        probe_details["failure_reason"] = "all candidates failed"
    return False


def try_push_clean_interaction(sim, probe_details=None):
    sim_name = getattr(sim, "full_name", None)
    if callable(sim_name):
        try:
            sim_name = sim_name()
        except Exception:
            sim_name = None
    sim_name = sim_name or getattr(sim, "first_name", None) or "Sim"

    # Scan objects and look for any viable “clean” affordance.
    # We deliberately search affordances rather than trying to detect “dirty” state,
    # because different object types expose different dirtiness APIs.
    clean_aff_keywords = [
        "clean",
        "clean up",
        "mop",
        "wash",
        "wash dishes",
        "do laundry",
        "laundry",
        "throw away",
        "take out trash",
    ]

    for obj in iter_objects():
        try:
            candidates = find_affordance_candidates(obj, clean_aff_keywords, sim=sim)
        except Exception:
            continue
        if not candidates:
            continue
        filtered = [aff for aff in candidates if not is_picker_affordance(aff)]
        if not filtered:
            continue
        aff = filtered[0]
        ok, reason, sig, _client_attached = _push_affordance_with_details(
            sim, obj, aff, reason="clean", force=False
        )
        if probe_details is not None:
            probe_details["resolution_type"] = "OTHER_RULE"
            probe_details["target_type"] = "object"
            probe_details["target_label"] = _get_object_probe_label(obj)
            probe_details["push_attempts"] = [
                {
                    "affordance_name": affordance_name(aff),
                    "affordance_class": _get_affordance_class_name(aff),
                    "affordance_is_picker": is_picker_affordance(aff),
                    "push_ok": ok,
                    "push_sig_names": list(sig or []),
                    "push_reason": reason,
                }
            ]
        if ok:
            _append_debug(f"{sim_name}: WANT clean -> pushed {affordance_name(aff)} on {obj}")
            return True
        _append_debug(f"{sim_name}: WANT clean push failed: {reason} sig={sig}")
    _append_debug(f"{sim_name}: WANT clean no eligible object/affordance found in zone")
    if probe_details is not None:
        probe_details["failure_reason"] = "no clean affordance candidates"
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


def _log_started_skill_order(candidates):
    total = len(candidates)
    preview = ", ".join(
        f"({skill_key},{level})" for skill_key, _reason, level in candidates[:20]
    )
    _append_debug(f"Director: started_skills_order=[{preview}] total={total}")


def _log_try_skill(
    source,
    skill_key,
    level,
    result,
    obj_label=None,
    aff_label=None,
    details=None,
    push_signature=None,
    push_ok=None,
):
    obj_label = obj_label or "none"
    aff_label = aff_label or "none"
    details = details or "none"
    parts = [
        f"TrySkill source={source}",
        f"skill={skill_key}",
        f"level={level}",
        f"result={result}",
        f"obj={obj_label}",
        f"aff={aff_label}",
        f"details={details}",
    ]
    if push_ok is not None:
        parts.append(f"push_ok={push_ok}")
    if push_signature is not None:
        parts.append(f"push_signature={push_signature}")
    _append_debug(" ".join(parts))


def _attempt_skill_candidate(sim, skill_key, level, source, force=False):
    resolution = skills.resolve_skill_action(sim, skill_key)
    reason = resolution.get("reason")
    if reason == "no_rule":
        _log_try_skill(
            source,
            skill_key,
            level,
            "no_object",
            details="no_rule_for_skill_key",
        )
        return {"success": False, "result": "no_object"}
    if reason == "no_object":
        _log_try_skill(
            source,
            skill_key,
            level,
            "no_object",
            details="no_matching_object",
        )
        return {"success": False, "result": "no_object"}
    if reason == "no_affordance":
        _log_try_skill(
            source,
            skill_key,
            level,
            "no_affordance",
            details="no_safe_affordance",
        )
        return {"success": False, "result": "no_affordance"}
    target_obj = resolution.get("target_obj")
    affordances = list(resolution.get("affordances") or [])
    if not affordances:
        _log_try_skill(
            source,
            skill_key,
            level,
            "no_affordance",
            details="no_safe_affordance",
        )
        return {"success": False, "result": "no_affordance"}
    last_failure = None
    last_sig = None
    last_aff_label = None
    for affordance in affordances[:8]:
        ok, failure_reason, sig_names, _client_attached = _push_affordance_with_details(
            sim, target_obj, affordance, reason=skill_key, force=force
        )
        last_aff_label = affordance_name(affordance)
        last_sig = list(sig_names or [])
        if ok:
            _log_try_skill(
                source,
                skill_key,
                level,
                "success",
                obj_label=_get_object_probe_label(target_obj),
                aff_label=last_aff_label,
                details="push_ok",
                push_signature=last_sig,
                push_ok=True,
            )
            return {"success": True, "result": "success"}
        last_failure = failure_reason or "push_failed"
    _log_try_skill(
        source,
        skill_key,
        level,
        "push_failed",
        obj_label=_get_object_probe_label(target_obj),
        aff_label=last_aff_label,
        details=last_failure,
        push_signature=last_sig,
        push_ok=False,
    )
    return {"success": False, "result": "push_failed"}


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


def get_last_career_probe():
    return list(_LAST_CAREER_PROBE)


def build_plan_preview(sim, now=None):
    if sim is None:
        return None
    sim_info = getattr(sim, "sim_info", None)
    if sim_info is None:
        return None
    now = time.time() if now is None else now
    sim_id = _sim_identifier(sim_info)
    snapshot = _get_motive_snapshot(sim_info)
    min_motive = _safe_min_motive(snapshot) if snapshot else None
    green_count = 0
    for _key, value in snapshot or []:
        if guardian.motive_is_green(value, settings.director_green_motive_percent):
            green_count += 1
    motive_unsafe = (
        min_motive is not None and min_motive < settings.director_min_safe_motive
    )
    busy, busy_reason = _is_sim_busy(sim)
    last_push = _per_sim_last_push_time.get(sim_id)
    cooldown_ok = _can_push_for_sim(sim_id, now)
    time_since_last_push = None
    if last_push is not None:
        time_since_last_push = now - last_push
    plan = "NONE"
    plan_reason = "no plan"
    chosen_skill = None
    candidates = []
    if motive_unsafe:
        if settings.director_use_guardian_when_low and settings.guardian_enabled:
            plan = "GUARDIAN"
            plan_reason = "unsafe motive"
        else:
            plan = "NONE"
            plan_reason = "unsafe motive; guardian disabled"
    else:
        if snapshot and green_count >= settings.director_green_min_commodities:
            chosen_skill, candidates = build_skill_plan(sim_info)
            if chosen_skill is not None:
                plan = "SKILLS"
                plan_reason = chosen_skill[1]
            else:
                plan_reason = "no skill candidates"
        else:
            plan_reason = "green gate not met"
    return {
        "sim_id": sim_id,
        "busy": busy,
        "busy_reason": busy_reason,
        "cooldown_ok": cooldown_ok,
        "time_since_last_push": time_since_last_push,
        "min_motive": min_motive,
        "motive_unsafe": motive_unsafe,
        "green_count": green_count,
        "motive_total": len(snapshot or []),
        "plan": plan,
        "plan_reason": plan_reason,
        "chosen_skill": chosen_skill,
        "candidates": candidates,
        "snapshot": snapshot or [],
    }


def _evaluate(now: float, force: bool = False):
    global last_director_time
    _DEBUG_RING.clear()
    last_director_debug[:] = []
    settings.last_director_debug = ""
    actions_before = len(last_director_actions)
    sims = _get_instantiated_sims_for_director()
    if not sims:
        _dbg("Director ran: no eligible sims")
        return

    for sim in sims:
        try:
            sim_info = getattr(sim, "sim_info", None)
            if sim_info is None:
                continue
            sim_name = _sim_display_name(sim_info)

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
                motive_unsafe = (
                    min_motive is not None
                    and min_motive < settings.director_min_safe_motive
                )
                if motive_unsafe:
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
                        _dbg(f"{sim_name}: CARE disabled (unsafe motive)")
                    continue
                if green_count < settings.director_green_min_commodities:
                    _dbg(
                        f"{sim_name}: SKIP green gate "
                        f"({green_count}/{len(snapshot)} < {settings.director_green_min_commodities})"
                    )
                    continue
            else:
                _dbg(f"{sim_name}: SKIP motives unreadable (no motive stats found)")
                continue

            busy, busy_reason = _is_sim_busy(sim)
            if busy:
                _dbg(f"{sim_name}: SKIP busy ({busy_reason})")
                continue

            if not _can_push_for_sim(sim_id, now):
                _dbg(f"{sim_name}: SKIP cooldown")
                continue

            result = run_skill_plan(sim_info, sim, now, force=force, source="director")
            if result.get("success"):
                skill_key = result.get("skill_key")
                reason = result.get("skill_reason")
                _record_push(sim_id, now)
                _record_action(sim_info, skill_key, reason, now)
            else:
                _dbg(f"{sim_name}: SKILL plan failed")
        except Exception:
            continue
    if not last_director_debug and len(last_director_actions) == actions_before:
        _dbg(f"Director ran: {len(sims)} eligible sims")


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
