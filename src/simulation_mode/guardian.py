import time

from interactions.context import InteractionContext, QueueInsertStrategy, InteractionBucketType
import interactions.priority as priority
import services
from server_commands.argument_helpers import get_tunable_instance
import sims4.log
import sims4.resources

from simulation_mode import clock_utils
from simulation_mode.settings import settings

logger = sims4.log.Logger("SimulationModeGuardian")

_MOTIVE_ALIASES = {
    "motive_hunger": ["motive_hunger", "motive_Hunger", "commodity_Hunger"],
    "motive_bladder": ["motive_bladder", "motive_Bladder", "commodity_Bladder"],
    "motive_energy": ["motive_energy", "motive_Energy", "commodity_Energy"],
    "motive_fun": ["motive_fun", "motive_Fun", "commodity_Fun"],
    "motive_social": ["motive_social", "motive_Social", "commodity_Social"],
    "motive_hygiene": ["motive_hygiene", "motive_Hygiene", "commodity_Hygiene"],
}

_MOTIVE_KEYS = list(_MOTIVE_ALIASES.keys())

_OBJECT_KEYWORDS = {
    "motive_hunger": ["fridge", "refriger", "microwave", "stove", "oven", "grill"],
    "motive_bladder": ["toilet", "urinal", "potty"],
    "motive_energy": ["bed", "tent", "coffin", "sleeping"],
    "motive_hygiene": ["shower", "bath", "sink"],
    "motive_fun": ["tv", "stereo", "radio", "computer", "console", "game", "toy"],
    "motive_social": ["phone", "computer"],
}

_AFFORDANCE_KEYWORDS = {
    "motive_hunger": ["quickmeal", "quick_meal", "leftover", "grab", "snack", "eat"],
    "motive_bladder": ["use"],
    "motive_energy": ["sleep", "nap"],
    "motive_hygiene": ["shower", "bath", "wash", "brush"],
    "motive_fun": ["watch", "play", "listen", "dance"],
    "motive_social": ["chat", "talk", "social", "call", "text"],
}

_LAST_GLOBAL_CHECK = 0.0
_LAST_AUTONOMY_LOG = 0.0
_LAST_NO_OBJECT_LOG = 0.0
_LAST_NO_MOTIVE_LOG = 0.0
_PER_SIM_LAST_PUSH = {}
_PER_SIM_PUSH_HISTORY = {}
_MOTIVE_STATS = {}
_LAST_CARE_DETAILS = None

_CARE_KIND_TO_MOTIVE = {
    "eat": "motive_hunger",
    "sleep": "motive_energy",
    "hygiene": "motive_hygiene",
    "fun": "motive_fun",
    "social": "motive_social",
    "bladder": "motive_bladder",
}

_MOTIVE_TO_CARE_KIND = {value: key for key, value in _CARE_KIND_TO_MOTIVE.items()}


def motive_percent(value: float) -> float:
    try:
        percent = (float(value) + 100.0) / 200.0
    except Exception:
        return 0.0
    if percent < 0.0:
        return 0.0
    if percent > 1.0:
        return 1.0
    return percent


def motive_is_green(value: float, green_percent: float) -> bool:
    return motive_percent(value) >= green_percent


def _get_motive_stat(stat_name):
    if stat_name in _MOTIVE_STATS:
        return _MOTIVE_STATS[stat_name]
    try:
        stat = get_tunable_instance(sims4.resources.Types.STATISTIC, stat_name, exact_match=True)
    except Exception as exc:
        logger.warn(f"Failed to load stat {stat_name}: {exc}")
        stat = None
    _MOTIVE_STATS[stat_name] = stat
    return stat


def _get_motive_value(sim_info, stat):
    if stat is None:
        return None
    try:
        stat_obj = sim_info.get_statistic(stat)
        if stat_obj is None:
            try:
                stat_obj = sim_info.get_statistic(stat, add=True)
            except TypeError:
                pass
        if stat_obj is not None and hasattr(stat_obj, "get_value"):
            return stat_obj.get_value()
    except Exception:
        pass
    try:
        commodity_tracker = getattr(sim_info, "commodity_tracker", None)
        if commodity_tracker is not None and hasattr(commodity_tracker, "get_value"):
            try:
                return commodity_tracker.get_value(stat)
            except TypeError:
                return commodity_tracker.get_value(stat, add=True)
    except Exception:
        pass
    try:
        tracker = sim_info.get_tracker(stat)
        if tracker is None:
            return None
        return tracker.get_value(stat)
    except Exception:
        return None


def _sim_identifier(sim_info):
    sim_id = getattr(sim_info, "sim_id", None)
    return sim_id or id(sim_info)


def _is_sim_busy(sim):
    """
    Return True only when the Sim has pending queued interactions waiting to run.
    Do NOT treat queue.running as 'busy' because it's commonly a truthy idle/default interaction.
    """
    queue = getattr(sim, "queue", None)
    if queue is None:
        return False

    # Primary signal: pending queued interactions (not the running SI).
    try:
        queued = getattr(queue, "_queue", None)
        if queued is not None and hasattr(queued, "__len__"):
            return len(queued) > 0
    except Exception:
        pass

    return False


def _object_label(obj):
    parts = [getattr(obj.__class__, "__name__", None)]
    definition = getattr(obj, "definition", None)
    if definition is not None:
        name = getattr(definition, "name", None)
        if name:
            parts.append(name)
    parts.append(str(obj))
    return " ".join(part for part in parts if part).lower()


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


def _find_target_object(sim, motive_key):
    keywords = _OBJECT_KEYWORDS.get(motive_key)
    if not keywords:
        return None
    object_manager = services.object_manager()
    if object_manager is None:
        return None
    best = None
    best_distance = None
    for obj in object_manager.get_objects():
        try:
            label = _object_label(obj)
            if not any(keyword in label for keyword in keywords):
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


def _find_affordance(obj, motive_key):
    keywords = _AFFORDANCE_KEYWORDS.get(motive_key)
    if not keywords:
        return None
    affordances = getattr(obj, "super_affordances", None)
    if affordances is None:
        affordances = getattr(obj, "_super_affordances", None)
    if not affordances:
        return None
    for keyword in keywords:
        for affordance in affordances:
            try:
                name = (
                    getattr(affordance, "__name__", None)
                    or getattr(affordance, "__qualname__", None)
                    or str(affordance)
                )
                if keyword in name.lower():
                    return affordance
            except Exception:
                continue
    return None


def _push_interaction(sim, affordance, target_obj):
    context = InteractionContext(
        sim,
        InteractionContext.SOURCE_SCRIPT,
        priority.Priority.High,
        insert_strategy=QueueInsertStrategy.NEXT,
        bucket=InteractionBucketType.DEFAULT,
    )
    return sim.push_super_affordance(affordance, target_obj, context)


def _log_once_per_hour(message, last_timestamp_attr):
    global _LAST_AUTONOMY_LOG, _LAST_NO_OBJECT_LOG, _LAST_NO_MOTIVE_LOG
    now = time.time()
    last_value = globals().get(last_timestamp_attr, 0.0)
    if now - last_value < 3600:
        return
    globals()[last_timestamp_attr] = now
    logger.warn(message)


def _maybe_run_autonomy(sim):
    autonomy_component = getattr(sim, "autonomy_component", None)
    if autonomy_component is not None:
        run_autonomy = getattr(autonomy_component, "run_autonomy", None)
        if callable(run_autonomy):
            try:
                run_autonomy()
                return True
            except Exception:
                pass
    run_autonomy = getattr(sim, "run_autonomy", None)
    if callable(run_autonomy):
        try:
            run_autonomy()
            return True
        except Exception:
            pass
    return False


def _maybe_apply_better_autonomy_trait(sim_info):
    if not settings.integrate_better_autonomy_trait:
        return
    try:
        trait_manager = services.trait_manager()
        if trait_manager is None:
            return
        trait = trait_manager.get(settings.better_autonomy_trait_id)
        if trait is None:
            return
        tracker = getattr(sim_info, "trait_tracker", None)
        if tracker is None:
            return
        has_trait = getattr(tracker, "has_trait", None)
        add_trait = getattr(tracker, "add_trait", None)
        if callable(has_trait) and callable(add_trait) and not has_trait(trait):
            add_trait(trait)
    except Exception as exc:
        logger.warn(f"Failed to apply Better Autonomy trait: {exc}")


def _motive_snapshot(sim_info):
    snapshot = []
    for key in _MOTIVE_KEYS:
        aliases = _MOTIVE_ALIASES.get(key, [key])
        for alias in aliases:
            stat = _get_motive_stat(alias)
            value = _get_motive_value(sim_info, stat)
            if value is None:
                continue
            snapshot.append((key, float(value)))
            break
    return snapshot


def _select_lowest_motive(snapshot):
    lowest_key = None
    lowest_value = None
    for key, value in snapshot:
        if lowest_value is None or value < lowest_value:
            lowest_key = key
            lowest_value = value
    return lowest_key, lowest_value


def _snapshot_dict(snapshot):
    return {key: value for key, value in snapshot}


def _affordance_label(affordance):
    return (
        getattr(affordance, "__name__", None)
        or getattr(affordance, "__qualname__", None)
        or str(affordance)
    )


def pick_care_goal(sim_info, snapshot: dict, green_percent: float):
    lowest_key = None
    lowest_value = None
    lowest_percent = None
    for key, value in snapshot.items():
        percent = motive_percent(value)
        if lowest_percent is None or percent < lowest_percent:
            lowest_percent = percent
            lowest_key = key
            lowest_value = value
    if lowest_key is None:
        return None, None, None
    care_kind = _MOTIVE_TO_CARE_KIND.get(lowest_key)
    return lowest_key, lowest_value, care_kind


def _attempt_care_push(sim, motive_key):
    target_obj = _find_target_object(sim, motive_key)
    if target_obj is None:
        if _maybe_run_autonomy(sim):
            return False, f"no object for {motive_key}; autonomy refresh attempted"
        return False, f"no object for {motive_key}; autonomy refresh unavailable"
    affordance = _find_affordance(target_obj, motive_key)
    if affordance is None:
        if _maybe_run_autonomy(sim):
            return False, f"no affordance for {motive_key}; autonomy refresh attempted"
        return False, f"no affordance for {motive_key}; autonomy refresh unavailable"
    try:
        result = _push_interaction(sim, affordance, target_obj)
    except Exception as exc:
        return False, f"push failed for {motive_key}: {exc}"
    if result:
        global _LAST_CARE_DETAILS
        _LAST_CARE_DETAILS = (
            motive_key,
            f"{_object_label(target_obj)}:{_affordance_label(affordance)}",
        )
        return True, f"pushed {motive_key} via {_object_label(target_obj)}"
    return False, f"push failed for {motive_key}"


def push_self_care(sim_info, now: float, green_percent: float):
    sim = sim_info.get_sim_instance() if sim_info else None
    if sim is None:
        return False, "no sim instance"
    if getattr(sim_info, "is_npc", False):
        return False, "npc skipped"
    if getattr(sim_info, "is_human", True) is False:
        return False, "non-human skipped"

    snapshot = _motive_snapshot(sim_info)
    if not snapshot:
        return False, "no motive stats available"
    snapshot_dict = _snapshot_dict(snapshot)

    motive_key, motive_value, care_kind = pick_care_goal(sim_info, snapshot_dict, green_percent)
    if motive_key is None or care_kind is None:
        return False, "no care goal found"

    sim_id = _sim_identifier(sim_info)
    if not _can_push_for_sim(sim_id, now):
        return False, "guardian cooldown"

    busy_state = _is_sim_busy(sim)
    if busy_state:
        return False, "sim busy"

    ordered = sorted(snapshot, key=lambda item: motive_percent(item[1]))
    lowest_key = ordered[0][0]
    non_social_keys = [key for key, _ in ordered if key != "motive_social"]
    attempted = []
    attempted_non_social = False
    if lowest_key != "motive_social":
        for key in non_social_keys:
            attempted.append(key)
            attempted_non_social = True
            pushed, message = _attempt_care_push(sim, key)
            if pushed:
                _record_push(sim_id, now)
                return True, message
    else:
        attempted.append(lowest_key)
        pushed, message = _attempt_care_push(sim, lowest_key)
        if pushed:
            _record_push(sim_id, now)
            return True, message

    if "motive_social" in snapshot_dict:
        allow_social = (
            settings.director_allow_social_goals
            or lowest_key == "motive_social"
            or attempted_non_social
            or not non_social_keys
        )
        if allow_social and "motive_social" not in attempted:
            pushed, message = _attempt_care_push(sim, "motive_social")
            if pushed:
                _record_push(sim_id, now)
                return True, message

    return False, "no viable self-care interaction"


def last_care_details():
    return _LAST_CARE_DETAILS


def _can_push_for_sim(sim_id, now):
    cooldown = settings.guardian_per_sim_cooldown_seconds
    last_push = _PER_SIM_LAST_PUSH.get(sim_id)
    if last_push is not None and now - last_push < cooldown:
        return False

    history = _PER_SIM_PUSH_HISTORY.setdefault(sim_id, [])
    history[:] = [ts for ts in history if now - ts < 3600]
    max_pushes = settings.guardian_max_pushes_per_sim_per_hour
    if max_pushes > 0 and len(history) >= max_pushes:
        return False
    return True


def _record_push(sim_id, now):
    _PER_SIM_LAST_PUSH[sim_id] = now
    history = _PER_SIM_PUSH_HISTORY.setdefault(sim_id, [])
    history.append(now)


def _process_sim(sim_info, now):
    sim = sim_info.get_sim_instance()
    if sim is None:
        return
    if getattr(sim_info, "is_npc", False):
        return
    if getattr(sim_info, "is_human", True) is False:
        return

    _maybe_apply_better_autonomy_trait(sim_info)

    snapshot = _motive_snapshot(sim_info)
    if not snapshot:
        _log_once_per_hour("No motive stats available to evaluate.", "_LAST_NO_MOTIVE_LOG")
        return

    motive_key, motive_value = _select_lowest_motive(snapshot)
    if motive_key is None or motive_value is None:
        return
    if motive_value >= settings.guardian_min_motive:
        return

    busy_state = _is_sim_busy(sim)
    if busy_state:
        return

    sim_id = _sim_identifier(sim_info)
    if not _can_push_for_sim(sim_id, now):
        return

    target_obj = _find_target_object(sim, motive_key)
    if target_obj is None:
        if _maybe_run_autonomy(sim):
            _log_once_per_hour("No guardian object found; autonomy refresh attempted.", "_LAST_AUTONOMY_LOG")
        else:
            _log_once_per_hour("No guardian object found; autonomy refresh unavailable.", "_LAST_NO_OBJECT_LOG")
        return

    affordance = _find_affordance(target_obj, motive_key)
    if affordance is None:
        if _maybe_run_autonomy(sim):
            _log_once_per_hour("No guardian affordance found; autonomy refresh attempted.", "_LAST_AUTONOMY_LOG")
        else:
            _log_once_per_hour("No guardian affordance found; autonomy refresh unavailable.", "_LAST_NO_OBJECT_LOG")
        return

    try:
        result = _push_interaction(sim, affordance, target_obj)
        if result:
            _record_push(sim_id, now)
    except Exception as exc:
        logger.warn(f"Failed to push guardian interaction: {exc}")


def run_guardian():
    global _LAST_GLOBAL_CHECK
    now = time.time()
    if now - _LAST_GLOBAL_CHECK < settings.guardian_check_seconds:
        return
    _LAST_GLOBAL_CHECK = now

    try:
        if clock_utils.is_paused():
            return
    except Exception as exc:
        logger.warn(f"Pause detection failed: {exc}")
        return

    household = services.active_household()
    if household is None:
        return

    try:
        sim_infos = list(household)
    except Exception:
        sim_infos = []
        try:
            sim_infos = list(household.sim_infos)
        except Exception:
            return

    for sim_info in sim_infos:
        try:
            _process_sim(sim_info, now)
        except Exception as exc:
            logger.warn(f"Guardian failed for sim: {exc}")
