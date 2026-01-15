import inspect

import services

from simulation_mode import push_utils


_PICKER_HINTS = ("picker", "dialog", "choose", "selection", "pie_menu")


def _trim_repr(value, limit=200):
    try:
        text = repr(value)
    except Exception:
        text = "<repr-error>"
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _get_object_label(obj):
    if obj is None:
        return "none"
    definition = getattr(obj, "definition", None)
    definition_name = getattr(definition, "name", None) if definition is not None else None
    object_id = getattr(obj, "id", None) or getattr(obj, "object_id", None)
    pieces = []
    if definition_name:
        pieces.append(str(definition_name))
    if object_id is not None:
        pieces.append(f"id={object_id}")
    return " ".join(pieces) or str(obj)


def _object_sort_key(obj):
    obj_id = getattr(obj, "id", None) or getattr(obj, "object_id", None)
    if obj_id is not None:
        return (0, obj_id)
    return (1, str(obj))


def _is_on_active_lot(obj):
    attr = getattr(obj, "is_on_active_lot", None)
    if callable(attr):
        try:
            return bool(attr())
        except Exception:
            return True
    if attr is not None:
        return bool(attr)
    return True


def _get_super_affordances(obj):
    candidates = []
    getter = getattr(obj, "get_super_affordances", None)
    if callable(getter):
        try:
            candidates.extend(getter())
        except Exception:
            pass
    affordances = getattr(obj, "super_affordances", None)
    if affordances is not None:
        try:
            candidates.extend(affordances() if callable(affordances) else affordances)
        except Exception:
            pass
    affordances = getattr(obj, "_super_affordances", None)
    if affordances is not None:
        try:
            candidates.extend(affordances() if callable(affordances) else affordances)
        except Exception:
            pass
    return list(dict.fromkeys(candidates))


def _make_probe_context(sim):
    context, _client_attached = push_utils.make_interaction_context(sim, force=True)
    return context


def _is_likely_picker(aff):
    class_name = getattr(aff, "__class__", type(aff)).__name__
    module = getattr(aff, "__module__", "") or ""
    attrs = []
    try:
        attrs = list(dir(aff))
    except Exception:
        attrs = []
    pool = " ".join([class_name, module, " ".join(attrs)]).lower()
    if any(token in pool for token in _PICKER_HINTS):
        return True
    for attr in attrs:
        lower_attr = attr.lower()
        if "picker" in lower_attr or "dialog" in lower_attr:
            return True
    return False


def probe_affordances(sim, out_lines):
    out_lines.append("AFFORDANCE PROBE:")
    if sim is None:
        out_lines.append("active_sim= (none)")
        return
    object_manager = services.object_manager()
    objects = []
    for obj in push_utils._iter_objects_from_manager(object_manager):
        try:
            if getattr(obj, "client_attached", None) is not True:
                continue
            if not _is_on_active_lot(obj):
                continue
        except Exception:
            continue
        objects.append(obj)
    objects.sort(key=_object_sort_key)
    objects = objects[:60]
    total_objects_scanned = len(objects)
    total_objects_with_any_pass = 0
    total_pass_affordances = 0
    total_pass_picker_likely = 0
    context = _make_probe_context(sim)
    for obj in objects:
        affordances = _get_super_affordances(obj)
        if not affordances:
            continue
        passing = []
        for aff in affordances:
            try:
                passes, _reason = push_utils.evaluate_affordance(sim, obj, aff, context)
            except Exception:
                passes = False
            if not passes:
                continue
            total_pass_affordances += 1
            picker_likely = _is_likely_picker(aff)
            if picker_likely:
                total_pass_picker_likely += 1
            if len(passing) < 12:
                passing.append((aff, picker_likely))
        if not passing:
            continue
        total_objects_with_any_pass += 1
        out_lines.append(f"object={_get_object_label(obj)}")
        for aff, picker_likely in passing:
            aff_name = getattr(aff, "__name__", None) or aff.__class__.__name__
            aff_guid = (
                getattr(aff, "guid64", None)
                or getattr(aff, "guid", None)
                or "None"
            )
            aff_module = getattr(aff, "__module__", "None")
            allow_autonomous = getattr(aff, "allow_autonomous", None)
            allow_user_directed = getattr(aff, "allow_user_directed", None)
            is_cheat = getattr(aff, "cheat", None)
            is_debug = getattr(aff, "debug", None)
            out_lines.append(
                "  - PASS aff={} guid={} module={} auto={} user={} picker={} cheat={} debug={}".format(
                    aff_name,
                    aff_guid,
                    aff_module,
                    allow_autonomous,
                    allow_user_directed,
                    picker_likely,
                    is_cheat,
                    is_debug,
                )
            )
    out_lines.append(
        "affordance_probe_summary objects_scanned={} objects_with_pass={} "
        "pass_affordances={} pass_picker_likely={}".format(
            total_objects_scanned,
            total_objects_with_any_pass,
            total_pass_affordances,
            total_pass_picker_likely,
        )
    )


def _log_attr_snapshot(out_lines, label, value):
    out_lines.append(
        "{} type={} value={}".format(label, type(value).__name__, _trim_repr(value))
    )


def _iter_safe_getters(tracker):
    tokens = ("aspiration", "milestone", "objective", "goal")
    for name in dir(tracker):
        lower = name.lower()
        if not lower.startswith("get_"):
            continue
        if not any(token in lower for token in tokens):
            continue
        method = getattr(tracker, name, None)
        if not callable(method):
            continue
        try:
            sig = inspect.signature(method)
        except Exception:
            continue
        required = [
            p
            for p in sig.parameters.values()
            if p.default is inspect._empty
            and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        if len(required) > 1:
            continue
        yield name, method


def probe_aspirations(sim_info, out_lines):
    out_lines.append("ASPIRATION PROBE:")
    tracker = getattr(sim_info, "aspiration_tracker", None)
    if tracker is None:
        out_lines.append("aspiration_tracker=None")
        return
    active_aspiration = getattr(tracker, "_active_aspiration", None)
    if active_aspiration is None:
        active_aspiration = getattr(tracker, "active_aspiration", None)
    _log_attr_snapshot(out_lines, "active_aspiration", active_aspiration)
    _log_attr_snapshot(
        out_lines, "active_milestone", getattr(tracker, "_active_milestone", None)
    )
    _log_attr_snapshot(
        out_lines, "current_milestone", getattr(tracker, "_current_milestone", None)
    )
    _log_attr_snapshot(
        out_lines, "completed_objectives", getattr(tracker, "_completed_objectives", None)
    )
    _log_attr_snapshot(
        out_lines, "completed_milestones", getattr(tracker, "_completed_milestones", None)
    )
    for name, method in _iter_safe_getters(tracker):
        try:
            result = method()
        except Exception as exc:
            out_lines.append(f"{name} error={type(exc).__name__}: {exc}")
            continue
        out_lines.append(
            "{} return_type={} value={}".format(
                name, type(result).__name__, _trim_repr(result)
            )
        )
