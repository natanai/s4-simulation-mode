import inspect
import re
import time
from collections import deque

from interactions.context import (
    InteractionContext,
    InteractionSource,
)
import services
import sims4.resources

from simulation_mode import clock_utils
from simulation_mode import guardian
from simulation_mode import probe_log
from simulation_mode import capabilities
from simulation_mode.push_utils import (
    affordance_name,
    call_push_super_affordance,
    find_affordance_candidates,
    is_picker_affordance,
    iter_objects,
    iter_super_affordances,
    make_interaction_context,
    push_by_def_and_aff_guid,
)
from simulation_mode.settings import settings


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
_LAST_SKILL_PLAN_STRICT = None

_last_motive_snapshot_by_sim = {}
_LAST_CAREER_PROBE = []
_LAST_STARTED_SKILL_VALUES = {}
_LAST_STARTED_SKILL_GUID_MISSING = 0
_recent_skill_plans = {}
_PENDING_SKILL_PLAN_VERIFY_ALARMS = {}

_WINDOW_SECONDS = 3600
_BUSY_BUFFER = 10
_SKILL_PLAN_COOLDOWN_SECONDS = 1800
_SKILL_PLAN_VERIFY_SIM_MINUTES = 30
_SKILL_PLAN_VERIFY_REAL_SECONDS = 30

_CACHED_WHIM_MANAGER = None
_CACHED_WHIM_TYPE_NAME = None


def _norm(s: str) -> str:
    s = (s or "").lower()
    return re.sub(r"[\s_\-]+", "", s)


def _safe_get(obj, name, default=None):
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _safe_call(obj, name, *args, **kwargs):
    fn = _safe_get(obj, name)
    if not callable(fn):
        return False, None, f"not callable: {name}"
    try:
        return True, fn(*args, **kwargs), None
    except Exception as exc:
        return False, None, f"{type(exc).__name__}: {exc}"


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
    type_name = ""
    try:
        type_name = type(interaction).__name__.lower()
    except Exception:
        type_name = ""
    if type_name == "emotion_idle":
        return True
    if type_name.startswith("idle_"):
        return True
    if type_name.endswith("_idle") or type_name.endswith("idle"):
        return True

    affordance = None
    for attr in ("affordance", "_affordance"):
        affordance = getattr(interaction, attr, None)
        if affordance is not None:
            break
    if affordance is None:
        getter = getattr(interaction, "get_affordance", None)
        if callable(getter):
            try:
                affordance = getter()
            except Exception:
                affordance = None
    if affordance is not None:
        aff_name = None
        try:
            aff_name = getattr(affordance, "__name__", None)
        except Exception:
            aff_name = None
        if not aff_name:
            try:
                aff_name = getattr(affordance, "name", None)
            except Exception:
                aff_name = None
        if not aff_name:
            try:
                aff_name = str(affordance)
            except Exception:
                aff_name = None
        if aff_name:
            aff_name = str(aff_name).lower()
            if aff_name == "sim-stand":
                return True
            if aff_name == "idle" or aff_name.endswith("idle") or "_idle" in aff_name:
                return True
    return False


def _interaction_affordance_name(interaction, limit=80):
    affordance = None
    for attr in ("affordance", "_affordance"):
        affordance = getattr(interaction, attr, None)
        if affordance is not None:
            break
    if affordance is None:
        getter = getattr(interaction, "get_affordance", None)
        if callable(getter):
            try:
                affordance = getter()
            except Exception:
                affordance = None
    if affordance is None:
        return None
    name = None
    try:
        name = getattr(affordance, "__name__", None)
    except Exception:
        name = None
    if not name:
        try:
            name = getattr(affordance, "name", None)
        except Exception:
            name = None
    if not name:
        try:
            name = str(affordance)
        except Exception:
            name = None
    if not name:
        return None
    name = str(name).strip()
    if len(name) > limit:
        return f"{name[:limit]}..."
    return name


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
    interaction = None
    source = None
    if isinstance(running, (list, tuple)):
        if running:
            interaction = running[0]
            source = "queue.running"
    elif running is not None:
        interaction = running
        source = "queue.running"

    if interaction is None:
        interaction, source = _get_current_interaction(sim)
        if interaction is None:
            queue_len = _queue_size(sim)
            detail = f"no active interaction source=get_current_interaction:{source} queue_len={queue_len}"
            return False, detail
        source = f"get_current_interaction:{source}"

    idle = _interaction_is_idle(interaction)
    queue_len = _queue_size(sim)
    if queue_len is not None and queue_len > 0:
        aff_name = _interaction_affordance_name(interaction)
        detail = (
            "queued interactions present "
            f"source={source} type={type(interaction).__name__} "
            f"current_idle={idle} queue_len={queue_len}"
        )
        if aff_name:
            detail = f"{detail} aff_name={aff_name}"
        return True, detail
    if idle:
        aff_name = _interaction_affordance_name(interaction)
        detail = (
            "idle interaction "
            f"source={source} type={type(interaction).__name__} idle=True queue_len={queue_len}"
        )
        if aff_name:
            detail = f"{detail} aff_name={aff_name}"
        return False, detail
    aff_name = _interaction_affordance_name(interaction)
    detail = (
        "active interaction "
        f"source={source} type={type(interaction).__name__} idle=False queue_len={queue_len}"
    )
    if aff_name:
        detail = f"{detail} aff_name={aff_name}"
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


def _is_primitive(value):
    return isinstance(value, (str, int, float, bool))


def _pick_skill_entry(candidate):
    if isinstance(candidate, (list, tuple)) and len(candidate) == 2:
        first, second = candidate

        def _looks_skill(value):
            if value is None:
                return False
            if getattr(value, "guid64", None) is not None:
                return True
            if not _is_primitive(value):
                return True
            return False

        first_skill = _looks_skill(first)
        second_skill = _looks_skill(second)
        if first_skill and not second_skill:
            return first
        if second_skill and not first_skill:
            return second
        if first_skill:
            return first
        if second_skill:
            return second
        return first
    return candidate


def _skill_guid64(skill_obj):
    if skill_obj is None:
        return None
    if isinstance(skill_obj, (list, tuple)) and len(skill_obj) == 2:
        return _skill_guid64(_pick_skill_entry(skill_obj))
    if isinstance(skill_obj, int) and not isinstance(skill_obj, bool):
        return int(skill_obj)
    guid = getattr(skill_obj, "guid64", None)
    if guid is not None and not isinstance(guid, bool):
        try:
            return int(guid)
        except Exception:
            return None
    for attr in ("skill_type", "stat_type"):
        skill_type = getattr(skill_obj, attr, None)
        if skill_type is None:
            continue
        skill_type_guid = getattr(skill_type, "guid64", None)
        if skill_type_guid is None or isinstance(skill_type_guid, bool):
            continue
        try:
            return int(skill_type_guid)
        except Exception:
            continue
    return None


def _skill_label(skill_obj):
    if skill_obj is None:
        return None
    for attr in ("name", "display_name", "skill_name"):
        value = getattr(skill_obj, attr, None)
        if value is None:
            continue
        try:
            value = value() if callable(value) else value
        except Exception:
            continue
        if value:
            return str(value)
    class_name = getattr(getattr(skill_obj, "__class__", None), "__name__", None)
    if class_name:
        return class_name
    try:
        return str(skill_obj)
    except Exception:
        return None


def _skill_is_allowed(skill_obj):
    allow_list = settings.director_skill_allow_list or []
    block_list = settings.director_skill_block_list or []
    if not allow_list and not block_list:
        return True
    guid = _skill_guid64(skill_obj)
    class_name = getattr(getattr(skill_obj, "__class__", None), "__name__", None)
    tokens = set()
    if guid is not None:
        tokens.add(str(guid))
    if class_name:
        tokens.add(class_name.lower())
    allow_set = {str(item).strip().lower() for item in allow_list if str(item).strip()}
    block_set = {str(item).strip().lower() for item in block_list if str(item).strip()}
    if allow_set and not (tokens & allow_set):
        return False
    if block_set and (tokens & block_set):
        return False
    return True


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


def _skill_value_from_skill(skill, skill_tracker=None):
    getter = getattr(skill, "get_skill_value", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    value = getattr(skill, "skill_value", None)
    if value is not None:
        try:
            return value() if callable(value) else value
        except Exception:
            return None
    if skill_tracker is not None:
        tracker_getter = getattr(skill_tracker, "get_skill_value", None)
        if callable(tracker_getter):
            try:
                return tracker_getter(skill)
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


def _tuning_is_skill(tuning):
    if tuning is None:
        return False
    attr = _safe_get(tuning, "is_skill")
    if attr is not None:
        try:
            if bool(attr() if callable(attr) else attr):
                return True
        except Exception:
            pass
    module = (
        _safe_get(tuning, "__module__")
        or _safe_get(_safe_get(tuning, "__class__"), "__module__")
        or ""
    ).lower()
    name = (
        _safe_get(tuning, "__name__")
        or _safe_get(_safe_get(tuning, "__class__"), "__name__")
        or ""
    ).lower()
    if ("skill" in module or "skill" in name) and not (
        "commodity" in module or "commodity" in name
    ):
        return True
    for attr_name in ("skill_value", "get_skill_value", "max_level", "get_max_level"):
        if hasattr(tuning, attr_name):
            return True
    return False


def _iter_skill_like_statistics(sim_info):
    tracker = _safe_get(sim_info, "commodity_tracker")
    if tracker is None:
        return
    ok, stats, _err = _safe_call(tracker, "get_all_commodities")
    if not ok or stats is None:
        stats = []
    try:
        stats_list = list(stats)
    except Exception:
        stats_list = []
    stat_mgr = services.get_instance_manager(sims4.resources.Types.STATISTIC)
    for stat in stats_list:
        guid = _skill_guid64(stat)
        if guid is None:
            continue
        tuning = stat_mgr.get(guid) if stat_mgr is not None else None
        if tuning is None:
            continue
        if _tuning_is_skill(tuning):
            yield stat


def _resolve_skill_tracker(sim_info):
    tracker = _safe_get(sim_info, "skill_tracker")
    if tracker is not None:
        return tracker, "sim_info.skill_tracker"
    tracker = _safe_get(sim_info, "_skill_tracker")
    if tracker is not None:
        return tracker, "sim_info._skill_tracker"
    ok, sim, _err = _safe_call(sim_info, "get_sim_instance")
    if ok and sim is not None:
        tracker = _safe_get(sim, "skill_tracker")
        if tracker is not None:
            return tracker, "sim_instance.skill_tracker"
        tracker = _safe_get(sim, "_skill_tracker")
        if tracker is not None:
            return tracker, "sim_instance._skill_tracker"
    return None, "none"


def _skill_from_handle(skill_tracker, handle):
    if handle is None:
        return None
    return _pick_skill_entry(handle)


def _iter_all_skill_objects(sim_info):
    tracker, _source = _resolve_skill_tracker(sim_info)
    seen_guids = set()
    if tracker is not None:
        for handle in _iter_skill_handles(tracker):
            skill = _skill_from_handle(tracker, handle)
            if skill is None:
                continue
            guid = _skill_guid64(skill)
            if guid is not None:
                if guid in seen_guids:
                    continue
                seen_guids.add(guid)
            yield skill
    for stat in _iter_skill_like_statistics(sim_info):
        guid = _skill_guid64(stat)
        if guid is not None and guid in seen_guids:
            continue
        if guid is not None:
            seen_guids.add(guid)
        yield stat


def _skill_level_and_max_for_guid(sim_info, guid64):
    if sim_info is None or not guid64:
        return None, None
    for skill in _iter_all_skill_objects(sim_info):
        skill_guid = _skill_guid64(skill)
        if skill_guid != guid64:
            continue
        level = _skill_level_from_skill(skill)
        max_level = _skill_max_from_skill(skill)
        return level, max_level
    stat_mgr = services.get_instance_manager(sims4.resources.Types.STATISTIC)
    tracker = _safe_get(sim_info, "commodity_tracker")
    tuning = stat_mgr.get(guid64) if stat_mgr is not None else None
    if tuning is not None and tracker is not None:
        ok, stat, _err = _safe_call(tracker, "get_statistic", tuning, add=False)
        if not ok:
            ok, stat, _err = _safe_call(tracker, "get_statistic", tuning)
        if stat is not None:
            level = _skill_level_from_skill(stat)
            max_level = _skill_max_from_skill(stat)
            if level is not None or max_level is not None:
                return level, max_level
    return None, None


def _skill_progress_snapshot(sim_info, guid64):
    if sim_info is None or not guid64:
        return None, None
    tracker, _source = _resolve_skill_tracker(sim_info)
    for skill in _iter_all_skill_objects(sim_info):
        skill_guid = _skill_guid64(skill)
        if skill_guid != guid64:
            continue
        level = _skill_level_from_skill(skill)
        value = _skill_value_from_skill(skill, skill_tracker=tracker)
        return level, value
    level, _max_level = _skill_level_and_max_for_guid(sim_info, guid64)
    return level, None


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


def _walk_for_guid64s(value, max_nodes=400, max_depth=6):
    seen = set()
    found = set()
    stack = [(value, 0)]
    nodes = 0

    while stack and nodes < max_nodes:
        current, depth = stack.pop()
        if current is None:
            continue
        obj_id = id(current)
        if obj_id in seen:
            continue
        seen.add(obj_id)
        nodes += 1

        guid = _skill_guid64(current)
        if guid is not None:
            found.add(guid)

        if depth >= max_depth:
            continue

        if isinstance(current, dict):
            for key, val in current.items():
                stack.append((key, depth + 1))
                stack.append((val, depth + 1))
            continue
        if isinstance(current, (list, tuple, set)):
            for item in current:
                stack.append((item, depth + 1))
            continue

        for attr in (
            "skill",
            "skills",
            "skill_type",
            "statistic",
            "statistics",
            "commodity",
            "commodities",
        ):
            if hasattr(current, attr):
                try:
                    stack.append((getattr(current, attr), depth + 1))
                except Exception:
                    continue

    return found


def _get_career_skill_candidates(sim_info):
    global _LAST_CAREER_PROBE
    _LAST_CAREER_PROBE = []
    if sim_info is None or not settings.director_prefer_career_skills:
        return []
    candidates = set()
    careers = _iter_careers(sim_info)
    if not careers:
        _LAST_CAREER_PROBE.append("career_probe=no careers found")
        return []
    stat_mgr = services.get_instance_manager(sims4.resources.Types.STATISTIC)
    for career in careers:
        career_name = getattr(getattr(career, "__class__", None), "__name__", "") or "Career"
        career_guid = getattr(career, "guid64", None)
        career_tuning = _safe_get(career, "career_tuning")
        current_level_tuning = _safe_get(career, "current_level_tuning")
        next_level_tuning = _safe_get(career, "next_level_tuning")
        track_tuning = _safe_get(career, "current_track") or _safe_get(career, "track_tuning")
        roots = [
            root
            for root in (
                career,
                career_tuning,
                current_level_tuning,
                next_level_tuning,
                track_tuning,
            )
            if root is not None
        ]
        for root in roots:
            candidates.update(_walk_for_guid64s(root))
        _LAST_CAREER_PROBE.append(
            "career_probe=scan roots={roots} guid_candidates={count} career_id={career_id}".format(
                roots=len(roots),
                count=len(candidates),
                career_id=career_guid,
            )
        )
    if not candidates:
        return []
    filtered = []
    for guid in candidates:
        if guid is None:
            continue
        tuning = stat_mgr.get(guid) if stat_mgr is not None else None
        if tuning is None:
            continue
        if _tuning_is_skill(tuning):
            filtered.append(guid)
    if not filtered:
        return []
    filtered.sort(
        key=lambda guid: _skill_level_and_max_for_guid(sim_info, guid)[0]
        if _skill_level_and_max_for_guid(sim_info, guid)[0] is not None
        else 999
    )
    return filtered


def _choose_career_skill(sim_info):
    if sim_info is None or not settings.director_prefer_career_skills:
        return None
    candidates, _satisfied = _filter_unmet_career_skills(
        sim_info, _get_career_skill_candidates(sim_info)
    )
    for guid in candidates:
        return guid, f"career_skill_guid64={guid}"
    return None


def _filter_unmet_career_skills(sim_info, candidates):
    unmet = []
    satisfied = []
    for guid in candidates:
        level, max_level = _skill_level_and_max_for_guid(sim_info, guid)
        max_level = max_level if max_level is not None else 10
        if level is not None and level >= max_level:
            satisfied.append(guid)
            continue
        unmet.append(guid)
    return unmet, satisfied


def _iter_skill_handles(skill_tracker):
    if skill_tracker is None:
        return []
    sources = {}
    for name in ("get_all_skills", "get_all_skill_types", "get_skills"):
        fn = getattr(skill_tracker, name, None)
        if callable(fn):
            try:
                sources[name] = fn()
            except Exception:
                continue
    for name in (
        "_skill_type_to_skill",
        "_skills",
        "skills",
        "_skill_map",
        "_statistics",
        "_statistic_values",
    ):
        if hasattr(skill_tracker, name):
            try:
                sources[name] = getattr(skill_tracker, name)
            except Exception:
                continue
    priority = [
        "get_all_skills",
        "get_all_skill_types",
        "get_skills",
        "_skill_type_to_skill",
        "_skills",
        "skills",
        "_skill_map",
        "_statistics",
        "_statistic_values",
    ]
    for name in priority:
        if name not in sources:
            continue
        value = sources[name]
        if isinstance(value, dict) and value:
            handles = list(value.keys()) + list(value.values())
            return [_pick_skill_entry(handle) for handle in handles if handle is not None]
        if isinstance(value, (list, tuple, set)) and value:
            handles = list(value)
            return [_pick_skill_entry(handle) for handle in handles if handle is not None]
    return []


def _handle_guid64(handle):
    return _skill_guid64(handle)


def _handle_level(handle, skill_tracker, guid64):
    for attr in ("level",):
        value = getattr(handle, attr, None)
        if value is None:
            continue
        try:
            return value() if callable(value) else value
        except Exception:
            continue
    getter = getattr(handle, "get_level", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            pass
    if skill_tracker is not None:
        for target in (handle, guid64):
            if target is None:
                continue
            level = _skill_level_from_tracker(skill_tracker, target)
            if level is not None:
                return level
        tracker_get_value = getattr(skill_tracker, "get_value", None)
        if callable(tracker_get_value):
            for target in (handle, guid64):
                if target is None:
                    continue
                try:
                    value = tracker_get_value(target)
                except Exception:
                    continue
                if value is not None:
                    return value
    return None


def _handle_max_level(handle, skill_tracker, guid64):
    max_level = _skill_max_from_skill(handle)
    if max_level is not None:
        return max_level
    for attr in ("skill_type", "stat_type"):
        value = getattr(handle, attr, None)
        if value is None:
            continue
        max_level = _skill_max_from_skill(value)
        if max_level is not None:
            return max_level
    if skill_tracker is not None:
        for target in (handle, guid64):
            if target is None:
                continue
            max_level = _skill_max_from_tracker(skill_tracker, target)
            if max_level is not None:
                return max_level
    return None


def _get_started_skill_candidates(sim_info):
    if sim_info is None or not settings.director_fallback_to_started_skills:
        return []
    tracker, _source = _resolve_skill_tracker(sim_info)
    candidates = []
    skill_levels = {}
    skill_values = {}
    missing_guid_count = 0
    for skill in _iter_all_skill_objects(sim_info):
        try:
            if not _skill_is_allowed(skill):
                continue
            guid64 = _skill_guid64(skill)
            if guid64 is None:
                missing_guid_count += 1
                continue
            level = _skill_level_from_skill(skill)
            max_level = _skill_max_from_skill(skill)
            if level is None or max_level is None:
                continue
            if level <= 0 or level >= max_level:
                continue
            skill_value = _skill_value_from_skill(skill, skill_tracker=tracker)
            skill_values[id(skill)] = skill_value
            candidates.append(skill)
            skill_levels[id(skill)] = level
        except Exception:
            continue
    candidates.sort(
        key=lambda item: (skill_levels.get(id(item), 999), str(_skill_guid64(item)))
    )
    global _LAST_STARTED_SKILL_VALUES
    _LAST_STARTED_SKILL_VALUES = skill_values
    global _LAST_STARTED_SKILL_GUID_MISSING
    _LAST_STARTED_SKILL_GUID_MISSING = missing_guid_count
    return candidates


def probe_skills(sim_info, limit=30):
    lines = ["SKILLS (PROBE)"]
    tracker, source = _resolve_skill_tracker(sim_info)
    lines.append(f"skill_tracker_source={source}")
    lines.append(f"commodity_tracker_present={bool(_safe_get(sim_info, 'commodity_tracker'))}")

    discovered = []
    seen = set()
    if sim_info is not None:
        if tracker is not None:
            for handle in _iter_skill_handles(tracker):
                skill = _skill_from_handle(tracker, handle)
                if skill is None:
                    continue
                guid = _skill_guid64(skill)
                if guid is None or guid in seen:
                    continue
                seen.add(guid)
                discovered.append((skill, guid, "tracker"))
        for stat in _iter_skill_like_statistics(sim_info):
            guid = _skill_guid64(stat)
            if guid is None or guid in seen:
                continue
            seen.add(guid)
            discovered.append((stat, guid, "commodity"))

    lines.append(f"discovered_skill_count={len(discovered)}")
    for skill, guid, skill_source in discovered[:limit]:
        level = _skill_level_from_skill(skill)
        max_level = _skill_max_from_skill(skill)
        label = _skill_label(skill) or "Skill"
        lines.append(
            "skill={label}(guid64={guid}) level={level} max={max_level} source={source}".format(
                label=label,
                guid=guid,
                level=level,
                max_level=max_level,
                source=skill_source,
            )
        )
    return lines


def probe_skill_tracker(sim_info, limit=25):
    return probe_skills(sim_info, limit=limit)


def _choose_started_skill(sim_info):
    if sim_info is None or not settings.director_fallback_to_started_skills:
        return None
    candidates = _get_started_skill_candidates(sim_info)
    if candidates:
        skill_obj = candidates[0]
        level = _skill_level_from_skill(skill_obj)
        guid = _skill_guid64(skill_obj)
        return guid, f"started_skill_guid64={guid} level={level}"
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
        for guid in career_candidates:
            candidates.append((guid, f"career_skill_guid64={guid}"))
        if career_candidates:
            selected = (career_candidates[0], f"career_skill_guid64={career_candidates[0]}")
    if selected is None and settings.director_fallback_to_started_skills:
        started_candidates = _get_started_skill_candidates(sim_info)
        for skill_obj in started_candidates:
            guid = _skill_guid64(skill_obj)
            level = _skill_level_from_skill(skill_obj)
            candidates.append((guid, f"started_skill_guid64={guid} level={level}"))
        if started_candidates:
            guid = _skill_guid64(started_candidates[0])
            level = _skill_level_from_skill(started_candidates[0])
            selected = (guid, f"started_skill_guid64={guid} level={level}")
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

    for guid in career_candidates:
        level, _max_level = _skill_level_and_max_for_guid(sim_info, guid)
        attempt_ok = try_push_skill_interaction(
            sim, guid, force=force, probe_details=None
        )
        if attempt_ok:
            _append_debug(
                f"Director: skill_result=success skill_guid64={guid} source=career"
            )
            return {
                "success": True,
                "skill_key": guid,
                "skill_reason": f"career_skill_guid64={guid}",
                "skill_source": "career",
                "wants_reason": wants_reason,
                "career_reason": career_reason,
                "started_candidates": started_candidates,
            }
        _log_try_skill(
            "career",
            guid,
            level,
            "push_failed",
            details="capability_push_failed",
        )

    failure_counts = {"no_object": 0, "no_affordance": 0, "push_failed": 0}
    attempted = 0
    for skill_obj in started_candidates:
        attempted += 1
        guid = _skill_guid64(skill_obj)
        level = _skill_level_from_skill(skill_obj)
        attempt_ok = try_push_skill_interaction(
            sim, skill_obj, force=force, probe_details=None
        )
        if attempt_ok:
            _append_debug(
                f"Director: skill_result=success skill_guid64={guid} source=started"
            )
            return {
                "success": True,
                "skill_key": guid,
                "skill_reason": f"started_skill_guid64={guid} level={level}",
                "skill_source": "started",
                "wants_reason": wants_reason,
                "career_reason": career_reason,
                "started_candidates": started_candidates,
            }
        failure_counts["push_failed"] += 1

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


def get_last_skill_plan_strict():
    if isinstance(_LAST_SKILL_PLAN_STRICT, dict):
        return dict(_LAST_SKILL_PLAN_STRICT)
    return _LAST_SKILL_PLAN_STRICT


def _schedule_skill_plan_verification(
    sim_info,
    chosen_skill_guid,
    baseline_level,
    baseline_value,
    interaction_aff_guid64,
    interaction_aff_name,
):
    if sim_info is None:
        return False, "sim_info_missing"
    sim_id = getattr(sim_info, "sim_id", None)
    if sim_id is None:
        return False, "sim_id_missing"
    alarms = __import__("alarms")
    clock = __import__("clock")
    story_log = __import__("simulation_mode.story_log", fromlist=["append_event"])

    old_handle = _PENDING_SKILL_PLAN_VERIFY_ALARMS.pop(sim_id, None)
    if old_handle is not None:
        try:
            old_handle.cancel()
        except Exception:
            pass

    def _verify_cb(handle, _sim_id=sim_id):
        _PENDING_SKILL_PLAN_VERIFY_ALARMS.pop(_sim_id, None)
        sim_info_inner = _resolve_sim_info_by_id(_sim_id)
        after_level = None
        after_value = None
        if sim_info_inner is not None:
            after_level, after_value = _skill_progress_snapshot(
                sim_info_inner, chosen_skill_guid
            )
        increased = "unknown"
        if baseline_level is not None and after_level is not None:
            try:
                increased = bool(after_level > baseline_level)
            except Exception:
                increased = "unknown"
        elif baseline_value is not None and after_value is not None:
            try:
                increased = bool(after_value > baseline_value)
            except Exception:
                increased = "unknown"
        story_log.append_event(
            "skill_plan_verify",
            sim_info=sim_info_inner,
            sim_id=_sim_id,
            chosen_skill_guid=chosen_skill_guid,
            baseline_level=baseline_level,
            baseline_value=baseline_value,
            after_level=after_level,
            after_value=after_value,
            increased=increased,
            interaction_aff_guid64=interaction_aff_guid64,
            interaction_aff_name=interaction_aff_name,
        )

    try:
        timespan = clock.interval_in_sim_minutes(_SKILL_PLAN_VERIFY_SIM_MINUTES)
    except Exception:
        timespan = clock.interval_in_real_seconds(_SKILL_PLAN_VERIFY_REAL_SECONDS)
    try:
        handle = alarms.add_alarm_real_time(sim_info, timespan, _verify_cb)
    except Exception as exc:
        return False, f"alarm_failed:{exc}"
    _PENDING_SKILL_PLAN_VERIFY_ALARMS[sim_id] = handle
    return True, "ok"


def try_push_skill_plan_strict(sim_info, caps: dict):
    global _LAST_SKILL_PLAN_STRICT
    details = {
        "chosen_skill_guid": None,
        "chosen_skill_label": None,
        "candidate_skill_count": 0,
        "candidate_affordance_count": 0,
        "attempted_pushes": [],
        "reason": "unknown",
    }
    sim = None
    if sim_info is not None:
        try:
            sim = sim_info.get_sim_instance()
        except Exception:
            sim = None
    if sim is None:
        details["reason"] = "sim_instance_missing"
        _LAST_SKILL_PLAN_STRICT = details
        return False, details

    by_skill = caps.get("by_skill_gain_guid") if isinstance(caps, dict) else {}
    sim_id = _sim_identifier(sim_info)
    now = time.time()
    if _recent_skill_plans:
        for key, ts in list(_recent_skill_plans.items()):
            if now - ts > (_SKILL_PLAN_COOLDOWN_SECONDS * 4):
                _recent_skill_plans.pop(key, None)

    def _cooldown_key(guid, entry):
        return (
            sim_id,
            guid,
            entry.get("obj_def_id"),
            entry.get("aff_guid64"),
        )

    def _on_cooldown(guid, entry):
        key = _cooldown_key(guid, entry)
        ts = _recent_skill_plans.get(key)
        if ts is None:
            return False
        return (now - ts) < _SKILL_PLAN_COOLDOWN_SECONDS

    def _cand_count_for_skill(guid):
        candidates = []
        if isinstance(by_skill, dict):
            candidates = list(by_skill.get(str(guid)) or by_skill.get(guid) or [])
        count = 0
        for entry in candidates:
            if entry.get("safe_push", True) is False:
                continue
            if entry.get("allow_autonomous", True) is False:
                continue
            if _on_cooldown(guid, entry):
                continue
            count += 1
        return count

    def _candidate_diagnostics(guid):
        if not isinstance(by_skill, dict):
            return {
                "skill_guid_present_in_caps": False,
                "caps_candidate_total": 0,
                "caps_candidate_after_filters": 0,
                "caps_filtered_breakdown": {
                    "safe_push_false": 0,
                    "allow_autonomous_false": 0,
                    "picker_like": 0,
                },
            }
        key = str(guid)
        present = key in by_skill or guid in by_skill
        raw_candidates = list(by_skill.get(key) or by_skill.get(guid) or [])
        breakdown = {
            "safe_push_false": 0,
            "allow_autonomous_false": 0,
            "picker_like": 0,
        }
        after_filters = 0
        for entry in raw_candidates:
            if entry.get("safe_push", True) is False:
                breakdown["safe_push_false"] += 1
                continue
            if entry.get("allow_autonomous", True) is False:
                breakdown["allow_autonomous_false"] += 1
                continue
            if entry.get("is_picker_like") is True:
                breakdown["picker_like"] += 1
            if _on_cooldown(guid, entry):
                continue
            after_filters += 1
        return {
            "skill_guid_present_in_caps": present,
            "caps_candidate_total": len(raw_candidates),
            "caps_candidate_after_filters": after_filters,
            "caps_filtered_breakdown": breakdown,
        }

    def _observed_in_catalog(guid):
        meta = caps.get("meta") if isinstance(caps, dict) else None
        observed = meta.get("skill_guid_observed_counts") if isinstance(meta, dict) else None
        if not isinstance(observed, dict):
            return 0
        return observed.get(str(guid), 0)

    candidate_skills = []
    had_career_candidates = False
    if settings.director_prefer_career_skills:
        career_candidates, _satisfied = _filter_unmet_career_skills(
            sim_info, _get_career_skill_candidates(sim_info)
        )
        if career_candidates:
            had_career_candidates = True
            for guid in career_candidates:
                candidate_skills.append(
                    {"guid": guid, "label": None, "source": "career"}
                )
    if not had_career_candidates and settings.director_fallback_to_started_skills:
        started_candidates = _get_started_skill_candidates(sim_info)
        if started_candidates:
            for skill_obj in started_candidates:
                guid = _skill_guid64(skill_obj)
                if guid is None:
                    continue
                candidate_skills.append(
                    {
                        "guid": guid,
                        "label": _skill_label(skill_obj),
                        "source": "started",
                    }
                )
    candidate_skill_count = len(candidate_skills)
    if not candidate_skills:
        details["reason"] = "no_skill_candidates"
        details["candidate_skill_count"] = candidate_skill_count
        _LAST_SKILL_PLAN_STRICT = details
        return False, details

    covered = [
        (item["guid"], _cand_count_for_skill(item["guid"]), index)
        for index, item in enumerate(candidate_skills)
    ]
    covered = [(guid, count, index) for guid, count, index in covered if count > 0]
    if not covered:
        by_skill_keys_count = len(by_skill) if isinstance(by_skill, dict) else 0
        top_zero = []
        for item in candidate_skills[:10]:
            guid = item["guid"]
            diag = _candidate_diagnostics(guid)
            diag.update(
                {
                    "skill_guid64": guid,
                    "candidate_count": 0,
                    "observed_in_catalog": _observed_in_catalog(guid),
                }
            )
            top_zero.append(diag)
        primary_guid = candidate_skills[0]["guid"] if candidate_skills else None
        primary_diag = _candidate_diagnostics(primary_guid) if primary_guid is not None else {
            "skill_guid_present_in_caps": False,
            "caps_candidate_total": 0,
            "caps_candidate_after_filters": 0,
            "caps_filtered_breakdown": {
                "safe_push_false": 0,
                "allow_autonomous_false": 0,
                "picker_like": 0,
            },
        }
        details["reason"] = "no_skill_candidates_with_affordances"
        details["candidate_skill_count"] = candidate_skill_count
        details["top_zero_skills"] = top_zero
        details["by_skill_gain_guid_keys_count"] = by_skill_keys_count
        details.update(primary_diag)
        details["observed_in_catalog"] = (
            _observed_in_catalog(primary_guid) if primary_guid is not None else 0
        )
        _LAST_SKILL_PLAN_STRICT = details
        return False, details

    chosen_skill_guid, _chosen_count, _chosen_index = max(
        covered, key=lambda item: (item[1], -item[2])
    )
    chosen_skill_label = None
    for item in candidate_skills:
        if item["guid"] == chosen_skill_guid:
            chosen_skill_label = item["label"]
            break

    details["chosen_skill_guid"] = chosen_skill_guid
    details["chosen_skill_label"] = chosen_skill_label
    details["candidate_skill_count"] = candidate_skill_count

    baseline_level, baseline_value = _skill_progress_snapshot(
        sim_info, chosen_skill_guid
    )

    candidates = capabilities.get_candidates_for_skill_gain_guid(
        chosen_skill_guid, caps
    )
    candidates = [
        entry
        for entry in candidates
        if entry.get("allow_autonomous") is True and entry.get("safe_push") is True
        and not _on_cooldown(chosen_skill_guid, entry)
    ]
    details["candidate_affordance_count"] = len(candidates)
    if not candidates:
        details["reason"] = "no_affordance_candidates_for_skill_guid"
        diag = _candidate_diagnostics(chosen_skill_guid)
        details.update(diag)
        details["observed_in_catalog"] = _observed_in_catalog(chosen_skill_guid)
        _LAST_SKILL_PLAN_STRICT = details
        return False, details

    for entry in candidates:
        def_id = entry.get("obj_def_id")
        aff_guid = entry.get("aff_guid64")
        aff_name = entry.get("aff_name")
        ok = push_by_def_and_aff_guid(
            sim,
            def_id,
            aff_guid,
            reason=f"director_skill_plan_strict_guid64={chosen_skill_guid}",
        )
        details["attempted_pushes"].append(
            {
                "def_id": def_id,
                "aff_guid64": aff_guid,
                "aff_name": aff_name,
                "ok": ok,
            }
        )
        if ok:
            details["reason"] = "ok"
            _recent_skill_plans[_cooldown_key(chosen_skill_guid, entry)] = time.time()
            _schedule_skill_plan_verification(
                sim_info,
                chosen_skill_guid,
                baseline_level,
                baseline_value,
                aff_guid,
                aff_name,
            )
            _LAST_SKILL_PLAN_STRICT = details
            return True, details

    details["reason"] = "all_candidate_pushes_failed"
    _LAST_SKILL_PLAN_STRICT = details
    return False, details


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


def _resolve_sim_info_by_id(sim_id):
    if not sim_id:
        return None
    try:
        mgr = services.sim_info_manager()
    except Exception:
        mgr = None
    if mgr is None:
        return None
    for fn_name in ("get", "get_sim_info_by_id", "get_by_id"):
        fn = getattr(mgr, fn_name, None)
        if callable(fn):
            try:
                sim_info = fn(sim_id)
            except Exception:
                sim_info = None
            if sim_info is not None:
                return sim_info
    return None


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
        # choose_skill_goal returns (skill_guid64, reason)
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
    if isinstance(skill_key, str):
        if probe_details is not None:
            probe_details["resolution_type"] = "FAIL"
            probe_details["failure_reason"] = "string_goal_skill_not_supported_in_kernel"
        _append_debug(f"{sim_name}: FAIL skill goal string not supported skill={skill_key}")
        return False

    skill_guid64 = None
    if isinstance(skill_key, int):
        skill_guid64 = skill_key
    else:
        skill_guid64 = _skill_guid64(skill_key)

    if not skill_guid64:
        if probe_details is not None:
            probe_details["resolution_type"] = "FAIL"
            probe_details["failure_reason"] = "missing_skill_guid64"
        _append_debug(f"{sim_name}: FAIL missing skill guid64")
        return False

    sim_info = getattr(sim, "sim_info", None)
    caps = capabilities.ensure_capabilities(sim_info, force_rebuild=False)
    if not caps:
        if probe_details is not None:
            probe_details["resolution_type"] = "FAIL"
            probe_details["failure_reason"] = "capabilities_missing"
        _append_debug(f"{sim_name}: FAIL capabilities missing for skill_guid64={skill_guid64}")
        return False

    candidates = capabilities.get_candidates_for_skill_gain_guid(skill_guid64, caps)
    candidates = [entry for entry in candidates if entry.get("allow_autonomous") is True]
    if probe_details is not None:
        probe_details["resolution_type"] = "CAPABILITY_INDEX"
        probe_details["skill_guid64"] = skill_guid64
        probe_details["candidate_count"] = len(candidates)
        probe_details["push_attempts"] = []
    if not candidates:
        if probe_details is not None:
            probe_details["failure_reason"] = "no capability candidates for skill guid"
            probe_details["skill_guid64"] = skill_guid64
        _append_debug(
            f"{sim_name}: FAIL no capability candidates for skill guid skill_guid64={skill_guid64}"
        )
        return False

    for entry in candidates:
        def_id = entry.get("obj_def_id")
        aff_guid = entry.get("aff_guid64")
        ok = push_by_def_and_aff_guid(
            sim,
            def_id,
            aff_guid,
            reason=f"director_skill_guid64={skill_guid64}",
            probe_details=probe_details,
        )
        if ok:
            _append_debug(
                f"{sim_name}: SUCCESS push skill_guid64={skill_guid64} def_id={def_id} aff_guid={aff_guid}"
            )
            return True
    if probe_details is not None:
        probe_details["failure_reason"] = "no_capability_candidates_or_all_failed"
    _append_debug(f"{sim_name}: FAIL all candidates failed for skill_guid64={skill_guid64}")
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
        f"({ _skill_guid64(skill_obj)},{_skill_level_from_skill(skill_obj)})"
        for skill_obj in candidates[:20]
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
    ok = try_push_skill_interaction(sim, skill_key, force=force)
    if ok:
        _log_try_skill(
            source,
            skill_key,
            level,
            "success",
            details="capability_push_ok",
            push_ok=True,
        )
        return {"success": True, "result": "success"}
    _log_try_skill(
        source,
        skill_key,
        level,
        "push_failed",
        details="capability_push_failed",
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
    from simulation_mode import story_log
    story_log.append_event(
        "director_push",
        sim_info=sim_info,
        skill_key=skill_key,
        reason=reason,
        object_label=object_label,
        affordance_label=affordance_label,
        action=action,
    )
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
                            if debug_message == "guardian cooldown":
                                cooldown_detail = guardian.get_guardian_cooldown_debug(sim_info, now)
                                _dbg(f"{sim_name}: CARE guardian cooldown {cooldown_detail}")
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
