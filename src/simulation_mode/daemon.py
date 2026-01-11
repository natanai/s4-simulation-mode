from simulation_mode.settings import settings

_ALARM_HANDLE = None
last_error = None


def _set_last_error(error):
    global last_error
    last_error = error


def _maybe_auto_unpause():
    if not settings.auto_unpause:
        return
    try:
        import services
        from clock import ClockSpeed

        clock_service = services.game_clock_service()
        if clock_service is None:
            return
        if clock_service.clock_speed == ClockSpeed.PAUSED:
            clock_service.set_clock_speed(ClockSpeed.NORMAL)
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
