import importlib
import time
import traceback

from simulation_mode.settings import settings


class _DaemonOwnerSingleton:
    pass


_ALARM_OWNER = _DaemonOwnerSingleton()
_ALARM_HANDLE = None
_LAST_DEATH_REASSERT = 0
_DEATH_REASSERT_SECONDS = 60
daemon_error = None
last_error = None
last_alarm_variant = None
last_tick_wallclock = 0.0
tick_count = 0
_daemon_connection = None
last_unpause_attempt_ts = None
last_unpause_result = None
last_pause_requests_count = None


def _set_last_error(error):
    global last_error, daemon_error
    last_error = error
    daemon_error = error


def set_connection(conn):
    global _daemon_connection
    _daemon_connection = conn


def _pause_requests_count(clock_service):
    if clock_service is None:
        return None
    for attr in ("pause_requests", "_pause_requests", "pause_requests_count", "_pause_requests_count"):
        value = getattr(clock_service, attr, None)
        if value is None:
            continue
        try:
            value = value() if callable(value) else value
            if isinstance(value, int):
                return value
            if hasattr(value, "__len__"):
                return len(value)
        except Exception:
            continue
    return None


def _is_paused(clock_service, clock_module):
    _ClockSpeed = getattr(clock_module, "ClockSpeedMode", None)
    if _ClockSpeed is None:
        _ClockSpeed = getattr(clock_module, "ClockSpeed", None)
    if _ClockSpeed is None:
        return False
    speed_attr = getattr(clock_service, "clock_speed", None)
    current_speed = speed_attr() if callable(speed_attr) else speed_attr
    return current_speed == _ClockSpeed.PAUSED


def _try_unpause():
    global last_unpause_attempt_ts, last_unpause_result, last_pause_requests_count
    services = importlib.import_module("services")
    clock_module = importlib.import_module("clock")
    sims4_commands = importlib.import_module("sims4.commands")

    try:
        zone = services.current_zone()
        if zone is None or not getattr(zone, "is_zone_running", True):
            return

        clock_service = services.game_clock_service()
        if clock_service is None:
            return

        if not _is_paused(clock_service, clock_module):
            return

        last_unpause_attempt_ts = time.time()
        last_pause_requests_count = _pause_requests_count(clock_service)
        last_unpause_result = "failed"

        _ClockSpeed = getattr(clock_module, "ClockSpeedMode", None)
        if _ClockSpeed is None:
            _ClockSpeed = getattr(clock_module, "ClockSpeed", None)
        if _ClockSpeed is None:
            return

        _GameSpeedChangeSource = getattr(clock_module, "GameSpeedChangeSource", None)
        _change_source = _GameSpeedChangeSource.GAMEPLAY if _GameSpeedChangeSource is not None else None

        if _GameSpeedChangeSource is not None:
            clock_service.set_clock_speed(_ClockSpeed.NORMAL, change_source=_change_source)
        else:
            clock_service.set_clock_speed(_ClockSpeed.NORMAL)

        if not _is_paused(clock_service, clock_module):
            last_unpause_result = "clock_service_ok"
            return

        if _daemon_connection is not None:
            sims4_commands.client_cheat("|clock.toggle_pause_unpause", _daemon_connection)
            if not _is_paused(clock_service, clock_module):
                last_unpause_result = "toggle_used"
                return

            sims4_commands.client_cheat("|clock.setspeed one", _daemon_connection)
            if not _is_paused(clock_service, clock_module):
                last_unpause_result = "setspeed_used"
                return

        last_unpause_result = "failed"
    except Exception as exc:
        last_unpause_result = f"failed: {exc}"
        _set_last_error(str(exc))


def _maybe_auto_unpause():
    if not settings.auto_unpause:
        return

    # Only attempt to unpause in an actively running zone.
    try:
        _try_unpause()
    except Exception as exc:
        _set_last_error(str(exc))


def _maybe_reassert_death():
    global _LAST_DEATH_REASSERT
    if settings.allow_death:
        return
    now = time.time()
    if now - _LAST_DEATH_REASSERT < _DEATH_REASSERT_SECONDS:
        return
    _LAST_DEATH_REASSERT = now
    sims4_commands = importlib.import_module("sims4.commands")
    try:
        sims4_commands.execute("death.toggle false", None)
    except Exception as exc:
        _set_last_error(str(exc))


def _on_tick(_alarm_handle=None):
    global last_tick_wallclock, tick_count
    tick_count += 1
    last_tick_wallclock = time.time()
    if not settings.enabled:
        return
    _maybe_auto_unpause()
    _maybe_reassert_death()


def start():
    global _ALARM_HANDLE, last_alarm_variant
    stop()
    _set_last_error(None)
    last_alarm_variant = None
    alarms = importlib.import_module("alarms")
    clock = importlib.import_module("clock")
    time_span = clock.interval_in_real_seconds(float(settings.tick_seconds))
    try:
        _ALARM_HANDLE = alarms.add_alarm_real_time(
            _ALARM_OWNER,
            time_span,
            _on_tick,
            repeating=True,
            use_sleep_time=True,
        )
        last_alarm_variant = "timespan_owner_callback_sleep"
        if _ALARM_HANDLE is None:
            raise RuntimeError("alarm failed to start")
    except Exception:
        _ALARM_HANDLE = None
        _set_last_error(traceback.format_exc())


def stop():
    global _ALARM_HANDLE
    if _ALARM_HANDLE is None:
        return
    alarms = importlib.import_module("alarms")
    try:
        alarms.cancel_alarm(_ALARM_HANDLE)
    except Exception:
        pass
    _ALARM_HANDLE = None


def is_running():
    return _ALARM_HANDLE is not None
