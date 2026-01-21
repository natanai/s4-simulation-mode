import time

import services
from server_commands.argument_helpers import get_tunable_instance
import sims4.log
import sims4.resources

from simulation_mode import capabilities
from simulation_mode import sim_scope
from simulation_mode import clock_utils
from simulation_mode.push_utils import push_by_def_and_aff_guid
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

_RUNNING_CARE_KEYWORDS = {
    "motive_energy": ["sleep", "nap", "bed_sleep", "bed_nap"],
    "motive_hunger": [
        "consume_food",
        "eat",
        "grab_a_serving",
        "cook",
        "microwave",
        "get_leftovers",
        "have_meal",
    ],
    "motive_bladder": ["toilet", "use_toilet", "pee", "bladder"],
    "motive_hygiene": ["shower", "bath", "wash_hands", "brush_teeth", "hygiene"],
    "motive_fun": ["watch", "tv", "game", "play", "fun"],
    "motive_social": ["social", "chat", "talk", "hug", "friendly", "kiss", "compliment"],
}

_LAST_GLOBAL_CHECK = 0.0
_LAST_AUTONOMY_LOG = 0.0
_LAST_NO_OBJECT_LOG = 0.0
_LAST_NO_MOTIVE_LOG = 0.0
_PER_SIM_LAST_PUSH = {}
_PER_SIM_PUSH_HISTORY = {}
_PER_SIM_LAST_CHOSEN_MOTIVE = {}
_MOTIVE_STATS = {}
_LAST_CARE_DETAILS = None
_CARE_LOCKS = {}
_last_critical_cancel_ts = {}
_NONCRITICAL_INTERRUPT_STATE = {}
_last_noncritical_cancel_ts = {}
_last_noncritical_force_push_ts_by_sim = {}
_LAST_NONCRITICAL_CANCEL_AFF_GUID_BY_SIM = {}
_NONCRITICAL_REPEAT_CANCEL_COUNT_BY_SIM = {}

_CARE_KIND_TO_MOTIVE = {
    "eat": "motive_hunger",
    "sleep": "motive_energy",
    "hygiene": "motive_hygiene",
    "fun": "motive_fun",
    "social": "motive_social",
    "bladder": "motive_bladder",
}

_MOTIVE_TO_CARE_KIND = {value: key for key, value in _CARE_KIND_TO_MOTIVE.items()}

_CARE_LOCK_DURATIONS = {
    "motive_hunger": 180,
    "motive_energy": 120,
    "motive_bladder": 90,
    "motive_hygiene": 90,
}


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


def _motive_guid64_from_key(motive_key):
    aliases = _MOTIVE_ALIASES.get(motive_key, [motive_key])
    for alias in aliases:
        stat = _get_motive_stat(alias)
        guid = getattr(stat, "guid64", None)
        if guid is not None:
            return guid
    return None


def _sim_identifier(sim_info):
    sim_id = getattr(sim_info, "sim_id", None)
    return sim_id or id(sim_info)


def _care_lock_duration(motive_key):
    return _CARE_LOCK_DURATIONS.get(motive_key, 60)


def _get_care_lock(sim_id, motive_key):
    return _CARE_LOCKS.get(sim_id, {}).get(motive_key)


def _set_care_lock(sim_id, motive_key, now, reason):
    lock = {"until_ts": now + _care_lock_duration(motive_key), "reason": reason}
    _CARE_LOCKS.setdefault(sim_id, {})[motive_key] = lock
    return lock


def _care_lock_blocks(sim_id, motive_key, now):
    lock = _get_care_lock(sim_id, motive_key)
    if not lock:
        return False, 0
    until_ts = lock.get("until_ts")
    if until_ts is None:
        return False, 0
    remaining = until_ts - now
    if remaining <= 0:
        _CARE_LOCKS.get(sim_id, {}).pop(motive_key, None)
        return False, 0
    return True, remaining


def _has_running_non_idle(sim):
    queue = getattr(sim, "queue", None)
    if queue is None:
        return False, None
    running = getattr(queue, "running", None)
    if running is None:
        return False, None
    if isinstance(running, (list, tuple)):
        running = running[0] if running else None
    if running is None:
        return False, None
    return not _interaction_is_idle(running), running


def _safe_story_event(event_type, **kwargs):
    from simulation_mode import story_log

    try:
        story_log.append_event(event_type, **kwargs)
        return True
    except Exception:
        return False


def _interaction_addresses_motive(aff_name: str, motive_key: str) -> bool:
    if not aff_name or not motive_key:
        return False
    try:
        lowered = aff_name.lower()
    except Exception:
        return False
    if motive_key == "motive_hunger":
        return any(token in lowered for token in ("eat", "cook", "meal", "grab", "quickmeal"))
    if motive_key == "motive_energy":
        return any(token in lowered for token in ("sleep", "nap"))
    if motive_key == "motive_bladder":
        return "toilet" in lowered
    if motive_key == "motive_hygiene":
        return any(token in lowered for token in ("shower", "bath"))
    return False


def _running_affordance_matches_motive_candidates(
    sim_info, running_aff_guid64, motive_key
) -> bool:
    if not running_aff_guid64:
        return False
    motive_guid = _motive_guid64_from_key(motive_key)
    if not motive_guid:
        return False
    caps = capabilities.ensure_capabilities(sim_info, force_rebuild=False)
    if not caps:
        return False
    candidates = capabilities.get_candidates_for_ad_guid(motive_guid, caps) or []
    return any(entry.get("aff_guid64") == running_aff_guid64 for entry in candidates)


def _cancel_sim_interactions_safe(sim):
    if sim is None:
        return False, "sim_missing"
    queue = getattr(sim, "queue", None)
    cancel = getattr(queue, "cancel_all", None) if queue is not None else None
    if callable(cancel):
        try:
            cancel()
            return True, "queue.cancel_all"
        except Exception as exc:
            return False, f"queue.cancel_all_error:{type(exc).__name__}"
    cancel_all = getattr(sim, "cancel_all_interactions", None)
    if callable(cancel_all):
        try:
            cancel_all()
            return True, "sim.cancel_all_interactions"
        except Exception as exc:
            return False, f"sim.cancel_all_interactions_error:{type(exc).__name__}"
    return False, "cancel_unavailable"


def _can_cancel_for_sim(sim_id, now):
    cooldown = settings.guardian_critical_cancel_cooldown_seconds
    if cooldown <= 0:
        return True
    last_cancel = _last_critical_cancel_ts.get(sim_id)
    return last_cancel is None or now - last_cancel >= cooldown


def get_noncritical_interrupt_strikes(sim_id):
    state = _NONCRITICAL_INTERRUPT_STATE.get(sim_id)
    if not state:
        return 0
    return int(state.get("count", 0))


def get_last_noncritical_cancel_timestamp(sim_id):
    return _last_noncritical_cancel_ts.get(sim_id)


def get_last_noncritical_force_push_timestamp(sim_id):
    return _last_noncritical_force_push_ts_by_sim.get(sim_id)


def _update_noncritical_interrupt_strikes(sim_id, now, motive_key):
    state = _NONCRITICAL_INTERRUPT_STATE.setdefault(
        sim_id,
        {"count": 0, "last_ts": 0.0, "motive_key": None},
    )
    if state.get("motive_key") != motive_key:
        state["count"] = 0
    last_ts = state.get("last_ts") or 0.0
    if last_ts and now - last_ts > settings.guardian_check_seconds * 2:
        state["count"] = 0
    state["count"] = int(state.get("count", 0)) + 1
    state["last_ts"] = now
    state["motive_key"] = motive_key
    return state["count"]


def _maybe_interrupt_running_noncritical(
    sim_info,
    sim,
    sim_id,
    now,
    motive_key,
    motive_value,
    running_aff_name,
    running_aff_guid64,
    snapshot_dict,
    bypass_cooldown: bool = False,
    unsafe_threshold_override=None,
):
    if not settings.guardian_interrupt_running_noncritical:
        return False, "disabled"
    if motive_value is None:
        return False, "no_motive_value"
    running_type = None
    resolved_type, resolved_aff_name, _running_label, resolved_guid = _running_interaction_info(sim)
    running_type = resolved_type
    if not running_aff_name:
        running_aff_name = resolved_aff_name
    if not running_aff_guid64:
        running_aff_guid64 = resolved_guid
    unsafe_threshold = (
        unsafe_threshold_override
        if unsafe_threshold_override is not None
        else settings.guardian_min_motive
    )
    if snapshot_dict and (running_aff_guid64 or running_aff_name):
        for other_key, other_val in snapshot_dict.items():
            if other_key == motive_key:
                continue
            if other_val is None:
                continue
            if other_val < unsafe_threshold:
                if _running_affordance_matches_motive_candidates(
                    sim_info, running_aff_guid64, other_key
                ):
                    _safe_story_event(
                        "guardian_noncritical_interrupt_blocked",
                        sim_info=sim_info,
                        sim_id=sim_id,
                        motive_key=motive_key,
                        motive_value=motive_value,
                        running_aff_name=running_aff_name,
                        running_aff_guid64=running_aff_guid64,
                        blocked_by_key=other_key,
                        blocked_by_value=other_val,
                    )
                    return False, "blocked_by_running_care"
                if running_aff_name and _interaction_addresses_motive(
                    running_aff_name, other_key
                ):
                    _safe_story_event(
                        "guardian_noncritical_interrupt_blocked",
                        sim_info=sim_info,
                        sim_id=sim_id,
                        motive_key=motive_key,
                        motive_value=motive_value,
                        running_aff_name=running_aff_name,
                        running_aff_guid64=running_aff_guid64,
                        blocked_by_key=other_key,
                        blocked_by_value=other_val,
                    )
                    return False, "blocked_by_running_care"
    effective_interrupt_threshold = max(
        settings.guardian_interrupt_noncritical_motive_threshold, unsafe_threshold
    )
    if motive_value > effective_interrupt_threshold:
        return (
            False,
            f"above_threshold(value={motive_value} thr={effective_interrupt_threshold})",
        )
    if not _can_push_for_sim(sim_id, now):
        return False, "max_pushes"
    motive_unsafe = motive_value < unsafe_threshold
    if not _cooldown_allows_push(
        sim, sim_id, now, motive_key, motive_unsafe, bypass_cooldown=bypass_cooldown
    ):
        return False, "cooldown"
    cancel_cd = settings.guardian_noncritical_cancel_cooldown_seconds
    last_cancel = _last_noncritical_cancel_ts.get(sim_id)
    if cancel_cd > 0 and last_cancel is not None and now - last_cancel < cancel_cd:
        running_type = None
        resolved_type, resolved_aff_name, _running_label, resolved_guid = _running_interaction_info(sim)
        running_type = resolved_type
        if not running_aff_name:
            running_aff_name = resolved_aff_name
        if not running_aff_guid64:
            running_aff_guid64 = resolved_guid
        last_aff = _LAST_NONCRITICAL_CANCEL_AFF_GUID_BY_SIM.get(sim_id)
        repeat_count = _NONCRITICAL_REPEAT_CANCEL_COUNT_BY_SIM.get(sim_id, 0)
        if (
            last_aff is not None
            and running_aff_guid64 == last_aff
            and motive_value <= unsafe_threshold
            and repeat_count < 2
        ):
            cancel_ok, cancel_method = _cancel_sim_interactions_safe(sim)
            if cancel_ok:
                _NONCRITICAL_REPEAT_CANCEL_COUNT_BY_SIM[sim_id] = repeat_count + 1
                _safe_story_event(
                    "guardian_noncritical_repeat_cancel",
                    sim_info=sim_info,
                    motive_key=motive_key,
                    motive_value=motive_value,
                    running_aff_name=running_aff_name,
                    running_aff_guid64=running_aff_guid64,
                    running_type=running_type,
                    cancel_ok=cancel_ok,
                    cancel_method=cancel_method,
                    repeat_count=repeat_count + 1,
                )
                return True, "repeat_cancel"
        if settings.guardian_noncritical_force_push_during_cancel_cooldown:
            last_force = _last_noncritical_force_push_ts_by_sim.get(sim_id)
            force_cd = settings.guardian_noncritical_force_push_cooldown_seconds
            if (
                force_cd <= 0
                or last_force is None
                or (now - last_force) >= force_cd
            ):
                _last_noncritical_force_push_ts_by_sim[sim_id] = now
                return False, "cancel_cooldown_force_push"
        return False, "cancel_cooldown"
    strikes = _update_noncritical_interrupt_strikes(sim_id, now, motive_key)
    strikes_needed = settings.guardian_interrupt_noncritical_strikes
    if strikes < strikes_needed:
        _safe_story_event(
            "guardian_noncritical_interrupt_waiting",
            sim_info=sim_info,
            motive_key=motive_key,
            motive_value=motive_value,
            running_aff_name=running_aff_name,
            running_aff_guid64=running_aff_guid64,
            running_type=running_type,
            strikes=strikes,
            strikes_needed=strikes_needed,
            threshold=effective_interrupt_threshold,
            unsafe_threshold=unsafe_threshold,
        )
        return False, "waiting"
    cancel_ok, cancel_method = _cancel_sim_interactions_safe(sim)
    _last_noncritical_cancel_ts[sim_id] = now
    _safe_story_event(
        "guardian_noncritical_cancel",
        sim_info=sim_info,
        motive_key=motive_key,
        motive_value=motive_value,
        running_aff_name=running_aff_name,
        running_aff_guid64=running_aff_guid64,
        running_type=running_type,
        cancel_ok=cancel_ok,
        cancel_method=cancel_method,
        strikes=strikes,
    )
    if cancel_ok:
        _LAST_NONCRITICAL_CANCEL_AFF_GUID_BY_SIM[sim_id] = running_aff_guid64
        _NONCRITICAL_REPEAT_CANCEL_COUNT_BY_SIM[sim_id] = 0
    state = _NONCRITICAL_INTERRUPT_STATE.setdefault(
        sim_id,
        {"count": 0, "last_ts": 0.0, "motive_key": None},
    )
    state["count"] = 0
    state["last_ts"] = now
    state["motive_key"] = motive_key
    return True, "canceled"


def _is_sim_busy(sim):
    """
    Return True when the Sim has a running non-idle interaction or queued interactions waiting to run.
    Do NOT treat queue.running as 'busy' if it is idle/default.
    """
    queue = getattr(sim, "queue", None)
    if queue is None:
        return False, "queue_missing"

    running = getattr(queue, "running", None)
    if running is not None:
        if isinstance(running, (list, tuple)):
            running = running[0] if running else None
        if running is not None and not _interaction_is_idle(running):
            return True, "running_non_idle"

    # Primary signal: pending queued interactions (not the running SI).
    try:
        queued = getattr(queue, "_queue", None)
        if queued is not None and hasattr(queued, "__len__"):
            if len(queued) > 0:
                return True, "queued_interactions"
    except Exception:
        pass

    return False, "idle_or_empty_queue"


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


def _running_interaction_info(sim):
    queue = getattr(sim, "queue", None)
    if queue is None:
        return None, None, None, None
    running = getattr(queue, "running", None)
    if running is None:
        return None, None, None, None
    running_type = None
    try:
        running_type = str(running)
    except Exception:
        running_type = None
    if not running_type:
        running_type = getattr(running.__class__, "__name__", None)
    affordance = getattr(running, "affordance", None)
    if affordance is None:
        affordance = getattr(running, "super_affordance", None)
    affordance_guid64 = None
    if affordance is not None and hasattr(affordance, "guid64"):
        try:
            affordance_guid64 = int(affordance.guid64)
        except Exception:
            affordance_guid64 = None
    affordance_name = None
    if affordance is not None:
        affordance_name = getattr(affordance, "__name__", None)
        if not affordance_name:
            try:
                affordance_name = str(affordance)
            except Exception:
                affordance_name = None
    running_label = affordance_name or running_type
    return running_type, affordance_name, running_label, affordance_guid64


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


def _is_running_care_for_motive(sim, motive_key: str) -> bool:
    queue = getattr(sim, "queue", None)
    if queue is None:
        return False
    running = getattr(queue, "running", None)
    if running is None:
        return False
    running_type, affordance_name, _running_label, _aff_guid64 = _running_interaction_info(sim)
    running_type = (running_type or "").lower()
    affordance_name = (affordance_name or "").lower()
    keywords = _RUNNING_CARE_KEYWORDS.get(motive_key, [])
    return any(
        keyword in running_type or keyword in affordance_name for keyword in keywords
    )


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


def _attempt_care_push(sim, motive_key, motive_value=None, force=False):
    motive_guid = _motive_guid64_from_key(motive_key)
    if not motive_guid:
        if _maybe_run_autonomy(sim):
            return False, f"motive={motive_key} guid=none; autonomy refresh attempted", None
        return False, f"motive={motive_key} guid=none; autonomy refresh unavailable", None
    sim_info = getattr(sim, "sim_info", None)
    caps = capabilities.ensure_capabilities(sim_info, force_rebuild=False)
    if not caps:
        if _maybe_run_autonomy(sim):
            return False, f"motive={motive_key} caps=missing; autonomy refresh attempted", None
        return False, f"motive={motive_key} caps=missing; autonomy refresh unavailable", None
    candidates = capabilities.get_candidates_for_ad_guid(motive_guid, caps)
    candidates = [
        entry
        for entry in candidates
        if entry.get("allow_autonomous") is True and entry.get("safe_push") is True
    ]
    if not candidates:
        if _maybe_run_autonomy(sim):
            return (
                False,
                f"motive={motive_key} no candidates; autonomy refresh attempted",
                None,
            )
        return (
            False,
            f"motive={motive_key} no candidates; autonomy refresh unavailable",
            None,
        )

    def _format_push_failure_summary(attempts):
        if not attempts:
            return "attempts_count=0"
        last_attempt = attempts[-1]
        summary = {"attempts_count": len(attempts)}
        failure_reason = last_attempt.get("failure_reason")
        if failure_reason:
            summary["last_failure_reason"] = failure_reason
        object_label = last_attempt.get("object_label")
        if object_label:
            summary["last_object_label"] = object_label
        affordance_label = last_attempt.get("affordance_label")
        if affordance_label:
            summary["last_affordance_label"] = affordance_label
        if "precheck_requested" in last_attempt:
            summary["precheck_requested"] = last_attempt.get("precheck_requested")
        return " ".join(f"{key}={value}" for key, value in summary.items())

    if motive_key == "motive_hunger" and motive_value is not None:
        hunger_percent = motive_percent(motive_value)
        if hunger_percent <= settings.guardian_hunger_prefer_quick_meal_threshold:
            prefer_keywords = [
                "grabserving",
                "grab a serving",
                "getleftovers",
                "quickmeal",
                "eat",
            ]
            deprioritize_keywords = ["cook", "createtray", "prep", "baking"]

            def _score(entry):
                name = (entry.get("aff_name") or "").lower()
                score = 0
                if any(token in name for token in prefer_keywords):
                    score += 10
                if any(token in name for token in deprioritize_keywords):
                    score -= 5
                return score

            candidates = sorted(candidates, key=_score, reverse=True)

    last_failure_summary = None
    for entry in candidates:
        def_id = entry.get("obj_def_id")
        aff_guid = entry.get("aff_guid64")
        probe_details = {}
        ok, _push_reason = push_by_def_and_aff_guid(
            sim,
            def_id,
            aff_guid,
            reason=f"guardian_motive_guid64={motive_guid}",
            probe_details=probe_details,
            precheck=settings.guardian_precheck_affordance_tests,
            force=force,
        )
        if ok:
            global _LAST_CARE_DETAILS
            _LAST_CARE_DETAILS = (
                motive_key,
                f"obj_def_id={def_id} aff_guid64={aff_guid}",
            )
            last_success = probe_details.get("last_success")
            return (
                True,
                f"motive={motive_key} obj_def_id={def_id} aff_guid64={aff_guid}",
                last_success,
            )
        last_failure_summary = _format_push_failure_summary(
            probe_details.get("push_attempts", [])
        )
    if _maybe_run_autonomy(sim):
        message = f"motive={motive_key} push_failed; autonomy refresh attempted"
        if last_failure_summary:
            message = f"{message} push_failed_summary={last_failure_summary}"
        return False, message, None
    message = f"motive={motive_key} push_failed; autonomy refresh unavailable"
    if last_failure_summary:
        message = f"{message} push_failed_summary={last_failure_summary}"
    return False, message, None


def push_self_care(
    sim_info,
    now: float,
    green_percent: float,
    bypass_cooldown: bool = False,
    unsafe_threshold_override=None,
):
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
    _PER_SIM_LAST_CHOSEN_MOTIVE[sim_id] = motive_key
    unsafe_threshold = (
        unsafe_threshold_override
        if unsafe_threshold_override is not None
        else settings.guardian_min_motive
    )
    if not bypass_cooldown and motive_value is not None and motive_value >= unsafe_threshold:
        return False, "motive safe"
    motive_unsafe = motive_value is not None and motive_value < unsafe_threshold
    if motive_unsafe and _is_running_care_for_motive(sim, motive_key):
        running_type, running_aff_name, _running_label, _running_aff_guid64 = (
            _running_interaction_info(sim)
        )
        from simulation_mode import story_log
        sim_name = getattr(sim, "full_name", None)
        if callable(sim_name):
            try:
                sim_name = sim_name()
            except Exception:
                sim_name = None
        sim_name = sim_name or getattr(sim, "first_name", None)
        story_log.append_event(
            "guardian_skip_running_care",
            sim_info=sim_info,
            motive_key=motive_key,
            running_aff_name=running_aff_name,
            running_type=running_type,
            sim_name=sim_name,
        )
        return False, "already_running_care"
    critical = motive_value is not None and motive_value <= settings.guardian_red_motive
    running_non_idle, running_interaction = _has_running_non_idle(sim)
    (
        running_type,
        running_aff_name,
        _running_label,
        running_aff_guid64,
    ) = _running_interaction_info(sim)
    interrupted_noncritical = False
    if running_non_idle and not critical:
        did_interrupt, decision_reason = _maybe_interrupt_running_noncritical(
            sim_info,
            sim,
            sim_id,
            now,
            motive_key,
            motive_value,
            running_aff_name,
            running_aff_guid64,
            snapshot_dict,
            bypass_cooldown=bypass_cooldown,
            unsafe_threshold_override=unsafe_threshold,
        )
        if did_interrupt:
            interrupted_noncritical = True
        elif decision_reason == "cancel_cooldown_force_push":
            interrupted_noncritical = True
            _safe_story_event(
                "guardian_noncritical_force_push_window",
                sim_info=sim_info,
                sim_id=sim_id,
                motive_key=motive_key,
                motive_value=motive_value,
                running_aff_name=running_aff_name,
                running_aff_guid64=running_aff_guid64,
                last_noncritical_cancel_ts=_last_noncritical_cancel_ts.get(sim_id),
                last_noncritical_force_push_ts=_last_noncritical_force_push_ts_by_sim.get(
                    sim_id
                ),
            )
        else:
            sim_name = getattr(sim, "full_name", None)
            if callable(sim_name):
                try:
                    sim_name = sim_name()
                except Exception:
                    sim_name = None
            sim_name = sim_name or getattr(sim, "first_name", None)
            _safe_story_event(
                "guardian_skip_running_noncritical",
                sim_info=sim_info,
                motive_key=motive_key,
                running_aff_name=running_aff_name,
                running_aff_guid64=running_aff_guid64,
                running_type=running_type,
                sim_name=sim_name,
                motive_value=motive_value,
                threshold=settings.guardian_interrupt_noncritical_motive_threshold,
                threshold_used=max(
                    settings.guardian_interrupt_noncritical_motive_threshold,
                    unsafe_threshold,
                ),
                unsafe_threshold=unsafe_threshold,
                decision_reason=decision_reason,
                strikes=get_noncritical_interrupt_strikes(sim_id),
            )
            return False, "running_noncritical"
    if interrupted_noncritical and settings.guardian_force_push_on_noncritical_interrupt:
        bypass_cooldown_local = True
    else:
        bypass_cooldown_local = bypass_cooldown
    if not _cooldown_allows_push(
        sim,
        sim_id,
        now,
        motive_key,
        motive_unsafe,
        bypass_cooldown=bypass_cooldown_local,
    ):
        return False, "guardian cooldown"
    if not _can_push_for_sim(sim_id, now):
        return False, "guardian max pushes"

    busy_state, _busy_reason = _is_sim_busy(sim)
    if critical and busy_state and running_non_idle:
        red_threshold = settings.guardian_red_motive
        critical_motive_keys = [
            key
            for key, value in snapshot_dict.items()
            if value is not None and value <= red_threshold
        ]
        if running_aff_name and any(
            _interaction_addresses_motive(running_aff_name, key)
            for key in critical_motive_keys
        ):
            return False, "critical_running_addresses_motive"
        if not _can_cancel_for_sim(sim_id, now):
            return False, "critical_cancel_cooldown"
        cancel_ok, cancel_method = _cancel_sim_interactions_safe(sim)
        _last_critical_cancel_ts[sim_id] = now
        _safe_story_event(
            "guardian_critical_cancel",
            sim_info=sim_info,
            sim_id=sim_id,
            running_aff_name=running_aff_name,
            running_aff_guid64=running_aff_guid64,
            cancel_ok=cancel_ok,
            cancel_method=cancel_method,
            critical_motive_keys=critical_motive_keys,
        )
    if busy_state and not (critical or interrupted_noncritical):
        return False, "sim busy"

    ordered = sorted(snapshot, key=lambda item: motive_percent(item[1]))
    lowest_key = ordered[0][0]
    non_social_keys = [key for key, _ in ordered if key != "motive_social"]
    attempted = []
    attempted_non_social = False
    last_failure_message = None
    if lowest_key != "motive_social":
        for key in non_social_keys:
            attempted.append(key)
            attempted_non_social = True
            value = snapshot_dict.get(key)
            critical = value is not None and value <= settings.guardian_red_motive
            lock_active, remaining = _care_lock_blocks(sim_id, key, now)
            if lock_active and not critical:
                _safe_story_event(
                    "guardian_care_lock_skip",
                    sim_info=sim_info,
                    motive_key=key,
                    seconds_remaining=round(remaining, 2),
                )
                continue
            base_force = value is not None and value <= settings.guardian_min_motive
            force = (
                True
                if interrupted_noncritical
                and settings.guardian_force_push_on_noncritical_interrupt
                else base_force
            )
            pushed, message, push_details = _attempt_care_push(
                sim, key, motive_value=value, force=force
            )
            if pushed:
                _record_push(sim_id, now)
                _set_care_lock(sim_id, key, now, "pushed_care")
                from simulation_mode import story_log
                story_log.append_event(
                    "guardian_push",
                    sim_info=sim_info,
                    message=message,
                    motive_key=key,
                    force=force,
                    push_details=push_details,
                )
                return True, message
            _safe_story_event(
                "guardian_push_failed",
                sim_info=sim_info,
                motive_key=key,
                message=message,
                force=force,
            )
            last_failure_message = message
    else:
        attempted.append(lowest_key)
        value = snapshot_dict.get(lowest_key)
        critical = value is not None and value <= settings.guardian_red_motive
        lock_active, remaining = _care_lock_blocks(sim_id, lowest_key, now)
        if lock_active and not critical:
            _safe_story_event(
                "guardian_care_lock_skip",
                sim_info=sim_info,
                motive_key=lowest_key,
                seconds_remaining=round(remaining, 2),
            )
            return False, "guardian care lock"
        base_force = value is not None and value <= settings.guardian_min_motive
        force = (
            True
            if interrupted_noncritical
            and settings.guardian_force_push_on_noncritical_interrupt
            else base_force
        )
        pushed, message, push_details = _attempt_care_push(
            sim, lowest_key, motive_value=value, force=force
        )
        if pushed:
            _record_push(sim_id, now)
            _set_care_lock(sim_id, lowest_key, now, "pushed_care")
            from simulation_mode import story_log
            story_log.append_event(
                "guardian_push",
                sim_info=sim_info,
                message=message,
                motive_key=lowest_key,
                force=force,
                push_details=push_details,
            )
            return True, message
        _safe_story_event(
            "guardian_push_failed",
            sim_info=sim_info,
            motive_key=lowest_key,
            message=message,
            force=force,
        )
        last_failure_message = message

    if "motive_social" in snapshot_dict:
        allow_social = (
            settings.director_allow_social_goals
            or lowest_key == "motive_social"
            or attempted_non_social
            or not non_social_keys
        )
        if allow_social and "motive_social" not in attempted:
            value = snapshot_dict.get("motive_social")
            critical = value is not None and value <= settings.guardian_red_motive
            lock_active, remaining = _care_lock_blocks(sim_id, "motive_social", now)
            if lock_active and not critical:
                _safe_story_event(
                    "guardian_care_lock_skip",
                    sim_info=sim_info,
                    motive_key="motive_social",
                    seconds_remaining=round(remaining, 2),
                )
                return False, "guardian care lock"
            base_force = value is not None and value <= settings.guardian_min_motive
            force = (
                True
                if interrupted_noncritical
                and settings.guardian_force_push_on_noncritical_interrupt
                else base_force
            )
            pushed, message, push_details = _attempt_care_push(
                sim, "motive_social", motive_value=value, force=force
            )
            if pushed:
                _record_push(sim_id, now)
                _set_care_lock(sim_id, "motive_social", now, "pushed_care")
                from simulation_mode import story_log
                story_log.append_event(
                    "guardian_push",
                    sim_info=sim_info,
                    message=message,
                    motive_key="motive_social",
                    force=force,
                    push_details=push_details,
                )
                return True, message
            _safe_story_event(
                "guardian_push_failed",
                sim_info=sim_info,
                motive_key="motive_social",
                message=message,
                force=force,
            )
            last_failure_message = message

    if last_failure_message:
        logger.warn(f"Guardian push failed: {last_failure_message}")
    else:
        logger.warn(
            f"Guardian push failed: no viable self-care interaction (sim_id={sim_id})"
        )
    return False, "no viable self-care interaction"


def last_care_details():
    return _LAST_CARE_DETAILS


def _cooldown_allows_push(sim, sim_id, now, motive_key, motive_unsafe, bypass_cooldown: bool):
    cooldown = settings.guardian_per_sim_cooldown_seconds
    last_push = _PER_SIM_LAST_PUSH.get(sim_id)
    if bypass_cooldown:
        return True
    if last_push is None or cooldown <= 0:
        return True
    secs_since_last = now - last_push
    if secs_since_last >= cooldown:
        return True

    _running_type, _affordance_name, running_label, _aff_guid64 = _running_interaction_info(sim)
    running_label = running_label or "none"
    care_relevant = False
    if motive_unsafe:
        care_relevant = _is_running_care_for_motive(sim, motive_key)
        if not care_relevant:
            logger.warn(
                "CARE guardian cooldown bypassed motive={} secs_since_last={} running={} "
                "care_relevant={}".format(motive_key, secs_since_last, running_label, care_relevant)
            )
            return True

    logger.warn(
        "CARE guardian cooldown motive={} secs_since_last={} running={} care_relevant={}".format(
            motive_key, secs_since_last, running_label, care_relevant
        )
    )
    return False


def _can_push_for_sim(sim_id, now):
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
    # legacy wrapper used by daemon
    push_self_care(
        sim_info,
        now=now,
        green_percent=settings.director_green_motive_percent,
        bypass_cooldown=False,
        unsafe_threshold_override=None,
    )


def get_last_push_timestamp(sim_id):
    return _PER_SIM_LAST_PUSH.get(sim_id)


def get_last_chosen_motive(sim_id):
    return _PER_SIM_LAST_CHOSEN_MOTIVE.get(sim_id)


def get_guardian_cooldown_debug(sim_info, now):
    if sim_info is None:
        return "motive=None secs_since_last=None running=none care_relevant=False"
    sim = sim_info.get_sim_instance()
    sim_id = _sim_identifier(sim_info)
    last_push = _PER_SIM_LAST_PUSH.get(sim_id)
    secs_since_last = None if last_push is None else now - last_push
    motive_key = _PER_SIM_LAST_CHOSEN_MOTIVE.get(sim_id)
    _running_type, _affordance_name, running_label, _aff_guid64 = _running_interaction_info(sim)
    running_label = running_label or "none"
    care_relevant = False
    if motive_key is not None:
        care_relevant = _is_running_care_for_motive(sim, motive_key)
    return (
        "motive={} secs_since_last={} running={} care_relevant={}".format(
            motive_key, secs_since_last, running_label, care_relevant
        )
    )


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

    sim_infos = list(sim_scope.iter_playable_household_sim_infos() or [])
    if not sim_infos:
        return

    for sim_info in sim_infos:
        try:
            _process_sim(sim_info, now)
        except Exception as exc:
            logger.warn(f"Guardian failed for sim: {exc}")
