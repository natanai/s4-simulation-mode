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
        f"director_allow_social_wants={settings.director_allow_social_wants}",
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
    for token in ("want",):
        for name in dir(sim_info):
            if token not in name.lower():
                continue
            value = _safe_get(sim_info, name)
            if value is not None:
                return name, value
    for token in ("whim",):
        for name in dir(sim_info):
            if token not in name.lower():
                continue
            value = _safe_get(sim_info, name)
            if value is not None:
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


def _append_simulation_log(lines):
    log_dump = importlib.import_module("simulation_mode.log_dump")
    path = log_dump.get_log_path()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(f"[{timestamp}] {line}" for line in lines))
        handle.write("\n")


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
    lines = []
    tracker_name, tracker = _select_want_tracker(sim_info)
    if tracker is None:
        lines.append("want_tracker= (not found)")
        return lines, None, None
    lines.append(
        f"want_tracker_attr={tracker_name} type={type(tracker).__name__}"
    )
    slots, source = _iter_probe_container(tracker)
    if not slots:
        lines.append(f"No active wants container found on tracker={type(tracker).__name__}")
        return lines, tracker, None
    lines.append(f"active_wants_source={source} count={len(slots)}")
    for idx, slot in enumerate(slots):
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
            f"slot[{idx}] slot_type={type(slot).__name__} "
            f"is_empty={is_empty!r} is_locked={is_locked!r} "
            f"want_type={type(want).__name__} want_name={want_name}"
        )
        _log_identifiers(lines, "  want.", want)

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
            lines.append(
                f"  goal_source={goal_attr} goal_type={type(goal).__name__}"
            )
            _log_identifiers(lines, "  goal.", goal)
            for attr in (
                "completed",
                "progress",
                "_count",
                "_required_count",
                "count",
                "required_count",
            ):
                if hasattr(goal, attr):
                    lines.append(f"  goal.{attr}={_safe_get(goal, attr)!r}")

        aff_attr, affordance = _find_first_attr(
            slot,
            (
                "affordance",
                "super_affordance",
                "interaction",
                "_interaction",
                "interaction_to_push",
                "_super_affordance",
                "_affordance",
            ),
        )
        if affordance is None:
            aff_attr, affordance = _find_first_attr(
                want,
                (
                    "affordance",
                    "super_affordance",
                    "interaction",
                    "_interaction",
                    "interaction_to_push",
                    "_super_affordance",
                    "_affordance",
                ),
            )
        if affordance is None and goal is not None:
            aff_attr, affordance = _find_first_attr(
                goal,
                (
                    "affordance",
                    "super_affordance",
                    "interaction",
                    "_interaction",
                    "interaction_to_push",
                    "_super_affordance",
                    "_affordance",
                ),
            )
        if affordance is not None:
            aff_name = getattr(affordance, "__name__", None) or str(affordance)
            lines.append(
                f"  affordance_source={aff_attr} aff_type={type(affordance).__name__} "
                f"aff_name={aff_name}"
            )
            _log_identifiers(lines, "  aff.", affordance)

        tests_attr, tests = _find_first_attr(
            goal,
            ("tests", "_tests", "test_set", "_test_set", "goal_tests", "_goal_tests"),
        )
        if tests is None:
            tests_attr, tests = _find_first_attr(
                want,
                ("tests", "_tests", "test_set", "_test_set", "goal_tests", "_goal_tests"),
            )
        if tests is not None:
            lines.append(f"  tests_source={tests_attr} tests_type={type(tests).__name__}")
            if hasattr(tests, "__iter__") and not isinstance(tests, (str, bytes)):
                try:
                    for idx_test, item in enumerate(list(tests)[:5]):
                        lines.append(
                            f"    tests[{idx_test}] type={type(item).__name__} "
                            f"repr={_trim_repr(item)}"
                        )
                except Exception:
                    lines.append(f"    tests_repr={_trim_repr(tests)}")
            else:
                lines.append(f"    tests_repr={_trim_repr(tests)}")

        for attr in (
            "target_type",
            "participant_type",
            "participants",
            "resolver",
            "_target",
            "_target_sim",
            "target",
            "sim_filter",
        ):
            if goal is not None and hasattr(goal, attr):
                lines.append(
                    f"  goal.{attr} type={type(_safe_get(goal, attr)).__name__} "
                    f"repr={_trim_repr(_safe_get(goal, attr))}"
                )
            if hasattr(want, attr):
                lines.append(
                    f"  want.{attr} type={type(_safe_get(want, attr)).__name__} "
                    f"repr={_trim_repr(_safe_get(want, attr))}"
                )
    return lines, tracker, slots


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
    for idx, want in enumerate(wants[:6]):
        lines.append(f"want[{idx}] type={type(want).__name__}")
        lines.append(f"want[{idx}] name={director._extract_whim_name(want)}")
        for attr in ("goal", "objective", "affordance", "super_affordance", "tuning", "guid", "guid64"):
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
               "director_allow_social_goals", "director_allow_social_wants",
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
        output(f"director_allow_social_wants={settings.director_allow_social_wants}")
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
        sim_info = _active_sim_info()
        if sim_info is None:
            output("No active sim found.")
            return True
        ok, message = director._try_resolve_wants(sim_info, force=True)
        output(f"want_now pushed={ok} detail={message}")
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
