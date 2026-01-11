import clock
import services


def _clock_speed_enum():
    _ClockSpeed = getattr(clock, "ClockSpeedMode", None)
    if _ClockSpeed is None:
        _ClockSpeed = getattr(clock, "ClockSpeed", None)
    return _ClockSpeed


def is_paused(clock_service=None):
    if clock_service is None:
        clock_service = services.game_clock_service()
    if clock_service is None:
        return False
    _ClockSpeed = _clock_speed_enum()
    if _ClockSpeed is None:
        return False
    speed_attr = getattr(clock_service, "clock_speed", None)
    current_speed = speed_attr() if callable(speed_attr) else speed_attr
    return current_speed == _ClockSpeed.PAUSED
