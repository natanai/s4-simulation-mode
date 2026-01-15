import json
import os
import time

from simulation_mode.settings import get_config_path


_DEFAULT_FILENAME = "simulation-mode-object-catalog.jsonl"


def get_catalog_log_path(filename: str = None) -> str:
    try:
        cfg = os.path.abspath(get_config_path())
        folder = os.path.dirname(cfg)
        name = filename or _DEFAULT_FILENAME
        return os.path.join(folder, name)
    except Exception:
        return ""


def write_catalog_records(records, path: str) -> None:
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False))
                handle.write("\n")
    except Exception:
        return


def _trim_repr(value, limit=180):
    try:
        text = repr(value)
    except Exception as exc:
        text = f"<repr failed: {exc}>"
    if text is None:
        return ""
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def _safe_get(obj, name, default=None):
    try:
        return getattr(obj, name)
    except Exception:
        return default


def _safe_bool(obj, name, default=False):
    value = _safe_get(obj, name, None)
    if value is None:
        return default
    try:
        return bool(value() if callable(value) else value)
    except Exception:
        return default


def _aff_name(aff):
    try:
        return (
            _safe_get(aff, "__name__")
            or _safe_get(aff, "__qualname__")
            or str(aff)
        )
    except Exception:
        return "<aff?>"


def _is_picker_like(aff):
    name = _safe_get(aff, "__name__") or ""
    if isinstance(name, str) and "picker" in name.lower():
        return True
    class_name = _safe_get(_safe_get(aff, "__class__"), "__name__") or ""
    return "Picker" in class_name


def _is_staging_like(aff):
    name = _safe_get(aff, "__name__") or ""
    return isinstance(name, str) and "staging" in name.lower()


def _has_tests(aff):
    return _safe_get(aff, "tests") is not None or _safe_get(aff, "test_globals") is not None


def _advertisement_hint(aff):
    for attr in ("false_advertisements", "commodity_advertisements", "advertisements"):
        if hasattr(aff, attr):
            value = _safe_get(aff, attr)
            if value is not None:
                return _trim_repr(value, limit=180)
    return None


def _obj_name(obj):
    try:
        definition = _safe_get(obj, "definition")
        if definition is not None:
            name = _safe_get(definition, "name")
            if name:
                return str(name)
            def_id = _safe_get(definition, "id") or _safe_get(definition, "guid64")
            if def_id:
                class_name = _safe_get(_safe_get(obj, "__class__"), "__name__")
                if class_name:
                    return f"{class_name}(def={def_id})"
    except Exception:
        pass
    class_name = _safe_get(_safe_get(obj, "__class__"), "__name__")
    if class_name:
        return str(class_name)
    try:
        name = str(obj)
        if name:
            return name
    except Exception:
        pass
    return "<obj?>"


def _obj_def_id(obj):
    definition = _safe_get(obj, "definition")
    if definition is None:
        return None
    for attr in ("id", "guid64", "tuning_id", "instance_id"):
        value = _safe_get(definition, attr)
        if value is not None:
            return value
    return None


def _zone_id():
    try:
        services = __import__("services")
        zone = getattr(services, "current_zone", None)
        if callable(zone):
            zone = zone()
        if zone is None:
            return None
        return _safe_get(zone, "id") or _safe_get(zone, "zone_id")
    except Exception:
        return None


def scan_zone_catalog(
    sim_info,
    *,
    max_objects=2000,
    max_affordances_per_object=80,
    include_non_autonomous=False,
):
    push_utils = __import__("simulation_mode.push_utils", fromlist=["iter_objects"])
    notes = []
    records = []
    scanned_objects = 0
    scanned_affordances = 0
    truncated = False
    zone_id = _zone_id()

    try:
        for obj in push_utils.iter_objects():
            scanned_objects += 1
            if scanned_objects > max_objects:
                truncated = True
                notes.append("max_objects cap reached")
                break

            try:
                getter = _safe_get(obj, "get_super_affordances")
                if callable(getter):
                    affordances = getter()
                else:
                    affordances = None
            except Exception as exc:
                notes.append(f"get_super_affordances error obj_id={_safe_get(obj, 'id')} err={exc}")
                continue

            if not affordances:
                continue

            try:
                affordance_list = list(affordances)
            except Exception:
                affordance_list = []

            if len(affordance_list) > max_affordances_per_object:
                truncated = True
                notes.append(
                    f"affordance cap applied obj_id={_safe_get(obj, 'id')} count={len(affordance_list)}"
                )

            for aff in affordance_list[:max_affordances_per_object]:
                scanned_affordances += 1
                allow_user_directed = _safe_bool(aff, "allow_user_directed", default=False)
                allow_autonomous = _safe_bool(aff, "allow_autonomous", default=False)
                is_cheat = _safe_bool(aff, "cheat", default=False)
                is_debug = _safe_bool(aff, "debug", default=False)
                is_picker_like = _is_picker_like(aff)
                is_staging_like = _is_staging_like(aff)

                if is_cheat or is_debug or is_picker_like or is_staging_like:
                    continue
                if not include_non_autonomous and not (allow_user_directed or allow_autonomous):
                    continue

                record = {
                    "ts": time.time(),
                    "zone_id": zone_id,
                    "obj_id": _safe_get(obj, "id"),
                    "obj_name": _obj_name(obj),
                    "obj_def_id": _obj_def_id(obj),
                    "aff_name": _aff_name(aff),
                    "aff_guid64": _safe_get(aff, "guid64"),
                    "allow_user_directed": allow_user_directed,
                    "allow_autonomous": allow_autonomous,
                    "is_picker_like": is_picker_like,
                    "is_staging_like": is_staging_like,
                    "is_cheat": is_cheat,
                    "is_debug": is_debug,
                    "has_tests": _has_tests(aff),
                    "advertisement_hint": _advertisement_hint(aff),
                }
                records.append(record)
    except Exception as exc:
        notes.append(f"scan error: {exc}")
        return {
            "ok": False,
            "path": "",
            "scanned_objects": scanned_objects,
            "scanned_affordances": scanned_affordances,
            "written_records": 0,
            "truncated": truncated,
            "notes": notes,
        }

    path = get_catalog_log_path()
    write_catalog_records(records, path)

    return {
        "ok": True,
        "path": path,
        "scanned_objects": scanned_objects,
        "scanned_affordances": scanned_affordances,
        "written_records": len(records),
        "truncated": truncated,
        "notes": notes,
    }
