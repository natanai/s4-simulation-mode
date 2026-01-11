import importlib

import sims4.commands
from sims4.commands import BOOL_TRUE, CommandType

from simulation_mode.settings import get_config_path, save_settings, settings

_FALSE_STRINGS = {"false", "f", "0", "off", "no", "n"}
_TICK_MIN_SECONDS = 2
_TICK_MAX_SECONDS = 120
_last_patch_error = None


def _parse_bool(arg: str):
    if arg is None:
        return None
    s = arg.strip().lower()
    if s in BOOL_TRUE:
        return True
    if s in _FALSE_STRINGS:
        return False
    return None


def _set_last_patch_error(error):
    global _last_patch_error
    _last_patch_error = error


def _status_lines():
    return [
        f"enabled={settings.enabled}",
        f"auto_unpause={settings.auto_unpause}",
        f"auto_dialogs={settings.auto_dialogs}",
        f"allow_death={settings.allow_death}",
        f"allow_pregnancy={settings.allow_pregnancy}",
        f"tick={settings.tick_seconds}",
        f"config_path={get_config_path()}",
    ]


def _emit_status(output):
    for line in _status_lines():
        output(line)


def _start_daemon():
    try:
        daemon = importlib.import_module("simulation_mode.daemon")
        daemon.start()
        return True, None
    except Exception as exc:
        return False, str(exc)


def _stop_daemon():
    try:
        daemon = importlib.import_module("simulation_mode.daemon")
        daemon.stop()
        return True, None
    except Exception as exc:
        return False, str(exc)


def _daemon_status():
    try:
        daemon = importlib.import_module("simulation_mode.daemon")
        return daemon.is_running(), daemon.last_error
    except Exception as exc:
        return False, str(exc)


def _apply_pregnancy_patch():
    if not settings.enabled or settings.allow_pregnancy:
        _set_last_patch_error(None)
        return True
    try:
        pregnancy_block = importlib.import_module("simulation_mode.patches.pregnancy_block")
        patched = pregnancy_block.apply_patch()
        if patched:
            _set_last_patch_error(None)
            return True
    except Exception as exc:
        _set_last_patch_error(str(exc))
        return False
    _set_last_patch_error("pregnancy patch unavailable")
    return False


def _apply_auto_dialogs(_connection, output):
    if not settings.auto_dialogs:
        return True
    try:
        sims4.commands.client_cheat("|ui.dialog.auto_respond", _connection)
        return True
    except Exception as exc:
        output(f"auto_dialogs failed: {exc}")
        return False


def _apply_death_toggle(_connection, output):
    try:
        state = "true" if settings.allow_death else "false"
        sims4.commands.execute(f"death.toggle {state}", _connection)
        return True
    except Exception as exc:
        output(f"death.toggle failed: {exc}")
        return False


def _set_enabled(enabled: bool, _connection, output):
    settings.enabled = enabled
    if enabled:
        _apply_pregnancy_patch()
        _apply_death_toggle(_connection, output)
        _apply_auto_dialogs(_connection, output)
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
    if _last_patch_error:
        output.append(f"patch_error={_last_patch_error}")
    return " | ".join(output)


def _usage_lines():
    return [
        "simulation status",
        "simulation true|false",
        "simulation set <key> <value>",
        "simulation preset <safe|chaos>",
        "simulation help",
        "keys: auto_unpause, auto_dialogs, allow_death, allow_pregnancy, tick",
    ]


def _emit_help(output):
    output("Simulation Mode v0.3 help:")
    for line in _usage_lines():
        output(f"- {line}")


def _handle_set(key, value, _connection, output):
    if key is None or value is None:
        _emit_help(output)
        return False

    key = key.strip().lower()
    if key in {"auto_unpause", "auto_dialogs", "allow_death", "allow_pregnancy"}:
        parsed = _parse_bool(value)
        if parsed is None:
            output(f"Invalid value for {key}: {value}")
            return False
        setattr(settings, key, parsed)
        if settings.enabled and key == "allow_death":
            _apply_death_toggle(_connection, output)
        if key == "allow_pregnancy":
            if settings.enabled and not settings.allow_pregnancy:
                _apply_pregnancy_patch()
            if settings.allow_pregnancy:
                _set_last_patch_error(None)
        save_settings(settings)
        return True

    if key == "tick":
        try:
            tick_value = int(value)
        except Exception:
            output(f"Invalid value for tick: {value}")
            return False
        _set_tick_seconds(tick_value)
        save_settings(settings)
        return True

    output(f"Unknown setting: {key}")
    return False


def _apply_preset(name, _connection, output):
    preset = name.strip().lower() if name else ""
    if preset == "safe":
        settings.auto_dialogs = True
        settings.allow_death = False
        settings.allow_pregnancy = False
        settings.auto_unpause = True
    elif preset == "chaos":
        settings.auto_dialogs = False
        settings.allow_death = True
        settings.allow_pregnancy = True
        settings.auto_unpause = False
    else:
        output(f"Unknown preset: {name}")
        return False

    save_settings(settings)
    if settings.enabled:
        _apply_death_toggle(_connection, output)
        if settings.allow_pregnancy:
            _set_last_patch_error(None)
        else:
            _apply_pregnancy_patch()
    return True


@sims4.commands.Command("simulation", command_type=CommandType.Live)
def simulation_cmd(action: str = None, key: str = None, value: str = None, _connection=None):
    output = sims4.commands.CheatOutput(_connection)

    parsed = _parse_bool(action)
    if parsed is not None and key is None:
        success, error = _set_enabled(parsed, _connection, output)
        _emit_status(output)
        if parsed:
            if success:
                output("Simulation daemon started successfully.")
            else:
                output(f"Simulation daemon failed to start: {error}")
        return True

    if action is None or action.strip().lower() == "status":
        _emit_status(output)
        return True

    action_key = action.strip().lower()

    if action_key == "help":
        _emit_help(output)
        return True

    if action_key == "set":
        _handle_set(key, value, _connection, output)
        _emit_status(output)
        return True

    if action_key == "preset":
        _apply_preset(key, _connection, output)
        _emit_status(output)
        return True

    if action_key == "debug":
        running, last_error = _daemon_status()
        output(_format_debug(settings.enabled, running, last_error))
        return True

    if action_key == "allow_pregnancy":
        _handle_set("allow_pregnancy", key, _connection, output)
        _emit_status(output)
        return True

    if action_key == "protect_motives":
        parsed = _parse_bool(key)
        if parsed is not None:
            settings.protect_motives = parsed
        _emit_status(output)
        return True

    if action_key == "auto_unpause":
        _handle_set("auto_unpause", key, _connection, output)
        _emit_status(output)
        return True

    if action_key == "auto_dialogs":
        _handle_set("auto_dialogs", key, _connection, output)
        _emit_status(output)
        return True

    if action_key == "allow_death":
        _handle_set("allow_death", key, _connection, output)
        _emit_status(output)
        return True

    if action_key == "tick":
        _handle_set("tick", key, _connection, output)
        _emit_status(output)
        return True

    _emit_status(output)
    return True


@sims4.commands.Command("simulation_mode", command_type=CommandType.Live)
def simulation_mode_cmd(action: str = None, key: str = None, value: str = None, _connection=None):
    return simulation_cmd(action, key, value, _connection)
