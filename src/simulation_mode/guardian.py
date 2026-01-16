import time

import services
from server_commands.argument_helpers import get_tunable_instance
import sims4.log
import sims4.resources

from simulation_mode import capabilities
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
        return None, None, None
    running = getattr(queue, "running", None)
    if running is None:
        return None, None, None
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
    affordance_name = None
    if affordance is not None:
        affordance_name = getattr(affordance, "__name__", None)
        if not affordance_name:
            try:
                affordance_name = str(affordance)
            except Exception:
                affordance_name = None
    running_label = affordance_name or running_type
    return running_type, affordance_name, running_label


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
    running_type, affordance_name, _running_label = _running_interaction_info(sim)
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


def _attempt_care_push(sim, motive_key, force=False):
    motive_guid = _motive_guid64_from_key(motive_key)
    if not motive_guid:
        if _maybe_run_autonomy(sim):
            return False, f"motive={motive_key} guid=none; autonomy refresh attempted"
        return False, f"motive={motive_key} guid=none; autonomy refresh unavailable"
    sim_info = getattr(sim, "sim_info", None)
    caps = capabilities.ensure_capabilities(sim_info, force_rebuild=False)
    if not caps:
        if _maybe_run_autonomy(sim):
            return False, f"motive={motive_key} caps=missing; autonomy refresh attempted"
        return False, f"motive={motive_key} caps=missing; autonomy refresh unavailable"
    candidates = capabilities.get_candidates_for_ad_guid(motive_guid, caps)
    candidates = [
        entry
        for entry in candidates
        if entry.get("allow_autonomous") is True and entry.get("safe_push") is True
    ]
    if not candidates:
        if _maybe_run_autonomy(sim):
            return False, f"motive={motive_key} no candidates; autonomy refresh attempted"
        return False, f"motive={motive_key} no candidates; autonomy refresh unavailable"

    for entry in candidates:
        def_id = entry.get("obj_def_id")
        aff_guid = entry.get("aff_guid64")
        ok = push_by_def_and_aff_guid(
            sim,
            def_id,
            aff_guid,
            reason=f"guardian_motive_guid64={motive_guid}",
            probe_details=None,
        )
        if ok:
            global _LAST_CARE_DETAILS
            _LAST_CARE_DETAILS = (
                motive_key,
                f"obj_def_id={def_id} aff_guid64={aff_guid}",
            )
            return (
                True,
                f"motive={motive_key} obj_def_id={def_id} aff_guid64={aff_guid}",
            )
    if _maybe_run_autonomy(sim):
        return False, f"motive={motive_key} push_failed; autonomy refresh attempted"
    return False, f"motive={motive_key} push_failed; autonomy refresh unavailable"


def push_self_care(sim_info, now: float, green_percent: float, bypass_cooldown: bool = False):
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
    motive_unsafe = motive_value is not None and motive_value < settings.guardian_min_motive
    if motive_unsafe and _is_running_care_for_motive(sim, motive_key):
        running_type, running_aff_name, _running_label = _running_interaction_info(sim)
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
    if not _cooldown_allows_push(
        sim, sim_id, now, motive_key, motive_unsafe, bypass_cooldown=bypass_cooldown
    ):
        return False, "guardian cooldown"
    if not _can_push_for_sim(sim_id, now):
        return False, "guardian max pushes"

    busy_state, _busy_reason = _is_sim_busy(sim)
    if busy_state:
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
            force = value is not None and value <= settings.guardian_red_motive
            pushed, message = _attempt_care_push(sim, key, force=force)
            if pushed:
                _record_push(sim_id, now)
                from simulation_mode import story_log
                story_log.append_event(
                    "guardian_push",
                    sim_info=sim_info,
                    message=message,
                    motive_key=key,
                    force=force,
                )
                return True, message
            last_failure_message = message
    else:
        attempted.append(lowest_key)
        value = snapshot_dict.get(lowest_key)
        force = value is not None and value <= settings.guardian_red_motive
        pushed, message = _attempt_care_push(sim, lowest_key, force=force)
        if pushed:
            _record_push(sim_id, now)
            from simulation_mode import story_log
            story_log.append_event(
                "guardian_push",
                sim_info=sim_info,
                message=message,
                motive_key=lowest_key,
                force=force,
            )
            return True, message
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
            force = value is not None and value <= settings.guardian_red_motive
            pushed, message = _attempt_care_push(sim, "motive_social", force=force)
            if pushed:
                _record_push(sim_id, now)
                from simulation_mode import story_log
                story_log.append_event(
                    "guardian_push",
                    sim_info=sim_info,
                    message=message,
                    motive_key="motive_social",
                    force=force,
                )
                return True, message
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

    _running_type, _affordance_name, running_label = _running_interaction_info(sim)
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

    busy_state, _busy_reason = _is_sim_busy(sim)
    if busy_state:
        return

    sim_id = _sim_identifier(sim_info)
    _PER_SIM_LAST_CHOSEN_MOTIVE[sim_id] = motive_key
    if not _cooldown_allows_push(sim, sim_id, now, motive_key, True, bypass_cooldown=False):
        return
    if not _can_push_for_sim(sim_id, now):
        return

    force = motive_value <= settings.guardian_red_motive
    pushed, message = _attempt_care_push(sim, motive_key, force=force)
    if pushed:
        _record_push(sim_id, now)
    else:
        if "obj=none" in message:
            if "autonomy refresh attempted" in message:
                _log_once_per_hour(
                    "No guardian object found; autonomy refresh attempted.",
                    "_LAST_AUTONOMY_LOG",
                )
            else:
                _log_once_per_hour(
                    "No guardian object found; autonomy refresh unavailable.",
                    "_LAST_NO_OBJECT_LOG",
                )
        elif "no affordance candidates" in message or "no keywords" in message:
            if "autonomy refresh attempted" in message:
                _log_once_per_hour(
                    "No guardian affordance found; autonomy refresh attempted.",
                    "_LAST_AUTONOMY_LOG",
                )
            else:
                _log_once_per_hour(
                    "No guardian affordance found; autonomy refresh unavailable.",
                    "_LAST_NO_OBJECT_LOG",
                )
        else:
            logger.warn(f"Guardian push failed: {message}")


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
    _running_type, _affordance_name, running_label = _running_interaction_info(sim)
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
