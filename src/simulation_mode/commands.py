import importlib
import os
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
        f"director_enabled={settings.director_enabled}",
        f"director_check_seconds={settings.director_check_seconds}",
        f"director_min_safe_motive={settings.director_min_safe_motive}",
        f"director_green_motive_percent={settings.director_green_motive_percent}",
        f"director_green_min_commodities={settings.director_green_min_commodities}",
        f"director_allow_social_goals={settings.director_allow_social_goals}",
        f"director_use_guardian_when_low={settings.director_use_guardian_when_low}",
        f"director_per_sim_cooldown_seconds={settings.director_per_sim_cooldown_seconds}",
        f"director_max_pushes_per_sim_per_hour={settings.director_max_pushes_per_sim_per_hour}",
        f"director_prefer_career_skills={settings.director_prefer_career_skills}",
        f"director_fallback_to_started_skills={settings.director_fallback_to_started_skills}",
        f"director_skill_allow_list={settings.director_skill_allow_list}",
        f"director_skill_block_list={settings.director_skill_block_list}",
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


def _director_snapshot():
    director = importlib.import_module("simulation_mode.director")
    return (
        director.last_director_called_time,
        director.last_director_run_time,
        director.last_director_time,
        list(director.last_director_actions),
        list(director.last_director_debug),
    )


def _active_sim_info():
    services = importlib.import_module("services")
    getter = getattr(services, "active_sim_info", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    sim = services.active_sim()
    if sim is None:
        return None
    return getattr(sim, "sim_info", None)


def _emit_director_motive_snapshot(output, sim_info):
    director = importlib.import_module("simulation_mode.director")
    guardian = importlib.import_module("simulation_mode.guardian")
    snapshot = director.get_motive_snapshot_for_sim(sim_info)
    if not snapshot:
        output("motive_snapshot=")
        return
    output("motive_snapshot:")
    for key, value in snapshot:
        percent = guardian.motive_percent(value)
        green = guardian.motive_is_green(value, settings.director_green_motive_percent)
        output(f"- {key}={value:.1f} percent={percent:.2f} green={green}")


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
        "simulation director",
        "simulation director_gate",
        "simulation director_now",
        "simulation director_why",
        "simulation director_push <skill_key>",
        "simulation director_takeover <skill_key>",
        "simulation configpath",
        "simulation help",
        "keys: auto_unpause, allow_death, allow_pregnancy, tick, guardian_enabled, guardian_check_seconds, "
        "guardian_min_motive, guardian_red_motive, guardian_per_sim_cooldown_seconds, "
        "guardian_max_pushes_per_sim_per_hour, director_enabled, director_check_seconds, "
        "director_min_safe_motive, director_per_sim_cooldown_seconds, "
        "director_green_motive_percent, director_green_min_commodities, "
        "director_allow_social_goals, director_use_guardian_when_low, "
        "director_max_pushes_per_sim_per_hour, director_prefer_career_skills, "
        "director_fallback_to_started_skills, director_skill_allow_list, "
        "director_skill_block_list, integrate_better_autonomy_trait, better_autonomy_trait_id",
    ]


def _emit_help(output):
    output("Simulation Mode v0.5 help:")
    for line in _usage_lines():
        output(f"- {line}")


def _handle_set(key, value, _connection, output):
    if key is None or value is None:
        _emit_help(output)
        return False

    key = key.strip().lower()
    if key in {"auto_unpause", "allow_death", "allow_pregnancy", "guardian_enabled",
               "director_allow_social_goals", "director_use_guardian_when_low",
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
               "director_green_min_commodities", "better_autonomy_trait_id"}:
        try:
            parsed = int(value)
        except Exception:
            output(f"Invalid value for {key}: {value}")
            return False
        setattr(settings, key, parsed)
        output(f"Updated {key} to {parsed}. To persist, edit simulation-mode.txt")
        return True

    if key in {"director_green_motive_percent"}:
        try:
            parsed = float(value)
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

    if action_key == "director":
        last_called, last_run, last_time, actions, _debug = _director_snapshot()
        output(f"director_enabled={settings.director_enabled}")
        output(f"director_check_seconds={settings.director_check_seconds}")
        output(f"director_green_motive_percent={settings.director_green_motive_percent}")
        output(f"director_green_min_commodities={settings.director_green_min_commodities}")
        output(f"director_allow_social_goals={settings.director_allow_social_goals}")
        output(f"director_use_guardian_when_low={settings.director_use_guardian_when_low}")
        output(f"last_director_called_time={last_called}")
        output(f"last_director_run_time={last_run}")
        output(f"last_director_time={last_time}")
        sim_info = _active_sim_info()
        if sim_info is not None:
            _emit_director_motive_snapshot(output, sim_info)
        else:
            output("motive_snapshot= (no active sim)")
        if actions:
            output("last_director_actions:")
            for line in actions[-10:]:
                output(f"- {line}")
        else:
            output("last_director_actions=")
        director = importlib.import_module("simulation_mode.director")
        if director.last_director_debug:
            output("last_director_debug:")
            for line in director.last_director_debug[-10:]:
                output(f"- {line}")
        else:
            output("last_director_debug=")
        return True

    if action_key == "director_gate":
        director = importlib.import_module("simulation_mode.director")
        guardian = importlib.import_module("simulation_mode.guardian")
        sim_info = _active_sim_info()
        if sim_info is None:
            output("No active sim found.")
            return True
        snapshot = director.get_motive_snapshot_for_sim(sim_info)
        if not snapshot:
            output("motive_snapshot= (unavailable)")
            return True
        greens = 0
        for _key, value in snapshot:
            if guardian.motive_is_green(value, settings.director_green_motive_percent):
                greens += 1
        gate_pass = greens >= settings.director_green_min_commodities
        output(f"green_gate_pass={gate_pass}")
        output(f"green_count={greens}")
        output(f"green_min_commodities={settings.director_green_min_commodities}")
        _emit_director_motive_snapshot(output, sim_info)
        return True

    if action_key == "director_now":
        director = importlib.import_module("simulation_mode.director")
        director.run_now(time.time(), force=True)
        last_called, last_run, last_time, actions, debug = _director_snapshot()
        output(f"last_director_called_time={last_called}")
        output(f"last_director_run_time={last_run}")
        output(f"last_director_time={last_time}")
        if actions:
            output("last_director_actions:")
            for line in actions[-10:]:
                output(f"- {line}")
        else:
            output("last_director_actions=")
        if debug:
            output("last_director_debug:")
            for line in debug[-10:]:
                output(f"- {line}")
        else:
            output("last_director_debug=")
        return True

    if action_key == "director_takeover":
        director = importlib.import_module("simulation_mode.director")
        services = importlib.import_module("services")
        skill_key = key.strip().lower() if key else None
        if not skill_key:
            output("Missing skill_key")
            return True
        sim = None
        try:
            client = services.client_manager().get_first_client()
            if client is not None:
                sim = client.active_sim
        except Exception:
            sim = None
        if sim is None:
            output("No active sim found.")
            return True
        cancelled = False
        try:
            if hasattr(sim, "queue") and hasattr(sim.queue, "cancel_all"):
                sim.queue.cancel_all()
                cancelled = True
            elif hasattr(sim, "cancel_all_interactions"):
                sim.cancel_all_interactions()
                cancelled = True
            elif hasattr(sim, "queue") and hasattr(sim.queue, "clear"):
                sim.queue.clear()
                cancelled = True
        except Exception:
            output("director_takeover: cancel attempt failed")
        if not cancelled:
            output("director_takeover: cancel unavailable")
        ok = director.push_skill_now(sim, skill_key, time.time())
        _last_called, _last_run, _last_time, actions, debug = _director_snapshot()
        if ok:
            output(f"director_takeover {skill_key}: success")
            if actions:
                output(f"last_director_action={actions[-1]}")
        else:
            output(f"director_takeover {skill_key}: failure")
            if debug:
                output(f"last_director_debug={debug[-1]}")
        return True

    if action_key == "director_why":
        _last_called, _last_run, _last_time, _actions, debug = _director_snapshot()
        if debug:
            output("last_director_debug:")
            for line in debug[-25:]:
                output(f"- {line}")
        else:
            output("last_director_debug=")
        return True

    if action_key == "director_push":
        director = importlib.import_module("simulation_mode.director")
        services = importlib.import_module("services")
        skill_key = key.strip().lower() if key else None
        if not skill_key:
            output("Missing skill_key")
            return True
        sim = services.active_sim()
        if sim is None:
            output("No active sim found.")
            return True
        ok = director.push_skill_now(sim, skill_key, time.time())
        _last_called, _last_run, _last_time, actions, debug = _director_snapshot()
        if ok:
            output(f"director_push {skill_key}: success")
            if actions:
                output(f"last_director_action={actions[-1]}")
        else:
            output(f"director_push {skill_key}: failure")
            if debug:
                output(f"last_director_debug={debug[-1]}")
        return True

    if action_key == "configpath":
        config_path = os.path.abspath(get_config_path())
        output(f"config_path={config_path}")
        output(f"exists={os.path.exists(config_path)}")
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
