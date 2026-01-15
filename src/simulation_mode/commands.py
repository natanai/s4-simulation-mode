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
_PENDING_SKILL_PLAN_PUSHES = {}
# Keep alarm handles alive per-sim so they are not garbage-collected.
_PENDING_SKILL_PLAN_ALARMS = {}


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


def _safe_get(obj, name, default=None):
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _safe_call(obj, name, *args, **kwargs):
    fn = _safe_get(obj, name, None)
    if not callable(fn):
        return False, None, f"not callable: {name}"
    try:
        return True, fn(*args, **kwargs), None
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"


def _filter_names(obj, contains):
    out = []
    for name in dir(obj):
        lower_name = name.lower()
        if any(token in lower_name for token in contains):
            out.append(name)
    return sorted(set(out))


def _status_lines():
    running, daemon_error, daemon_tick_count = _daemon_snapshot()
    try:
        package = importlib.import_module("simulation_mode")
        version = getattr(package, "__version__", None)
    except Exception:
        version = None
    return [
        f"version={version}" if version else "version=unknown",
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
        f"director_allow_social_wants={settings.director_allow_social_wants}",
        f"director_enable_wants={settings.director_enable_wants}",
        f"director_use_guardian_when_low={settings.director_use_guardian_when_low}",
        f"director_per_sim_cooldown_seconds={settings.director_per_sim_cooldown_seconds}",
        f"director_max_pushes_per_sim_per_hour={settings.director_max_pushes_per_sim_per_hour}",
        f"director_prefer_career_skills={settings.director_prefer_career_skills}",
        f"director_fallback_to_started_skills={settings.director_fallback_to_started_skills}",
        f"director_skill_allow_list={settings.director_skill_allow_list}",
        f"director_skill_block_list={settings.director_skill_block_list}",
        f"collect_log_filename={settings.collect_log_filename}",
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


def _get_active_sim(services):
    getter = getattr(services, "active_sim", None)
    if callable(getter):
        try:
            sim = getter()
            if sim is not None:
                return sim
        except Exception:
            pass
    try:
        client = services.client_manager().get_first_client()
        if client is not None:
            return client.active_sim
    except Exception:
        return None
    return None


def _append_probe_log(title, lines):
    probe_log = importlib.import_module("simulation_mode.probe_log")
    probe_log.append_probe_block(title, lines)


def _trim_repr(value, limit=200):
    try:
        text = repr(value)
    except Exception as exc:
        text = f"<repr failed: {exc}>"
    if text is None:
        return ""
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def _iter_probe_container(tracker):
    slots_gen = _safe_get(tracker, "slots_gen")
    if callable(slots_gen):
        try:
            slots = list(slots_gen())
        except Exception:
            slots = None
        if slots:
            return slots, "slots_gen()"
    for attr in (
        "_whim_slots",
        "slots",
        "active_wants",
        "active_whims",
        "_active_wants",
        "_active_whims",
    ):
        value = _safe_get(tracker, attr)
        if value is None:
            continue
        try:
            slots = list(value)
        except Exception:
            continue
        if slots:
            return slots, attr
    return None, None


def _select_want_tracker(sim_info):
    if sim_info is None:
        return None, None
    tracker = _safe_get(sim_info, "whim_tracker")
    if tracker is not None:
        return "whim_tracker", tracker
    tracker = _safe_get(sim_info, "_whim_tracker")
    if tracker is not None:
        return "_whim_tracker", tracker
    for token in ("want",):
        for name in dir(sim_info):
            if token not in name.lower():
                continue
            value = _safe_get(sim_info, name)
            if value is not None:
                if isinstance(value, (list, tuple)) and "current" in name.lower():
                    continue
                return name, value
    for token in ("whim",):
        for name in dir(sim_info):
            if token not in name.lower():
                continue
            value = _safe_get(sim_info, name)
            if value is not None:
                if isinstance(value, (list, tuple)) and "current" in name.lower():
                    continue
                return name, value
    return None, None


def _find_first_attr(obj, attrs):
    if obj is None:
        return None, None
    for attr in attrs:
        if hasattr(obj, attr):
            value = _safe_get(obj, attr)
            if value is not None:
                return attr, value
    return None, None


def _log_identifiers(lines, prefix, obj):
    if obj is None:
        return
    for attr in ("guid64", "_guid64", "tuning_id", "_tuning_id", "instance_id"):
        if hasattr(obj, attr):
            value = _safe_get(obj, attr)
            lines.append(f"{prefix}{attr}={value!r}")


def _log(message):
    try:
        _append_simulation_log([str(message)])
    except Exception:
        pass


def _append_simulation_log(lines):
    log_dump = importlib.import_module("simulation_mode.log_dump")
    path = log_dump.get_log_path()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(f"[{timestamp}] {line}" for line in lines))
        handle.write("\n")


def _collect_config_snapshot():
    lines = ["COLLECT: CONFIG"]
    lines.append(f"config_path={os.path.abspath(get_config_path())}")
    for key in sorted(vars(settings).keys()):
        try:
            lines.append(f"{key}={getattr(settings, key)}")
        except Exception:
            continue
    return lines


def _collect_daemon_snapshot():
    daemon = importlib.import_module("simulation_mode.daemon")
    lines = ["COLLECT: DAEMON"]
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
            continue
    return lines


def _collect_career_summary(sim_info):
    director = importlib.import_module("simulation_mode.director")
    lines = []
    tracker = _safe_get(sim_info, "career_tracker")
    if tracker is None:
        lines.append("career_tracker= (not found)")
        return lines
    lines.append(f"career_tracker_type={type(tracker).__name__}")
    careers = director._iter_careers(sim_info)
    if not careers:
        lines.append("career_current= (none)")
        return lines
    for career in careers:
        career_name = getattr(getattr(career, "__class__", None), "__name__", "")
        track = _safe_get(career, "current_track")
        track_name = getattr(track, "__name__", None) if track is not None else None
        level = _safe_get(career, "career_level") or _safe_get(career, "level")
        lines.append(
            f"career_name={career_name} track={track_name} level={level}"
        )
        for attr in ("guid64", "uid", "tuning_id", "instance_id"):
            if hasattr(career, attr):
                lines.append(f"career_{attr}={_safe_get(career, attr)!r}")
    return lines


def _collect_started_skills(sim_info):
    director = importlib.import_module("simulation_mode.director")
    lines = []
    candidates = director._get_started_skill_candidates(sim_info)
    if not candidates:
        lines.append("started_skills= (none)")
        return lines
    lines.append("started_skills:")
    for skill_key, _reason, level in candidates:
        lines.append(f"- {skill_key} level={level}")
    return lines


def _collect_affordance_probe_lines(sim_info):
    """
    Probe-only: enumerate super-affordances on lot objects and log:
    - tuning flags (user-directed/autonomous/cheat/debug)
    - picker-like detection
    - whether affordance tests pass using resolver if available
    CAPPED for safety (performance).
    """
    importlib_util = importlib.import_module("importlib.util")
    push_utils = importlib.import_module("simulation_mode.push_utils")

    # caps (hard-coded for now; probe-only)
    MAX_OBJECTS = 80
    MAX_AFFORDANCES_PER_OBJECT = 30
    MAX_RESULTS = 120  # max lines for “good candidates”

    lines = []
    header = (
        "AFFORDANCE PROBE: test-passing affordances + whether they look user-directed"
    )
    lines.append(header)

    # resolve sim instance
    try:
        sim = sim_info.get_sim_instance()
    except Exception as e:
        return [header, f"  ERROR: sim_info.get_sim_instance() failed: {e!r}"]
    if sim is None:
        return [header, "  No live Sim instance (sim is not instantiated)."]

    # Try to import resolvers for running tests
    resolver_note = ""
    SingleActorAndObjectResolver = None
    SingleSimResolver = None
    if importlib_util.find_spec("event_testing.resolver") is None:
        resolver_note = (
            "  NOTE: resolver module not found; tests will be logged as unknown."
        )
    else:
        resolver_mod = importlib.import_module("event_testing.resolver")
        SingleActorAndObjectResolver = getattr(
            resolver_mod, "SingleActorAndObjectResolver", None
        )
        SingleSimResolver = getattr(resolver_mod, "SingleSimResolver", None)
        if SingleActorAndObjectResolver is None and SingleSimResolver is None:
            resolver_note = (
                "  NOTE: Could not find SingleActorAndObjectResolver/SingleSimResolver; "
                "tests will be logged as unknown."
            )
    if resolver_note:
        lines.append(resolver_note)

    scanned_objects = 0
    scanned_affordances = 0

    # exclusion counters
    excl_picker = 0
    excl_cheat = 0
    excl_debug = 0
    excl_no_tests = 0
    excl_tests_unknown = 0
    excl_tests_fail = 0
    good_candidates = 0

    # Helper to read tuning-ish fields safely
    def _get_bool(obj, *names, default=False):
        for name in names:
            if hasattr(obj, name):
                try:
                    value = getattr(obj, name)
                    # some tunables might be properties/callables
                    if callable(value):
                        value = value()
                    return bool(value)
                except Exception:
                    continue
        return default

    def _get_int(obj, *names, default=None):
        for name in names:
            if hasattr(obj, name):
                try:
                    value = getattr(obj, name)
                    if callable(value):
                        value = value()
                    return value
                except Exception:
                    continue
        return default

    def _aff_name(aff):
        try:
            return getattr(aff, "__name__", None) or str(aff)
        except Exception:
            return "<aff?>"

    def _obj_name(obj):
        try:
            # prefer definition name if present
            if (
                hasattr(obj, "definition")
                and obj.definition is not None
                and hasattr(obj.definition, "name")
            ):
                return obj.definition.name
        except Exception:
            pass
        try:
            return str(obj)
        except Exception:
            return "<obj?>"

    # Decide whether an affordance “looks user-directed”
    # (i.e., allowed user-directed, not cheat/debug, not picker-like)
    def _looks_user_directed(aff, allow_ud, cheat, debug, is_picker):
        return bool(allow_ud) and (not cheat) and (not debug) and (not is_picker)

    # Evaluate tests if possible
    def _tests_pass(aff, target_obj):
        """
        Return tuple: (status_str, pass_bool_or_none)
        pass_bool_or_none:
          - True/False when we can evaluate
          - None when unknown (no resolver/tests structure)
        """
        # tests / test_globals may exist (tunables); if absent, we log “no_tests”
        tests = getattr(aff, "tests", None)
        test_globals = getattr(aff, "test_globals", None)

        if tests is None and test_globals is None:
            return ("no_tests", None)

        # Build resolver
        resolver = None
        if SingleActorAndObjectResolver is not None:
            try:
                resolver = SingleActorAndObjectResolver(sim_info, target_obj)
            except Exception:
                resolver = None
        if resolver is None and SingleSimResolver is not None:
            try:
                resolver = SingleSimResolver(sim_info)
            except Exception:
                resolver = None

        if resolver is None:
            return ("unknown_no_resolver", None)

        # Run tests if object exposes run_tests(resolver)
        def _run_testset(testset):
            if testset is None:
                return True
            # Most TS4 test sets expose run_tests; guard strictly.
            fn = getattr(testset, "run_tests", None)
            if fn is None or not callable(fn):
                return None
            try:
                return bool(fn(resolver))
            except Exception:
                return False

        # `tests`
        res_tests = _run_testset(tests)
        if res_tests is None:
            return ("unknown_tests_structure", None)
        if res_tests is False:
            return ("fail_tests", False)

        # `test_globals`
        res_globals = _run_testset(test_globals)
        if res_globals is None:
            return ("unknown_test_globals_structure", None)
        if res_globals is False:
            return ("fail_test_globals", False)

        return ("pass", True)

    # Enumerate objects and affordances
    try:
        for obj in push_utils.iter_objects():
            scanned_objects += 1
            if scanned_objects > MAX_OBJECTS:
                break

            # Gather super affordances (existing helper already sorts)
            try:
                sa_list = list(push_utils.iter_super_affordances(obj))
            except Exception:
                continue

            if not sa_list:
                continue

            # Cap per object
            for aff in sa_list[:MAX_AFFORDANCES_PER_OBJECT]:
                scanned_affordances += 1

                # Fields (safe)
                allow_ud = _get_bool(
                    aff, "allow_user_directed", "_allow_user_directed", default=False
                )
                allow_auto = _get_bool(
                    aff, "allow_autonomous", "_allow_autonomous", default=False
                )
                cheat = _get_bool(aff, "cheat", "_cheat", default=False)
                debug = _get_bool(aff, "debug", "_debug", default=False)
                guid64 = _get_int(aff, "guid64", default=None)

                # Picker-like (existing helper)
                try:
                    is_picker = bool(push_utils.is_picker_affordance(aff))
                except Exception:
                    is_picker = False

                # Exclusion markers
                if cheat:
                    excl_cheat += 1
                if debug:
                    excl_debug += 1
                if is_picker:
                    excl_picker += 1

                # Tests
                status, passed = _tests_pass(aff, obj)
                if status == "no_tests":
                    excl_no_tests += 1
                elif status.startswith("unknown"):
                    excl_tests_unknown += 1
                elif status.startswith("fail"):
                    excl_tests_fail += 1

                looks_ud = _looks_user_directed(aff, allow_ud, cheat, debug, is_picker)

                # We only list “good candidates” in detail to keep logs readable:
                # requirement: tests PASS (True) AND looks user-directed.
                if passed is True and looks_ud:
                    good_candidates += 1
                    lines.append(
                        f"  CANDIDATE obj={_obj_name(obj)}(id={getattr(obj,'id',None)}) "
                        f"aff={_aff_name(aff)}(guid64={guid64}) "
                        f"allow_ud={allow_ud} allow_auto={allow_auto} "
                        f"cheat={cheat} debug={debug} picker={is_picker} tests={status}"
                    )
                    if good_candidates >= MAX_RESULTS:
                        break

            if good_candidates >= MAX_RESULTS:
                break

    except Exception as e:
        lines.append(f"  ERROR during enumeration: {e!r}")

    # Summary
    lines.append(
        "  SUMMARY scanned_objects={} scanned_affordances={} good_candidates={}".format(
            scanned_objects, scanned_affordances, good_candidates
        )
    )
    lines.append(
        "  EXCLUSIONS "
        "picker={} cheat={} debug={} "
        "no_tests={} tests_unknown={} tests_fail={}".format(
            excl_picker,
            excl_cheat,
            excl_debug,
            excl_no_tests,
            excl_tests_unknown,
            excl_tests_fail,
        )
    )

    if good_candidates == 0:
        lines.append(
            "  NOTE: No test-passing user-directed candidates found in sample. "
            "This is a SIGNAL for later (not a behavior change)."
        )
        lines.append(
            "  NOTE: If this repeats, increase caps or improve resolver choice; "
            "do NOT change director behavior in this build."
        )

    return lines


def _collect_aspiration_summary(sim_info):
    lines = []
    tracker = _safe_get(sim_info, "aspiration_tracker")
    if tracker is None:
        lines.append("aspiration_tracker= (not found)")
        return lines
    lines.append(f"aspiration_tracker_type={type(tracker).__name__}")
    active = _safe_get(tracker, "_active_aspiration") or _safe_get(tracker, "active_aspiration")
    if active is None:
        lines.append("active_aspiration= (none)")
    else:
        asp_name = (
            _safe_get(active, "__name__")
            or _safe_get(active, "name")
            or _safe_get(active, "display_name")
            or str(active)
        )
        lines.append(f"active_aspiration={asp_name}")
        for attr in ("guid64", "tuning_id", "instance_id"):
            if hasattr(active, attr):
                lines.append(f"active_aspiration_{attr}={_safe_get(active, attr)!r}")
    milestone_lines = []
    if active is not None:
        for name in dir(active):
            if not any(token in name.lower() for token in ("milestone", "goal")):
                continue
            value = _safe_get(active, name)
            if isinstance(value, (list, tuple)) and value:
                for item in list(value)[:5]:
                    ids = _probe_item_ids(item)
                    milestone_lines.append(
                        "{}: type={} ids={} repr={}".format(
                            name,
                            type(item).__name__,
                            ids if ids else "none",
                            _trim_repr(item),
                        )
                    )
    if milestone_lines:
        lines.append("aspiration_milestones_goals:")
        lines.extend(f"- {line}" for line in milestone_lines)
    else:
        lines.append("aspiration_milestones_goals= (none)")
        attr_names = _filter_names(
            tracker, ("aspir", "milestone", "goal", "track", "current", "active")
        )
        lines.append(f"aspiration_tracker_attrs_hint={attr_names[:12]}")
    try:
        obj_tracker = getattr(active, "objective_tracker", None)
        if obj_tracker is None:
            obj_tracker = getattr(sim_info, "aspiration_tracker", None)
        objectives = None
        if obj_tracker is not None:
            getter = getattr(obj_tracker, "get_objectives", None)
            if callable(getter):
                objectives = getter()
            elif hasattr(obj_tracker, "objectives"):
                objectives = getattr(obj_tracker, "objectives")
            elif hasattr(obj_tracker, "_objectives"):
                objectives = getattr(obj_tracker, "_objectives")
        if objectives:
            lines.append("aspiration_objectives:")
            for objective in list(objectives)[:20]:
                objective_name = objective.__class__.__name__
                guid64 = getattr(objective, "guid64", None)
                completed = getattr(objective, "completed", None)
                if completed is None:
                    completed = getattr(objective, "is_completed", None)
                if callable(completed):
                    completed = completed()
                current = getattr(objective, "current_value", None)
                if callable(current):
                    current = current()
                target = getattr(objective, "target_value", None)
                if callable(target):
                    target = target()
                lines.append(
                    "  objective type={} guid64={} completed={} current={} target={}".format(
                        objective_name, guid64, completed, current, target
                    )
                )
        else:
            lines.append("aspiration_objectives= (none)")
    except Exception as exc:
        lines.append(f"objective probe unavailable: {exc!r}")
    return lines


def _affordance_label(value):
    return (
        _safe_get(value, "__name__")
        or _safe_get(value, "__qualname__")
        or _safe_get(value, "name")
        or str(value)
    )


def _collect_active_sim_details(sim):
    lines = ["COLLECT: ACTIVE SIM"]
    if sim is None:
        lines.append("active_sim= (none)")
        return lines
    queue = _safe_get(sim, "queue")
    if queue is None:
        lines.append("queue= (none)")
        return lines
    cancel_methods = _filter_names(queue, ("cancel", "clear", "stop"))
    lines.append(f"queue_cancel_methods={cancel_methods}")
    running = _safe_get(queue, "running")
    if running is None:
        lines.append("running_interaction= (none)")
        return lines
    lines.append(f"running_interaction_type={type(running).__name__}")
    running_affordance = _safe_get(running, "super_affordance")
    if running_affordance is None:
        running_affordance = _safe_get(running, "affordance")
    if running_affordance is not None:
        lines.append(f"running_affordance_name={_affordance_label(running_affordance)}")
    else:
        lines.append("running_affordance_name= (unavailable)")
    return lines


def _collect_aspiration_probe_lines(sim_info):
    lines = ["COLLECT: ASPIRATIONS"]
    tracker = _safe_get(sim_info, "aspiration_tracker")
    if tracker is None:
        lines.append("aspiration_tracker= (not found)")
        return lines
    lines.append(f"aspiration_tracker_type={type(tracker).__name__}")
    active = _safe_get(tracker, "active_aspiration") or _safe_get(
        tracker, "_active_aspiration"
    )
    if active is None:
        lines.append("active_aspiration= (none)")
    else:
        lines.append(f"active_aspiration={_affordance_label(active)}")
    milestone_attr, milestone_value = _find_first_attr(
        tracker,
        (
            "current_milestone",
            "_current_milestone",
            "active_milestone",
            "milestone",
            "_milestone",
            "current_goal",
        ),
    )
    if milestone_value is None and callable(_safe_get(tracker, "get_current_milestone")):
        ok, result, error = _safe_call(tracker, "get_current_milestone")
        milestone_attr = "get_current_milestone()"
        milestone_value = result if ok else f"error {error}"
    lines.append(f"current_milestone_source={milestone_attr}")
    lines.append(f"current_milestone={_trim_repr(milestone_value)}")
    list_attrs = (
        "completed_milestones",
        "_completed_milestones",
        "completed_objectives",
        "_completed_objectives",
        "completed_goals",
        "_completed_goals",
        "objectives",
        "milestones",
    )
    for name in list_attrs:
        value = _safe_get(tracker, name)
        if value is None:
            continue
        if callable(value):
            ok, result, error = _safe_call(tracker, name)
            if not ok:
                lines.append(f"{name}=error {error}")
                continue
            value = result
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            lines.append(f"{name}_count={len(items)}")
            if items:
                lines.append(
                    f"{name}_sample={[ _trim_repr(item) for item in items[:3] ]}"
                )
        else:
            lines.append(f"{name}={_trim_repr(value)}")
    return lines


def _collect_plan_preview(sim, now):
    director = importlib.import_module("simulation_mode.director")
    guardian = importlib.import_module("simulation_mode.guardian")
    preview = director.build_plan_preview(sim, now=now)
    if preview is None:
        return ["plan_preview= (unavailable)"]
    lines = []
    sim_info = getattr(sim, "sim_info", None)
    sim_name = director._sim_display_name(sim_info) if sim_info is not None else "Sim"
    sim_id = director._sim_identifier(sim_info) if sim_info is not None else None
    lines.append(f"sim_name={sim_name}")
    last_push = guardian.get_last_push_timestamp(sim_id)
    secs_since_last = None if last_push is None else now - last_push
    chosen_motive = guardian.get_last_chosen_motive(sim_id)
    running_care_relevant = False
    if chosen_motive is not None:
        running_care_relevant = guardian._is_running_care_for_motive(sim, chosen_motive)
    lines.append(f"guardian_last_push_ts={last_push}")
    lines.append(f"guardian_secs_since_last={secs_since_last}")
    lines.append(
        f"guardian_cooldown_seconds={settings.guardian_per_sim_cooldown_seconds}"
    )
    lines.append(f"guardian_chosen_motive={chosen_motive}")
    lines.append(f"guardian_running_care_relevant={running_care_relevant}")
    lines.append(
        f"busy={preview['busy']} reason={preview['busy_reason']}"
    )
    lines.append(
        f"cooldown_ok={preview['cooldown_ok']} time_since_last_push={preview['time_since_last_push']}"
    )
    lines.append(
        "motive_safe={} min_motive={} threshold={}".format(
            not preview["motive_unsafe"],
            preview["min_motive"],
            settings.director_min_safe_motive,
        )
    )
    lines.append(
        "green_count={}/{} green_min={} green_percent={}".format(
            preview["green_count"],
            preview["motive_total"],
            settings.director_green_min_commodities,
            settings.director_green_motive_percent,
        )
    )
    if preview["snapshot"]:
        lines.append("motive_snapshot:")
        for key, value in preview["snapshot"]:
            percent = guardian.motive_percent(value)
            lines.append(f"- {key}={value:.1f} percent={percent:.2f}")
    else:
        lines.append("motive_snapshot= (none)")
    lines.append(f"plan={preview['plan']} reason={preview['plan_reason']}")
    if preview["plan"] == "GUARDIAN":
        lines.append("plan_preview=GUARDIAN")
    elif preview["plan"] == "SKILLS":
        chosen = preview["chosen_skill"]
        if chosen:
            lines.append(f"plan_preview=SKILLS chosen_skill={chosen[0]}")
            lines.append(f"plan_choice_reason={chosen[1]}")
        else:
            lines.append("plan_preview=SKILLS chosen_skill= (none)")
        candidates = preview["candidates"][:5]
        if candidates:
            lines.append("candidate_skills_top5:")
            for skill_key, reason in candidates:
                lines.append(f"- {skill_key} ({reason})")
        else:
            lines.append("candidate_skills_top5= (none)")
    return lines


def _collect_internal_probes(sim_info):
    lines = ["COLLECT: INTERNAL PROBES"]
    lines.append("PROBE ALL (EMBEDDED)")
    lines.extend(_probe_siminfo_tracker_introspection(sim_info))
    deep_lines, _tracker, _slots = _probe_active_wants_deep(sim_info)
    lines.append("ACTIVE WANTS / WHIMS (DEEP DUMP)")
    lines.extend(deep_lines)
    lines.append("CAREER SUMMARY (PROBE)")
    lines.extend(_collect_career_summary(sim_info))
    lines.append("ASPIRATION SUMMARY (PROBE)")
    lines.extend(_collect_aspiration_summary(sim_info))
    lines += [""] + _collect_affordance_probe_lines(sim_info)
    tracker = _safe_get(sim_info, "career_tracker")
    if tracker is not None:
        attrs = _filter_names(tracker, ("career", "level", "track", "promotion"))
        lines.append(
            f"career_tracker_attrs_hint={type(tracker).__name__} attrs={attrs[:10]}"
        )
    tracker = _safe_get(sim_info, "aspiration_tracker")
    if tracker is not None:
        attrs = _filter_names(tracker, ("aspir", "milestone", "goal", "track", "current"))
        lines.append(
            f"aspiration_tracker_attrs_hint={type(tracker).__name__} attrs={attrs[:10]}"
        )
    tracker = _safe_get(sim_info, "skill_tracker")
    if tracker is not None:
        attrs = _filter_names(tracker, ("skill", "level", "max"))
        lines.append(
            f"skill_tracker_attrs_hint={type(tracker).__name__} attrs={attrs[:10]}"
        )
    return lines


def _cancel_sim_interactions(sim):
    if sim is None:
        return False, "no sim"
    try:
        if hasattr(sim, "queue") and hasattr(sim.queue, "cancel_all"):
            sim.queue.cancel_all()
            return True, "queue.cancel_all"
        if hasattr(sim, "cancel_all_interactions"):
            sim.cancel_all_interactions()
            return True, "sim.cancel_all_interactions"
        if hasattr(sim, "queue") and hasattr(sim.queue, "clear"):
            sim.queue.clear()
            return True, "queue.clear"
    except Exception as exc:
        return False, f"cancel_failed: {exc}"
    return False, "cancel_unavailable"


def _schedule_skill_plan_push(sim_info, goal_skill, reason, delay_sim_seconds=3):
    if sim_info is None:
        return False, "no sim_info"
    sim_id = getattr(sim_info, "sim_id", None)
    if sim_id is None:
        return False, "no sim_id"
    _PENDING_SKILL_PLAN_PUSHES[sim_id] = {
        "goal_skill": goal_skill,
        "reason": reason,
        "delay_sim_seconds": delay_sim_seconds,
        "scheduled_at": time.time(),
    }
    alarms = importlib.import_module("alarms")
    clock = importlib.import_module("clock")
    delay_seconds = float(delay_sim_seconds)

    # Cancel any existing pending alarm for this sim.
    old_handle = _PENDING_SKILL_PLAN_ALARMS.pop(sim_id, None)
    if old_handle is not None:
        try:
            old_handle.cancel()
        except Exception:
            pass

    def _skill_plan_retry_cb(handle, _sim_id=sim_id):
        # Callback receives handle; we ignore it except to keep signature valid.
        _PENDING_SKILL_PLAN_ALARMS.pop(_sim_id, None)
        try:
            _execute_skill_plan_push(_sim_id)
        except Exception as e:
            # Keep failure visible in logs.
            _log(f"skill_plan retry callback failed sim_id={_sim_id}: {e}")

    try:
        # CRITICAL: call add_alarm_real_time with ONLY (owner, timespan, callback)
        handle = alarms.add_alarm_real_time(
            sim_info,
            clock.interval_in_real_seconds(delay_seconds),
            _skill_plan_retry_cb,
        )
        _PENDING_SKILL_PLAN_ALARMS[sim_id] = handle
        scheduled_retry_ok = True
        scheduled_retry_reason = "ok"
    except Exception as e:
        _PENDING_SKILL_PLAN_PUSHES.pop(sim_id, None)
        _PENDING_SKILL_PLAN_ALARMS.pop(sim_id, None)
        scheduled_retry_ok = False
        scheduled_retry_reason = f"alarm_failed: {e}"
    return scheduled_retry_ok, scheduled_retry_reason


def _format_push_attempts(lines, attempts, label):
    lines.append(f"{label}_push_attempts_count={len(attempts)}")
    if not attempts:
        return
    lines.append(f"{label}_push_attempts:")
    for idx, attempt in enumerate(attempts):
        lines.append(
            "  [{}] aff_name={} aff_class={} is_picker={} push_ok={} "
            "push_sig_names={} push_reason={}".format(
                idx,
                attempt.get("affordance_name"),
                attempt.get("affordance_class"),
                attempt.get("affordance_is_picker"),
                attempt.get("push_ok"),
                attempt.get("push_sig_names"),
                attempt.get("push_reason"),
            )
        )


def _resolve_sim_info_by_id(sim_id):
    services = importlib.import_module("services")
    manager = services.sim_info_manager()
    getter = getattr(manager, "get", None)
    if callable(getter):
        try:
            sim_info = getter(sim_id)
            if sim_info is not None:
                return sim_info
        except Exception:
            pass
    getter = getattr(manager, "get_sim_info_by_id", None)
    if callable(getter):
        try:
            sim_info = getter(sim_id)
            if sim_info is not None:
                return sim_info
        except Exception:
            pass
    try:
        for sim_info in manager.get_all():
            if getattr(sim_info, "sim_id", None) == sim_id:
                return sim_info
    except Exception:
        return None
    return None


def _execute_skill_plan_push(sim_id):
    _PENDING_SKILL_PLAN_ALARMS.pop(sim_id, None)
    payload = _PENDING_SKILL_PLAN_PUSHES.pop(sim_id, None)
    if not payload:
        return False
    director = importlib.import_module("simulation_mode.director")
    logging_utils = importlib.import_module("simulation_mode.logging_utils")
    sim_info = _resolve_sim_info_by_id(sim_id)
    result_lines = [
        "skill_plan_now_retry=SKILL_PLAN_NOW_RETRY",
        f"sim_id={sim_id}",
        f"goal_skill={payload.get('goal_skill')}",
        f"original_reason={payload.get('reason')}",
        f"delay_sim_seconds={payload.get('delay_sim_seconds')}",
    ]
    if sim_info is None:
        result_lines.append("skill_plan_retry_result=FAIL reason=sim_info_missing")
        payload_text = "\n".join(result_lines)
        logging_utils.append_log_block(
            settings.collect_log_filename, "SimulationMode SKILL_PLAN_NOW_RETRY", payload_text
        )
        return False
    sim = None
    getter = getattr(sim_info, "get_sim_instance", None)
    if callable(getter):
        try:
            sim = getter()
        except Exception:
            sim = None
    if sim is None:
        sim = getattr(sim_info, "sim", None)
    if sim is None:
        result_lines.append("skill_plan_retry_result=FAIL reason=sim_instance_missing")
        payload_text = "\n".join(result_lines)
        logging_utils.append_log_block(
            settings.collect_log_filename, "SimulationMode SKILL_PLAN_NOW_RETRY", payload_text
        )
        return False
    probe_details = {}
    ok = director.try_push_skill_interaction(
        sim, payload.get("goal_skill"), force=True, probe_details=probe_details
    )
    result_lines.append(f"push_ok={ok}")
    result_lines.append(f"chosen_object={probe_details.get('target_label')}")
    result_lines.append(f"chosen_affordance={probe_details.get('chosen_affordance')}")
    attempts = probe_details.get("push_attempts", [])
    _format_push_attempts(result_lines, attempts, "retry")
    candidate_details = probe_details.get("candidate_affordances", [])
    candidate_names = [entry.get("affordance_name") for entry in candidate_details]
    result_lines.append(f"candidate_affordances={candidate_names}")
    if probe_details.get("failure_reason"):
        result_lines.append(f"failure_reason={probe_details.get('failure_reason')}")
    payload_text = "\n".join(result_lines)
    logging_utils.append_log_block(
        settings.collect_log_filename, "SimulationMode SKILL_PLAN_NOW_RETRY", payload_text
    )
    if ok:
        director._record_push(director._sim_identifier(sim_info), time.time())
        director._record_action(sim_info, payload.get("goal_skill"), "skill_plan_now(retry)", time.time())
    return ok


def _build_collect_payload():
    director = importlib.import_module("simulation_mode.director")
    services = importlib.import_module("services")
    lines = []
    lines.extend(_collect_config_snapshot())
    lines.append("")
    lines.extend(_collect_daemon_snapshot())
    lines.append("")
    lines.append("COLLECT: DIRECTOR — PLAN PREVIEW (NO ACTIONS)")
    now = time.time()
    sims = director._get_instantiated_sims_for_director()
    if not sims:
        lines.append("eligible_sims= (none)")
    for sim in sims:
        sim_info = getattr(sim, "sim_info", None)
        sim_name = director._sim_display_name(sim_info) if sim_info is not None else "Sim"
        sim_id = director._sim_identifier(sim_info) if sim_info is not None else None
        lines.append("")
        lines.append(f"SIM: {sim_name} id={sim_id}")
        lines.extend(_collect_plan_preview(sim, now))
        if sim_info is not None:
            director._get_career_skill_candidates(sim_info)
            career_probe = director.get_last_career_probe()
            if career_probe:
                lines.append("career_requirement_probe:")
                lines.extend(f"- {line}" for line in career_probe[:10])
            lines.append("career_summary:")
            lines.extend(_collect_career_summary(sim_info))
            lines.append("skills_summary:")
            lines.extend(_collect_started_skills(sim_info))
            lines.append("aspiration_summary:")
            lines.extend(_collect_aspiration_summary(sim_info))
    lines.append("")
    active_sim = _get_active_sim(services)
    lines.extend(_collect_active_sim_details(active_sim))
    lines.append("")
    sim_info = _active_sim_info()
    if sim_info is None:
        lines.append("COLLECT: INTERNAL PROBES")
        lines.append("active_sim= (none)")
    else:
        lines.extend(_collect_aspiration_probe_lines(sim_info))
        lines.append("")
        lines.extend(_collect_internal_probes(sim_info))
    return "\n".join(lines)


def _dump_log(output, note):
    log_dump = importlib.import_module("simulation_mode.log_dump")
    ok, result = log_dump.dump_state_to_file(extra_note=note)
    if ok:
        output(f"log_dump_written={result}")
    else:
        output("log_dump_failed")
        output(result.splitlines()[-1] if result else "unknown error")
    return ok, result


def _probe_item_ids(item):
    ids = []
    for key in ("guid64", "tuning_guid", "instance_id"):
        value = _safe_get(item, key)
        if value is not None:
            ids.append(f"{key}={value}")
    name = _safe_get(item, "__name__")
    if name is not None:
        ids.append(f"__name__={name}")
    return ids


def _probe_slot_attrs(slot, attrs):
    lines = []
    for attr in attrs:
        if hasattr(slot, attr):
            value = _safe_get(slot, attr)
            if callable(value) and attr in {"is_locked", "is_empty"}:
                ok, result, error = _safe_call(slot, attr)
                if ok:
                    lines.append(f"  {attr}={result!r}")
                else:
                    lines.append(f"  {attr}=error {error}")
            else:
                lines.append(f"  {attr}={value!r}")
    return lines


def _probe_siminfo_tracker_introspection(sim_info):
    lines = []
    if sim_info is None:
        lines.append("sim_info= (none)")
        return lines
    tokens = ("want", "whim", "fear", "aspiration", "career")
    for name in dir(sim_info):
        lower_name = name.lower()
        if not any(token in lower_name for token in tokens):
            continue
        try:
            value = getattr(sim_info, name)
        except Exception as exc:
            lines.append(f"{name}=error {type(exc).__name__}: {exc}")
            continue
        lines.append(
            f"{name}: type={type(value).__name__} is_none={value is None}"
        )
    return lines


def _probe_active_wants_deep(sim_info):
    director = importlib.import_module("simulation_mode.director")
    lines = []
    wants = director._get_active_wants(sim_info)
    lines.append(f"active_wants_count={len(wants)}")
    for idx, want in enumerate(wants):
        guid64 = director._get_whim_guid64(want)
        tuning = director._resolve_whim_tuning_by_guid64(guid64)
        tuning_name = getattr(tuning, "__name__", None) if tuning is not None else None
        label = tuning_name if tuning_name else director._extract_whim_name(want)
        lines.append(
            f"want[{idx}] guid64={guid64} tuning={tuning_name!r} "
            f"label={label!r} type={type(want).__name__}"
        )
        if director._is_proto_message(want):
            for attr in (
                "whim_guid64",
                "whim_name",
                "whim_tooltip",
                "whim_target_sim",
                "whim_type",
                "slot_whim_type",
                "whim_current_count",
                "whim_goal_count",
                "whim_locked",
            ):
                if hasattr(want, attr):
                    lines.append(f"  {attr}={_safe_get(want, attr)!r}")
    return lines, None, None


def _probe_specific_want_slot(sim_info, index):
    lines = []
    tracker_name, tracker = _select_want_tracker(sim_info)
    if tracker is None:
        lines.append("want_tracker= (not found)")
        return lines
    lines.append(f"want_tracker_attr={tracker_name} type={type(tracker).__name__}")
    slots, source = _iter_probe_container(tracker)
    if not slots:
        lines.append(f"No active wants container found on tracker={type(tracker).__name__}")
        return lines
    lines.append(f"active_wants_source={source} count={len(slots)}")
    if index < 0 or index >= len(slots):
        lines.append(f"probe_want_index_error=index {index} out of range")
        return lines
    slot = slots[index]
    is_empty = None
    is_locked = None
    is_empty_attr = getattr(slot, "is_empty", None)
    if callable(is_empty_attr):
        try:
            is_empty = is_empty_attr()
        except Exception:
            is_empty = None
    else:
        is_empty = is_empty_attr
    is_locked_attr = getattr(slot, "is_locked", None)
    if callable(is_locked_attr):
        try:
            is_locked = is_locked_attr()
        except Exception:
            is_locked = None
    else:
        is_locked = is_locked_attr
    lines.append(
        f"slot[{index}] slot_type={type(slot).__name__} "
        f"is_empty={is_empty!r} is_locked={is_locked!r}"
    )
    want = getattr(slot, "whim", None)
    if want is None:
        want = getattr(slot, "want", None)
    if want is None:
        want = slot
    want_name = (
        getattr(want, "__name__", None)
        or getattr(want, "name", None)
        or str(want)
    )
    lines.append(
        f"want_type={type(want).__name__} want_name={want_name}"
    )
    _log_identifiers(lines, "want.", want)
    goal_attr, goal = _find_first_attr(
        slot,
        ("goal", "_goal", "objective", "_objective", "whim_goal", "_whim_goal"),
    )
    if goal is None:
        goal_attr, goal = _find_first_attr(
            want,
            ("goal", "_goal", "objective", "_objective", "whim_goal", "_whim_goal"),
        )
    if goal is not None:
        lines.append(f"goal_type={type(goal).__name__}")
        _log_identifiers(lines, "goal.", goal)
    filter_tokens = (
        "goal",
        "objective",
        "test",
        "afford",
        "interaction",
        "target",
        "participant",
        "progress",
        "count",
    )
    lines.append(f"probe_want_slot_index={index}")
    for label, obj in (("want", want), ("goal", goal)):
        if obj is None:
            lines.append(f"{label}_details= (none)")
            continue
        lines.append(f"{label}_details_type={type(obj).__name__}")
        for name in dir(obj):
            if not any(token in name.lower() for token in filter_tokens):
                continue
            value = _safe_get(obj, name)
            lines.append(
                f"  {label}.{name} type={type(value).__name__} repr={_trim_repr(value)}"
            )
    if goal_attr:
        lines.append(f"goal_attr_source={goal_attr}")
    return lines


def _probe_wants(output, emit_output=True, emit_dump=True):
    director = importlib.import_module("simulation_mode.director")
    services = importlib.import_module("services")
    sim = _get_active_sim(services)
    sim_info = _active_sim_info()
    lines = [
        "=" * 60,
        "PROBE WANTS",
    ]
    if sim is None and sim_info is None:
        lines.append("active_sim= (none)")
        _append_probe_log(None, lines)
        if emit_output:
            if emit_dump:
                _dump_log(output, "probe_wants")
            output("probe_wants complete; see simulation-mode-probe.log")
        return True
    lines.append(f"active_sim={sim!r}")
    lines.append(f"sim_info={sim_info!r}")

    wants = director.get_active_want_targets(sim_info)
    lines.append(f"wants_count={len(wants)}")
    dir_tokens = (
        "name",
        "goal",
        "objective",
        "afford",
        "interaction",
        "target",
        "test",
        "tuning",
        "guid",
        "instance",
        "whim",
        "want",
        "tracker",
        "resolver",
        "tooltip",
        "display",
    )
    for idx, want in enumerate(wants[:6]):
        lines.append(f"want[{idx}] type={type(want).__name__}")
        lines.append(f"want[{idx}] name={director._extract_whim_name(want)}")
        want_name = getattr(want, "__name__", None)
        if want_name:
            lines.append(f"want[{idx}] __name__={want_name}")
        want_module = getattr(want, "__module__", None)
        if want_module:
            lines.append(f"want[{idx}] __module__={want_module}")
        try:
            hint_names = [
                name
                for name in dir(want)
                if any(token in name.lower() for token in dir_tokens)
            ]
        except Exception:
            hint_names = []
        hint_names = sorted(hint_names)[:60]
        lines.append(f"want[{idx}] dir_hints={', '.join(hint_names)}")
        if getattr(director, "_is_proto_message", None) and director._is_proto_message(want):
            for field in (
                "whim_guid64",
                "whim_target_sim",
                "whim_type",
                "slot_whim_type",
                "whim_current_count",
                "whim_goal_count",
                "whim_locked",
                "whim_name",
                "whim_tooltip",
                "whim_tooltip_reason",
            ):
                try:
                    if hasattr(want, field):
                        lines.append(f"  {field}={_trim_repr(getattr(want, field))}")
                except Exception as exc:
                    lines.append(f"  {field}=error {type(exc).__name__}: {exc}")
        if not (getattr(director, "_is_proto_message", None) and director._is_proto_message(want)):
            for attr in (
                "goal",
                "objective",
                "affordance",
                "super_affordance",
                "tuning",
                "guid",
                "guid64",
                "_goal",
                "_objective",
                "_affordance",
                "_super_affordance",
                "_tuning",
                "_guid",
                "_guid64",
                "_instance_id",
                "_whim",
                "_want",
            ):
                try:
                    value = getattr(want, attr)
                except Exception as exc:
                    lines.append(f"  {attr}=error {type(exc).__name__}: {exc}")
                    continue
                if callable(value):
                    try:
                        value = value()
                    except Exception as exc:
                        lines.append(f"  {attr}=error {type(exc).__name__}: {exc}")
                        continue
                lines.append(f"  {attr}={_trim_repr(value)}")

    _append_probe_log(None, lines)
    if emit_output:
        if emit_dump:
            _dump_log(output, "probe_wants")
        output("probe_wants complete; see simulation-mode-probe.log")
    return True


def _probe_career(output, emit_output=True, emit_dump=True):
    services = importlib.import_module("services")
    sim = _get_active_sim(services)
    sim_info = _active_sim_info()
    lines = [
        "=" * 60,
        "PROBE CAREER",
    ]
    if sim is None and sim_info is None:
        lines.append("active_sim= (none)")
        _append_probe_log(None, lines)
        if emit_output:
            if emit_dump:
                _dump_log(output, "probe_career")
            output("probe_career complete; see simulation-mode-probe.log")
        return True
    lines.append(f"active_sim={sim!r}")
    lines.append(f"sim_info={sim_info!r}")

    tracker = _safe_get(sim_info, "career_tracker")
    if tracker is None:
        lines.append("career_tracker= (not found)")
        _append_probe_log(None, lines)
        if emit_output:
            if emit_dump:
                _dump_log(output, "probe_career")
            output("probe_career complete; see simulation-mode-probe.log")
        return True

    lines.append(f"career_tracker_type={type(tracker)}")
    careers_attr = _safe_get(tracker, "careers")
    if careers_attr is not None:
        if callable(careers_attr):
            ok, result, error = _safe_call(tracker, "careers")
            if ok:
                lines.append(f"careers()={result!r}")
            else:
                lines.append(f"careers()=error {error}")
        else:
            lines.append(f"careers={careers_attr!r}")

    mapping = _safe_get(tracker, "_careers")
    if mapping is not None:
        lines.append(f"_careers_type={type(mapping)}")
        if hasattr(mapping, "values"):
            try:
                for career in mapping.values():
                    lines.append(f"career={career!r}")
                    lines.append(f"career_type={type(career)}")
                    for attr in (
                        "career_level",
                        "level",
                        "current_track",
                        "track",
                        "performance",
                        "uid",
                        "guid64",
                    ):
                        if hasattr(career, attr):
                            lines.append(f"  {attr}={_safe_get(career, attr)!r}")
            except Exception as exc:
                lines.append(f"_careers_error={type(exc).__name__}: {exc}")
        else:
            lines.append(f"_careers_value={mapping!r}")
    else:
        lines.append("_careers= (none)")

    for name in ("currently_at_work", "has_work_career", "has_career"):
        if callable(_safe_get(tracker, name)):
            ok, result, error = _safe_call(tracker, name)
            if ok:
                lines.append(f"{name}()={result!r}")
            else:
                lines.append(f"{name}()=error {error}")

    _append_probe_log(None, lines)
    if emit_output:
        if emit_dump:
            _dump_log(output, "probe_career")
        output("probe_career complete; see simulation-mode-probe.log")
    return True


def _probe_aspiration(output, emit_output=True, emit_dump=True):
    services = importlib.import_module("services")
    sim = _get_active_sim(services)
    sim_info = _active_sim_info()
    lines = [
        "=" * 60,
        "PROBE ASPIRATION",
    ]
    if sim is None and sim_info is None:
        lines.append("active_sim= (none)")
        _append_probe_log(None, lines)
        if emit_output:
            if emit_dump:
                _dump_log(output, "probe_aspiration")
            output("probe_aspiration complete; see simulation-mode-probe.log")
        return True
    lines.append(f"active_sim={sim!r}")
    lines.append(f"sim_info={sim_info!r}")

    tracker = _safe_get(sim_info, "aspiration_tracker")
    if tracker is None:
        lines.append("aspiration_tracker= (not found)")
        _append_probe_log(None, lines)
        if emit_output:
            if emit_dump:
                _dump_log(output, "probe_aspiration")
            output("probe_aspiration complete; see simulation-mode-probe.log")
        return True

    lines.append(f"aspiration_tracker_type={type(tracker)}")
    active = _safe_get(tracker, "_active_aspiration")
    selected = _safe_get(tracker, "_selected_aspiration")
    lines.append(f"_active_aspiration={active!r}")
    lines.append(f"_active_aspiration_type={type(active)}")
    lines.append(f"_selected_aspiration={selected!r}")
    lines.append(f"_selected_aspiration_type={type(selected)}")
    lines.append("ASPIRATION DETAIL")
    lines.extend(_collect_aspiration_summary(sim_info))

    milestone = None
    milestone_source = None
    milestone_list_candidates = []

    if active is not None:
        for name in dir(active):
            try:
                value = getattr(active, name)
            except Exception:
                continue
            if isinstance(value, (list, tuple)) and value:
                first = value[0]
                if hasattr(first, "objectives"):
                    guid = getattr(first, "guid64", None)
                    milestone_list_candidates.append(
                        (name, value, len(value), type(first), guid)
                    )

    for name, value, length, elem_type, guid in milestone_list_candidates:
        guid_text = f" elem0_guid64={guid}" if guid is not None else ""
        lines.append(
            f"milestone_list_candidate=active_asp.{name} len={length} elem0_type={elem_type}{guid_text}"
        )

    if milestone_list_candidates:
        list_name, milestone_list, _length, _elem_type, _guid = milestone_list_candidates[0]
        idx = selected if isinstance(selected, int) else 0
        if not isinstance(idx, int) or idx < 0 or idx >= len(milestone_list):
            idx = 0
        milestone = milestone_list[idx]
        milestone_source = f"active_asp.{list_name}[{idx}]"

    lines.append(f"milestone_source={milestone_source}")
    lines.append(f"milestone_type={type(milestone)}")

    if milestone is not None and callable(_safe_get(tracker, "get_objectives")):
        lines.append(f"get_objectives_milestone_type={type(milestone)}")
        ok, result, error = _safe_call(tracker, "get_objectives", milestone)
        if ok and result is not None:
            lines.append(f"objectives_type={type(result)}")
            try:
                objectives = list(result)
            except Exception:
                objectives = None
            if objectives is not None:
                lines.append(f"objectives_count={len(objectives)}")
                for obj in objectives[:10]:
                    obj_name = (
                        _safe_get(obj, "__name__")
                        or _safe_get(obj, "__qualname__")
                        or _safe_get(type(obj), "__name__")
                        or str(obj)
                    )
                    lines.append(f"objective_name={obj_name}")
        elif not ok:
            lines.append(f"get_objectives()=error {error}")

    try:
        latest_objective_attr = getattr(tracker, "latest_objective", None)
    except Exception as exc:
        lines.append(f"latest_objective=error {type(exc).__name__}: {exc}")
        latest_objective_attr = None
    if latest_objective_attr is not None:
        if callable(latest_objective_attr):
            try:
                result = latest_objective_attr()
                lines.append(f"latest_objective_type={type(result)}")
            except Exception as exc:
                lines.append(f"latest_objective=error {type(exc).__name__}: {exc}")
        else:
            lines.append(f"latest_objective_type={type(latest_objective_attr)}")

    _append_probe_log(None, lines)
    if emit_output:
        if emit_dump:
            _dump_log(output, "probe_aspiration")
        output("probe_aspiration complete; see simulation-mode-probe.log")
    return True


def _probe_all(output):
    services = importlib.import_module("services")
    sim = _get_active_sim(services)
    sim_info = _active_sim_info()
    header = [
        "=" * 60,
        "PROBE ALL",
        f"active_sim={sim!r}",
        f"sim_info={sim_info!r}",
    ]
    _append_probe_log(None, header)
    _append_probe_log(
        "A) SIMINFO TRACKER INTROSPECTION",
        _probe_siminfo_tracker_introspection(sim_info),
    )
    deep_lines, _tracker, _slots = _probe_active_wants_deep(sim_info)
    _append_probe_log("B) ACTIVE WANTS / WHIMS (DEEP DUMP)", deep_lines)
    _append_probe_log("C) CAREER SUMMARY", [])
    _probe_career(output, emit_output=False, emit_dump=False)
    _append_probe_log("D) ASPIRATION SUMMARY", [])
    _probe_aspiration(output, emit_output=False, emit_dump=False)
    _dump_log(output, "probe_all")
    output("probe_all complete; see simulation-mode-probe.log")
    return True


def _probe_want(output, index, emit_dump=True):
    services = importlib.import_module("services")
    _sim = _get_active_sim(services)
    sim_info = _active_sim_info()
    try:
        idx = int(index)
    except Exception:
        output("probe_want requires a numeric index")
        return True
    lines = [
        "=" * 60,
        f"PROBE WANT index={idx}",
    ]
    lines.extend(_probe_specific_want_slot(sim_info, idx))
    _append_probe_log(None, lines)
    if emit_dump:
        _dump_log(output, "probe_want")
    output("probe_want complete; see simulation-mode-probe.log")
    return True


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
        "simulation guardian_now [force]",
        "simulation want_now",
        "simulation collect",
        "simulation skill_plan_now",
        "simulation configpath",
        "simulation dump_log",
        "simulation probe_all",
        "simulation probe_want <index>",
        "simulation probe_wants",
        "simulation probe_career",
        "simulation probe_aspiration",
        "simulation help",
        "keys: auto_unpause, allow_death, allow_pregnancy, tick, guardian_enabled, guardian_check_seconds, "
        "guardian_min_motive, guardian_red_motive, guardian_per_sim_cooldown_seconds, "
        "guardian_max_pushes_per_sim_per_hour, director_enabled, director_check_seconds, "
        "director_min_safe_motive, director_per_sim_cooldown_seconds, "
        "director_green_motive_percent, director_green_min_commodities, "
        "director_allow_social_goals, director_allow_social_wants, director_use_guardian_when_low, "
        "director_enable_wants, director_max_pushes_per_sim_per_hour, director_prefer_career_skills, "
        "director_fallback_to_started_skills, director_skill_allow_list, "
        "director_skill_block_list, collect_log_filename, "
        "integrate_better_autonomy_trait, better_autonomy_trait_id",
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
               "director_allow_social_goals", "director_allow_social_wants",
               "director_enable_wants",
               "director_use_guardian_when_low",
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
                output("Simulation daemon started successfully (build 47).")
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
        output(f"director_allow_social_wants={settings.director_allow_social_wants}")
        output(f"director_enable_wants={settings.director_enable_wants}")
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
        sim = _get_active_sim(services)
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

    if action_key == "guardian_now":
        guardian = importlib.import_module("simulation_mode.guardian")
        sim_info = _active_sim_info()
        if sim_info is None:
            output("No active sim found.")
            return True
        now = time.time()
        force = bool(key and key.strip().lower() == "force")
        ok, message = guardian.push_self_care(
            sim_info, now, settings.director_green_motive_percent, bypass_cooldown=force
        )
        lines = [
            "=" * 60,
            f"GUARDIAN NOW force={force}",
            f"pushed={ok}",
            f"detail={message}",
        ]
        _append_simulation_log(lines)
        output(f"guardian_now force={force} pushed={ok}")
        output(message)
        return True

    if action_key == "want_now":
        director = importlib.import_module("simulation_mode.director")
        probe_log = importlib.import_module("simulation_mode.probe_log")
        sim_info = _active_sim_info()
        if sim_info is None:
            output("No active sim found.")
            return True
        sim_name = director._sim_display_name(sim_info)
        sim_id = director._sim_identifier(sim_info)
        probe_log.log_probe("=" * 60)
        probe_log.log_probe(f"WANT_NOW start sim={sim_name} sim_id={sim_id}")
        ok, message = director._try_resolve_wants(sim_info, force=True)
        probe_log.log_probe(f"WANT_NOW end result={'SUCCESS' if ok else 'FAIL'}")
        output(
            "want_now result={} probe_log={}".format(
                "SUCCESS" if ok else "FAIL", probe_log.get_probe_log_path()
            )
        )
        return True

    if action_key == "collect":
        logging_utils = importlib.import_module("simulation_mode.logging_utils")
        _reload_settings(_connection, output)
        payload = _build_collect_payload()
        path = logging_utils.append_log_block(
            settings.collect_log_filename, "SimulationMode COLLECT", payload
        )
        output(f"collect_written={path}")
        return True

    if action_key == "skill_plan_now":
        director = importlib.import_module("simulation_mode.director")
        guardian = importlib.import_module("simulation_mode.guardian")
        logging_utils = importlib.import_module("simulation_mode.logging_utils")
        services = importlib.import_module("services")
        sim = _get_active_sim(services)
        sim_info = getattr(sim, "sim_info", None) if sim is not None else None
        if sim is None or sim_info is None:
            output("No active sim found.")
            return True
        now = time.time()
        sim_name = director._sim_display_name(sim_info)
        snapshot = director.get_motive_snapshot_for_sim(sim_info)
        min_motive = director._safe_min_motive(snapshot) if snapshot else None
        motive_unsafe = (
            min_motive is not None and min_motive < settings.director_min_safe_motive
        )
        result_lines = [
            f"skill_plan_now sim={sim_name} sim_id={director._sim_identifier(sim_info)}",
            f"min_motive={min_motive} min_safe_threshold={settings.director_min_safe_motive}",
            f"motive_unsafe={motive_unsafe}",
        ]
        success = False
        if motive_unsafe and settings.director_use_guardian_when_low:
            ok, detail = guardian.push_self_care(
                sim_info,
                now,
                settings.director_green_motive_percent,
                bypass_cooldown=True,
            )
            result_lines.append(f"guardian_invoked={ok} detail={detail}")
            result_lines.append(
                f"skill_plan_now: motive_unsafe=True -> guardian(force=True) result={detail}"
            )
            success = ok
        else:
            result = director.run_skill_plan(
                sim_info, sim, now, force=True, source="skill_plan_now"
            )
            result_lines.append(f"wants_reason={result.get('wants_reason')}")
            result_lines.append(f"career_reason={result.get('career_reason')}")
            started_candidates = result.get("started_candidates") or []
            result_lines.append(
                f"started_candidates_count={len(started_candidates)}"
            )
            if started_candidates:
                result_lines.append("started_skills_order_top5:")
                for skill_key, _reason, level in started_candidates[:5]:
                    result_lines.append(f"- {skill_key} level={level}")
            if result.get("success"):
                skill_key = result.get("skill_key")
                reason = result.get("skill_reason")
                skill_source = result.get("skill_source")
                result_lines.append("skill_plan_result=SUCCESS")
                result_lines.append(f"skill_key={skill_key}")
                result_lines.append(f"skill_source={skill_source}")
                result_lines.append(f"skill_reason={reason}")
                director._record_push(director._sim_identifier(sim_info), now)
                director._record_action(sim_info, skill_key, reason, now)
                success = True
            else:
                result_lines.append("skill_plan_result=FAIL")
                result_lines.append(
                    f"attempted_started={result.get('attempted', 0)}"
                )
                failure_counts = result.get("failure_counts", {})
                result_lines.append(
                    "failure_counts no_object={} no_affordance={} push_failed={}".format(
                        failure_counts.get("no_object", 0),
                        failure_counts.get("no_affordance", 0),
                        failure_counts.get("push_failed", 0),
                    )
                )
        payload = "\n".join(result_lines)
        path = logging_utils.append_log_block(
            settings.collect_log_filename, "SimulationMode SKILL_PLAN_NOW", payload
        )
        output(
            f"skill_plan_now_written={path} result={'success' if success else 'failure'}"
        )
        return True

    if action_key == "director_push":
        director = importlib.import_module("simulation_mode.director")
        services = importlib.import_module("services")
        skill_key = key.strip().lower() if key else None
        if not skill_key:
            output("Missing skill_key")
            return True
        sim = _get_active_sim(services)
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

    if action_key == "probe_wants":
        return _probe_wants(output)

    if action_key == "probe_want":
        return _probe_want(output, key)

    if action_key == "probe_career":
        return _probe_career(output)

    if action_key == "probe_aspiration":
        return _probe_aspiration(output)

    if action_key == "probe_all":
        return _probe_all(output)

    if action_key == "dump_log":
        dumper = importlib.import_module("simulation_mode.log_dump")
        ok, result = dumper.dump_state_to_file()
        if ok:
            output(f"log_dump_written={result}")
        else:
            output("log_dump_failed")
            output(result.splitlines()[-1] if result else "unknown error")
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
