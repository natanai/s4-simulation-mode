from simulation_mode.settings import settings

_ALARM_HANDLE = None
last_error = None


def _set_last_error(error):
    global last_error
    last_error = error


def _maybe_auto_unpause():
    if not settings.auto_unpause:
        return

    # Only attempt to unpause in an actively running zone.
    try:
        import services

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
        try:
            from clock import ClockSpeedMode as _ClockSpeed
        except Exception:
            from clock import ClockSpeed as _ClockSpeed

        # Prefer USER source so it behaves like pressing Play.
        try:
            from clock import GameSpeedChangeSource as _GameSpeedChangeSource

            _change_source = getattr(_GameSpeedChangeSource, "USER", _GameSpeedChangeSource.GAMEPLAY)
        except Exception:
            _GameSpeedChangeSource = None
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


def _on_tick(_alarm_handle=None):
    if not settings.enabled:
        return
    _maybe_auto_unpause()


def start():
    global _ALARM_HANDLE
    stop()
    _set_last_error(None)
    try:
        import alarms
        import services

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
        import alarms

        alarms.cancel_alarm(_ALARM_HANDLE)
    except Exception:
        pass
    _ALARM_HANDLE = None


def is_running():
    return _ALARM_HANDLE is not None
