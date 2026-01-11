import sims4.commands
from sims4.commands import BOOL_TRUE, CommandType

from simulation_mode import daemon
from simulation_mode.settings import settings
from simulation_mode.patches import pregnancy_block  # noqa: F401

_FALSE_STRINGS = {"false", "f", "0", "off", "no", "n"}
_TICK_MIN_SECONDS = 2
_TICK_MAX_SECONDS = 120


def _parse_bool(arg: str):
    if arg is None:
        return None
    s = arg.strip().lower()
    if s in BOOL_TRUE:
        return True
    if s in _FALSE_STRINGS:
        return False
    return None


def _status_line():
    return (
        "Simulation Mode = {enabled} | protect_motives={protect} | "
        "allow_pregnancy={pregnancy} | auto_unpause={unpause} | "
        "tick={tick}s"
    ).format(
        enabled=settings.enabled,
        protect=settings.protect_motives,
        pregnancy=settings.allow_pregnancy,
        unpause=settings.auto_unpause,
        tick=settings.tick_seconds,
    )


def _set_enabled(enabled: bool):
    settings.enabled = enabled
    if enabled:
        daemon.start()
    else:
        daemon.stop()


def _set_tick_seconds(value: int):
    clamped = max(_TICK_MIN_SECONDS, min(_TICK_MAX_SECONDS, value))
    settings.tick_seconds = clamped
    if settings.enabled:
        daemon.start()
    return clamped


@sims4.commands.Command("simulation", command_type=CommandType.Live)
def simulation_cmd(action: str = None, value: str = None, _connection=None):
    output = sims4.commands.CheatOutput(_connection)

    parsed = _parse_bool(action)
    if parsed is not None:
        _set_enabled(parsed)
        output(_status_line())
        return True

    if action is None or action.strip().lower() == "status":
        output(_status_line())
        return True

    action_key = action.strip().lower()
    if action_key == "allow_pregnancy":
        parsed = _parse_bool(value)
        if parsed is not None:
            settings.allow_pregnancy = parsed
        output(_status_line())
        return True

    if action_key == "protect_motives":
        parsed = _parse_bool(value)
        if parsed is not None:
            settings.protect_motives = parsed
        output(_status_line())
        return True

    if action_key == "auto_unpause":
        parsed = _parse_bool(value)
        if parsed is not None:
            settings.auto_unpause = parsed
        output(_status_line())
        return True

    if action_key == "tick":
        try:
            tick_value = int(value)
        except Exception:
            tick_value = None
        if tick_value is not None:
            _set_tick_seconds(tick_value)
        output(_status_line())
        return True

    output(_status_line())
    return True


@sims4.commands.Command("simulation_mode", command_type=CommandType.Live)
def simulation_mode_cmd(action: str = None, value: str = None, _connection=None):
    return simulation_cmd(action, value, _connection)
