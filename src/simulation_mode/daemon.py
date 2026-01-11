import importlib
import time

from simulation_mode.settings import settings

_ALARM_HANDLE = None
_LAST_DEATH_REASSERT = 0
_DEATH_REASSERT_SECONDS = 60
last_error = None
last_alarm_variant = None
last_tick_wallclock = 0.0
tick_count = 0


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
    global last_tick_wallclock, tick_count
    tick_count += 1
    last_tick_wallclock = time.time()
    if not settings.enabled:
        return
    _maybe_auto_unpause()
    _maybe_reassert_death()


def _try_schedule_alarm(alarms, owner, seconds, callback):
    global last_alarm_variant
    attempts = [
        ("seconds_owner_callback_sleep",
         lambda: alarms.add_alarm_real_time(
             owner,
             float(seconds),
             callback,
             repeating=True,
             use_sleep_time=True,
         )),
        ("seconds_owner_callback",
         lambda: alarms.add_alarm_real_time(
             owner,
             float(seconds),
             callback,
             repeating=True,
         )),
        ("seconds_owner_callback_reordered_sleep",
         lambda: alarms.add_alarm_real_time(
             owner,
             callback,
             float(seconds),
             repeating=True,
             use_sleep_time=True,
         )),
        ("seconds_owner_callback_reordered",
         lambda: alarms.add_alarm_real_time(
             owner,
             callback,
             float(seconds),
             repeating=True,
         )),
    ]
    try:
        date_and_time = importlib.import_module("date_and_time")
        clock = importlib.import_module("clock")
        time_span = date_and_time.TimeSpan(clock.interval_in_real_seconds(float(seconds)))
        attempts.extend([
            ("timespan_owner_callback_sleep",
             lambda: alarms.add_alarm_real_time(
                 owner,
                 time_span,
                 callback,
                 repeating=True,
                 use_sleep_time=True,
             )),
            ("timespan_owner_callback",
             lambda: alarms.add_alarm_real_time(
                 owner,
                 time_span,
                 callback,
                 repeating=True,
             )),
            ("timespan_owner_callback_reordered_sleep",
             lambda: alarms.add_alarm_real_time(
                 owner,
                 callback,
                 time_span,
                 repeating=True,
                 use_sleep_time=True,
             )),
            ("timespan_owner_callback_reordered",
             lambda: alarms.add_alarm_real_time(
                 owner,
                 callback,
                 time_span,
                 repeating=True,
             )),
        ])
    except Exception:
        pass

    last_exc = None
    for name, attempt in attempts:
        try:
            handle = attempt()
            last_alarm_variant = name
            return handle
        except TypeError as exc:
            last_exc = exc
            continue
    if last_exc is None:
        raise RuntimeError("No alarm variants attempted")
    raise last_exc


def start():
    global _ALARM_HANDLE, last_alarm_variant
    stop()
    _set_last_error(None)
    last_alarm_variant = None
    try:
        alarms = importlib.import_module("alarms")
        services = importlib.import_module("services")
        interval = max(1, int(settings.tick_seconds))
        _ALARM_HANDLE = _try_schedule_alarm(
            alarms,
            services.time_service(),
            interval,
            _on_tick,
        )
        if _ALARM_HANDLE is None:
            _set_last_error("alarm failed to start")
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
