import importlib
import os
import time
import traceback

from simulation_mode import director
from simulation_mode import probe_log
from simulation_mode.settings import get_config_path, settings


def get_log_path():
    cfg = os.path.abspath(get_config_path())
    folder = os.path.dirname(cfg)
    return os.path.join(folder, "simulation-mode.log")


def dump_state_to_file(extra_note: str = ""):
    daemon = importlib.import_module("simulation_mode.daemon")
    push_utils = importlib.import_module("simulation_mode.push_utils")
    path = get_log_path()
    try:
        lines = []
        lines.append("=" * 60)
        lines.append(f"SimulationMode dump @ {time.strftime('%Y-%m-%d %H:%M:%S')}")
        if extra_note:
            lines.append(f"NOTE: {extra_note}")
        lines.append(f"config_path={os.path.abspath(get_config_path())}")
        lines.append(
            "unsafe_affordance_cache_size={}".format(
                len(getattr(push_utils, "_UNSAFE_AFFORDANCE_CACHE", []))
            )
        )

        for key in sorted(vars(settings).keys()):
            try:
                lines.append(f"{key}={getattr(settings, key)}")
            except Exception:
                pass

        lines.append("")
        lines.append("DAEMON:")
        for key in (
            "tick_count",
            "last_error",
            "daemon_error",
            "last_tick_wallclock",
            "last_alarm_variant",
            "last_unpause_attempt_ts",
            "last_unpause_result",
            "last_pause_requests_count",
        ):
            try:
                lines.append(f"{key}={getattr(daemon, key, None)}")
            except Exception:
                pass

        lines.append("")
        lines.append("DIRECTOR ACTIONS:")
        for action in list(getattr(director, "last_director_actions", [])):
            lines.append(f"- {action}")

        lines.append("")
        lines.append("DIRECTOR DEBUG:")
        debug_text = getattr(settings, "last_director_debug", "")
        lines.append(f"last_director_debug={debug_text}")

        lines.append("")
        lines.append("PROBE LOG:")
        probe_path = probe_log.get_probe_log_path()
        lines.append(f"probe_log_path={probe_path}")
        try:
            with open(probe_path, "r", encoding="utf-8") as handle:
                probe_tail = handle.readlines()[-40:]
            if probe_tail:
                lines.append("probe_log_tail:")
                lines.extend(line.rstrip("\n") for line in probe_tail)
            else:
                lines.append("probe_log_tail=")
        except Exception as exc:
            lines.append(f"probe_log_error={exc!r}")

        try:
            services = importlib.import_module("services")
            sim_info = None
            getter = getattr(services, "active_sim_info", None)
            if callable(getter):
                sim_info = getter()
            if sim_info is not None:
                snap = director.get_motive_snapshot_for_sim(sim_info)
                lines.append("")
                lines.append("ACTIVE SIM MOTIVES:")
                for key, value in snap:
                    lines.append(f"- {key}={value}")
        except Exception:
            pass

        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
            handle.write("\n")

        return True, path
    except Exception:
        return False, traceback.format_exc()
