import sims4.commands
from sims4.commands import BOOL_TRUE, CommandType

from simulation_mode.settings import settings

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


def _start_daemon():
    try:
        from simulation_mode import daemon

        daemon.start()
        return True, None
    except Exception as exc:
        return False, str(exc)


def _stop_daemon():
    try:
        from simulation_mode import daemon

        daemon.stop()
        return True, None
    except Exception as exc:
        return False, str(exc)


def _daemon_status():
    try:
        from simulation_mode import daemon

        return daemon.is_running(), daemon.last_error
    except Exception as exc:
        return False, str(exc)


def _apply_pregnancy_patch():
    try:
        from simulation_mode.patches import pregnancy_block

        pregnancy_block.apply_patch()
    except Exception:
        return False
    return True


def _set_enabled(enabled: bool):
    settings.enabled = enabled
    if enabled:
        _apply_pregnancy_patch()
        return _start_daemon()
    return _stop_daemon()


def _set_tick_seconds(value: int):
    clamped = max(_TICK_MIN_SECONDS, min(_TICK_MAX_SECONDS, value))
    settings.tick_seconds = clamped
    if settings.enabled:
        _start_daemon()
    return clamped


def _format_debug(enabled: bool, running: bool, last_error: str):
    output = [
        f"enabled={enabled}",
        f"daemon_running={running}",
    ]
    if last_error:
        output.append(f"daemon_error={last_error}")
    return " | ".join(output)


@sims4.commands.Command("simulation", command_type=CommandType.Live)
def simulation_cmd(action: str = None, value: str = None, _connection=None):
    output = sims4.commands.CheatOutput(_connection)

    parsed = _parse_bool(action)
    if parsed is not None:
        success, error = _set_enabled(parsed)
        output(_status_line())
        if parsed:
            if success:
                output("Simulation daemon started successfully.")
            else:
                output(f"Simulation daemon failed to start: {error}")
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

    if action_key == "debug":
        running, last_error = _daemon_status()
        output(_format_debug(settings.enabled, running, last_error))
        return True

    output(_status_line())
    return True


@sims4.commands.Command("simulation_mode", command_type=CommandType.Live)
def simulation_mode_cmd(action: str = None, value: str = None, _connection=None):
    return simulation_cmd(action, value, _connection)
