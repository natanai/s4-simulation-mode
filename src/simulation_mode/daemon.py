import alarms
import services
from sims4.resources import Types, get_tunable_instance

from simulation_mode.settings import settings

_MOTIVE_NAMES = (
    "motive_hunger",
    "motive_energy",
    "motive_bladder",
    "motive_hygiene",
    "motive_fun",
    "motive_social",
)

_ALARM_HANDLE = None
_MOTIVE_STATS = {}


def _get_motive_stat(name):
    if name in _MOTIVE_STATS:
        return _MOTIVE_STATS[name]
    stat = None
    try:
        stat = get_tunable_instance(Types.STATISTIC, name, exact_match=True)
    except Exception:
        stat = None
    _MOTIVE_STATS[name] = stat
    return stat


def _maybe_auto_unpause():
    if not settings.auto_unpause:
        return
    try:
        clock_service = services.game_clock_service()
        if clock_service is None:
            return
        from clock import ClockSpeed

        if clock_service.clock_speed == ClockSpeed.PAUSED:
            clock_service.set_clock_speed(ClockSpeed.NORMAL)
    except Exception:
        return


def _protect_household_motives():
    if not settings.protect_motives:
        return
    household = None
    try:
        household = services.active_household()
    except Exception:
        return
    if household is None:
        return

    for sim_info in household.sim_info_gen():
        try:
            if not getattr(sim_info, "is_human", False):
                continue
            if getattr(sim_info, "is_npc", False):
                continue
            tracker = getattr(sim_info, "commodity_tracker", None)
            if tracker is None:
                continue
            for motive_name in _MOTIVE_NAMES:
                stat = _get_motive_stat(motive_name)
                if stat is None:
                    continue
                motive_tracker = sim_info.get_tracker(stat)
                if motive_tracker is None:
                    continue
                current_value = motive_tracker.get_value(stat)
                if current_value is None:
                    continue
                if current_value < settings.motive_floor:
                    tracker.set_value(stat, settings.motive_bump_to)
        except Exception:
            continue


def _on_tick(_alarm_handle=None):
    if not settings.enabled:
        return
    _maybe_auto_unpause()
    _protect_household_motives()


def start():
    global _ALARM_HANDLE
    stop()
    try:
        interval = max(1, int(settings.tick_seconds))
    except Exception:
        interval = 10
    try:
        _ALARM_HANDLE = alarms.add_alarm_real_time(
            services.time_service(),
            interval,
            _on_tick,
            repeating=True,
        )
    except Exception:
        _ALARM_HANDLE = None


def stop():
    global _ALARM_HANDLE
    if _ALARM_HANDLE is None:
        return
    try:
        alarms.cancel_alarm(_ALARM_HANDLE)
    except Exception:
        pass
    _ALARM_HANDLE = None
