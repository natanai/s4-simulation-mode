import importlib
import time

from simulation_mode.settings import settings

_ALARM_HANDLE = None
_LAST_DEATH_REASSERT = 0
_DEATH_REASSERT_SECONDS = 60
last_error = None


def _set_last_error(error):
    global last_error
    last_error = error


def _maybe_auto_unpause():
    if not settings.auto_unpause:
        return

    # Only attempt to unpause in an actively running zone.
    try:
        services = importlib.import_module("services")
        zone = services.current_zone()
        if zone is None or not getattr(zone, "is_zone_running", True):
            return
    except Exception:
        pass

    try:
        clock_service = services.game_clock_service()
        if clock_service is None:
            return

        # Enum names vary between builds.
        clock_module = importlib.import_module("clock")
        _ClockSpeed = getattr(clock_module, "ClockSpeedMode", None)
        if _ClockSpeed is None:
            _ClockSpeed = getattr(clock_module, "ClockSpeed", None)
        if _ClockSpeed is None:
            return

        _GameSpeedChangeSource = getattr(clock_module, "GameSpeedChangeSource", None)
        if _GameSpeedChangeSource is not None:
            _change_source = getattr(_GameSpeedChangeSource, "USER", _GameSpeedChangeSource.GAMEPLAY)
        else:
            _change_source = None

        speed_attr = getattr(clock_service, "clock_speed", None)
        current_speed = speed_attr() if callable(speed_attr) else speed_attr

        if current_speed == _ClockSpeed.PAUSED:
            if _GameSpeedChangeSource is not None:
                clock_service.set_clock_speed(_ClockSpeed.NORMAL, change_source=_change_source)
            else:
                clock_service.set_clock_speed(_ClockSpeed.NORMAL)

    except Exception:
        return


def _maybe_reassert_death():
    global _LAST_DEATH_REASSERT
    if settings.allow_death:
        return
    now = time.time()
    if now - _LAST_DEATH_REASSERT < _DEATH_REASSERT_SECONDS:
        return
    _LAST_DEATH_REASSERT = now
    try:
        sims4_commands = importlib.import_module("sims4.commands")
        sims4_commands.execute("death.toggle false", None)
    except Exception as exc:
        _set_last_error(str(exc))


def _on_tick(_alarm_handle=None):
    if not settings.enabled:
        return
    _maybe_auto_unpause()
    _maybe_reassert_death()


def start():
    global _ALARM_HANDLE
    stop()
    _set_last_error(None)
    try:
        alarms = importlib.import_module("alarms")
        services = importlib.import_module("services")
        interval = max(1, int(settings.tick_seconds))
        _ALARM_HANDLE = alarms.add_alarm_real_time(
            services.time_service(),
            interval,
            _on_tick,
            repeating=True,
        )
    except Exception as exc:
        _ALARM_HANDLE = None
        _set_last_error(str(exc))


def stop():
    global _ALARM_HANDLE
    if _ALARM_HANDLE is None:
        return
    try:
        alarms = importlib.import_module("alarms")
        alarms.cancel_alarm(_ALARM_HANDLE)
    except Exception:
        pass
    _ALARM_HANDLE = None


def is_running():
    return _ALARM_HANDLE is not None
