import importlib
import time

import sims4.commands
from sims4.commands import BOOL_TRUE, CommandType

from simulation_mode.settings import get_config_path, load_settings, settings

_FALSE_STRINGS = {"false", "f", "0", "off", "no", "n"}
_TICK_MIN_SECONDS = 1
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


def _daemon_snapshot():
    daemon = importlib.import_module("simulation_mode.daemon")
    return daemon.is_running(), daemon.daemon_error, daemon.tick_count


def _status_lines():
    running, daemon_error, daemon_tick_count = _daemon_snapshot()
    return [
        f"enabled={settings.enabled}",
        f"auto_unpause={settings.auto_unpause}",
        f"allow_death={settings.allow_death}",
        f"allow_pregnancy={settings.allow_pregnancy}",
        f"tick_seconds={settings.tick_seconds}",
        f"guardian_enabled={settings.guardian_enabled}",
        f"guardian_check_seconds={settings.guardian_check_seconds}",
        f"guardian_min_motive={settings.guardian_min_motive}",
        f"guardian_red_motive={settings.guardian_red_motive}",
        f"guardian_per_sim_cooldown_seconds={settings.guardian_per_sim_cooldown_seconds}",
        f"guardian_max_pushes_per_sim_per_hour={settings.guardian_max_pushes_per_sim_per_hour}",
        f"integrate_better_autonomy_trait={settings.integrate_better_autonomy_trait}",
        f"better_autonomy_trait_id={settings.better_autonomy_trait_id}",
        f"daemon_running={running}",
        f"tick_count={daemon_tick_count}",
        f"daemon_error={daemon_error}",
        f"settings_path={get_config_path()}",
    ]


def _emit_status(output):
    for line in _status_lines():
        output(line)


def _start_daemon():
    daemon = importlib.import_module("simulation_mode.daemon")
    try:
        daemon.start()
        if not daemon.is_running():
            return False, daemon.daemon_error or "alarm failed to start"
        return True, None
    except Exception as exc:
        return False, str(exc)


def _stop_daemon():
    daemon = importlib.import_module("simulation_mode.daemon")
    try:
        daemon.stop()
        return True, None
    except Exception as exc:
        return False, str(exc)


def _daemon_status():
    daemon = importlib.import_module("simulation_mode.daemon")
    return daemon.is_running(), daemon.daemon_error


def _apply_pregnancy_patch():
    if not settings.enabled or settings.allow_pregnancy:
        _set_last_patch_error(None)
        return True
    pregnancy_block = importlib.import_module("simulation_mode.patches.pregnancy_block")
    try:
        patched = pregnancy_block.apply_patch()
        if patched:
            _set_last_patch_error(None)
            return True
    except Exception as exc:
        _set_last_patch_error(str(exc))
        return False
    _set_last_patch_error("pregnancy patch unavailable")
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
        daemon = importlib.import_module("simulation_mode.daemon")
        daemon.set_connection(_connection)
        _apply_pregnancy_patch()
        _apply_death_toggle(_connection, output)
        return _start_daemon()
    return _stop_daemon()


def _set_tick_seconds(value: int):
    clamped = max(_TICK_MIN_SECONDS, min(_TICK_MAX_SECONDS, value))
    settings.tick_seconds = clamped
    if settings.enabled:
        _start_daemon()
    return clamped


def _clock_speed_info():
    services = importlib.import_module("services")
    clock = importlib.import_module("clock")
    try:
        clock_service = services.game_clock_service()
        if clock_service is None:
            return None
        speed_attr = getattr(clock_service, "clock_speed", None)
        current_speed = speed_attr() if callable(speed_attr) else speed_attr
        if current_speed is None:
            return None
        speed_name = getattr(current_speed, "name", None)
        if speed_name:
            return speed_name
        if hasattr(clock, "ClockSpeedMode"):
            return clock.ClockSpeedMode(current_speed).name
        if hasattr(clock, "ClockSpeed"):
            return clock.ClockSpeed(current_speed).name
        return str(current_speed)
    except Exception:
        return None


def _format_debug(enabled: bool, running: bool, last_error: str, tick_count: int = None,
                  seconds_since_last_tick: float = None, clock_speed: str = None,
                  last_alarm_variant: str = None, last_unpause_attempt_ts: float = None,
                  last_unpause_result: str = None, last_pause_requests_count: int = None):
    output = [
        f"enabled={enabled}",
        f"daemon_running={running}",
    ]
    if last_error:
        output.append(f"daemon_error={last_error}")
    if _last_patch_error:
        output.append(f"patch_error={_last_patch_error}")
    if tick_count is not None:
        output.append(f"tick_count={tick_count}")
    if seconds_since_last_tick is not None:
        output.append(f"seconds_since_last_tick={seconds_since_last_tick:.1f}")
    if last_alarm_variant:
        output.append(f"last_alarm_variant={last_alarm_variant}")
    if clock_speed:
        output.append(f"clock_speed={clock_speed}")
    if last_unpause_attempt_ts is not None:
        output.append(f"last_unpause_attempt_ts={last_unpause_attempt_ts:.1f}")
    if last_unpause_result:
        output.append(f"last_unpause_result={last_unpause_result}")
    if last_pause_requests_count is not None:
        output.append(f"last_pause_requests_count={last_pause_requests_count}")
    return " | ".join(output)


def _usage_lines():
    return [
        "simulation status",
        "simulation true|false",
        "simulation set <key> <value>",
        "simulation set tick 1..120",
        "simulation reload",
        "simulation help",
        "keys: auto_unpause, allow_death, allow_pregnancy, tick, guardian_enabled, guardian_check_seconds, "
        "guardian_min_motive, guardian_red_motive, guardian_per_sim_cooldown_seconds, "
        "guardian_max_pushes_per_sim_per_hour, integrate_better_autonomy_trait, better_autonomy_trait_id",
    ]


def _emit_help(output):
    output("Simulation Mode v0.4 help:")
    for line in _usage_lines():
        output(f"- {line}")


def _handle_set(key, value, _connection, output):
    if key is None or value is None:
        _emit_help(output)
        return False

    key = key.strip().lower()
    if key in {"auto_unpause", "allow_death", "allow_pregnancy", "guardian_enabled",
               "integrate_better_autonomy_trait"}:
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
        output(f"Updated {key} to {parsed}. To persist, edit simulation-mode.txt")
        return True

    if key == "tick":
        try:
            tick_value = int(value)
        except Exception:
            output(f"Invalid value for tick: {value}")
            return False
        _set_tick_seconds(tick_value)
        output(f"Updated tick_seconds to {settings.tick_seconds}. To persist, edit simulation-mode.txt")
        return True

    if key in {"guardian_check_seconds", "guardian_min_motive", "guardian_red_motive",
               "guardian_per_sim_cooldown_seconds", "guardian_max_pushes_per_sim_per_hour",
               "better_autonomy_trait_id"}:
        try:
            parsed = int(value)
        except Exception:
            output(f"Invalid value for {key}: {value}")
            return False
        setattr(settings, key, parsed)
        output(f"Updated {key} to {parsed}. To persist, edit simulation-mode.txt")
        return True

    output(f"Unknown setting: {key}")
    return False


def _reload_settings(_connection, output):
    was_enabled = settings.enabled
    load_settings(settings)
    if settings.enabled:
        daemon = importlib.import_module("simulation_mode.daemon")
        daemon.set_connection(_connection)
        _apply_death_toggle(_connection, output)
        if settings.allow_pregnancy:
            _set_last_patch_error(None)
        else:
            _apply_pregnancy_patch()
        _start_daemon()
    elif was_enabled:
        _stop_daemon()
    output("Reloaded settings from disk.")
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

    if action_key == "reload":
        _reload_settings(_connection, output)
        _emit_status(output)
        return True

    if action_key == "debug":
        running, last_error = _daemon_status()
        tick_count = None
        seconds_since_last_tick = None
        last_alarm_variant = None
        clock_speed = _clock_speed_info()
        daemon = importlib.import_module("simulation_mode.daemon")
        last_unpause_attempt_ts = None
        last_unpause_result = None
        last_pause_requests_count = None
        try:
            tick_count = daemon.tick_count
            if daemon.last_tick_wallclock and daemon.last_tick_wallclock > 0:
                seconds_since_last_tick = time.time() - daemon.last_tick_wallclock
            last_alarm_variant = daemon.last_alarm_variant
            last_unpause_attempt_ts = daemon.last_unpause_attempt_ts
            last_unpause_result = daemon.last_unpause_result
            last_pause_requests_count = daemon.last_pause_requests_count
        except Exception:
            pass
        output(_format_debug(
            settings.enabled,
            running,
            last_error,
            tick_count=tick_count,
            seconds_since_last_tick=seconds_since_last_tick,
            last_alarm_variant=last_alarm_variant,
            clock_speed=clock_speed,
            last_unpause_attempt_ts=last_unpause_attempt_ts,
            last_unpause_result=last_unpause_result,
            last_pause_requests_count=last_pause_requests_count,
        ))
        return True

    if action_key == "allow_pregnancy":
        _handle_set("allow_pregnancy", key, _connection, output)
        _emit_status(output)
        return True

    if action_key == "auto_unpause":
        _handle_set("auto_unpause", key, _connection, output)
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
