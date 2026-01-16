import inspect
import json
import os
import time

import services
import sims4.resources

import simulation_mode.settings as sm_settings
from simulation_mode.settings import get_config_path


_DEFAULT_FILENAME = "simulation-mode-object-catalog.jsonl"
META_RESERVE_BYTES = 4096


def get_catalog_log_path(filename: str = None) -> str:
    try:
        cfg = os.path.abspath(get_config_path())
        folder = os.path.dirname(cfg)
        name = filename or _DEFAULT_FILENAME
        return os.path.join(folder, name)
    except Exception:
        return ""


def write_catalog_records(records, path: str, mode: str = "a"):
    if not path:
        return False, "missing path"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception as exc:
        return False, repr(exc)
    try:
        with open(path, mode, encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False))
                handle.write("\n")
    except Exception as exc:
        return False, repr(exc)
    return True, ""


def _normalize_limit(value):
    if value is None:
        return None
    try:
        numeric = int(value)
    except Exception:
        return None
    if numeric <= 0:
        return None
    return numeric


def _build_padded_meta_line(meta):
    line = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    raw = line.encode("utf-8")
    limit = META_RESERVE_BYTES - 1
    if len(raw) > limit:
        notes = list(meta.get("notes") or [])
        while notes and len(raw) > limit:
            notes.pop()
            meta["notes"] = notes
            line = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
            raw = line.encode("utf-8")
        if len(raw) > limit:
            meta["notes"] = []
            line = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
            raw = line.encode("utf-8")
    if len(raw) > limit:
        raise ValueError("meta line exceeds reserved bytes")
    padded = raw + (b" " * (limit - len(raw)))
    return padded + b"\n"


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


def _bool_attr_or_call(value):
    if callable(value):
        try:
            return bool(value())
        except Exception:
            return False
    return bool(value)


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


def _tuning_guid64(value):
    if value is None:
        return None
    try:
        guid = getattr(value, "guid64", None)
    except Exception:
        return None
    if guid is None or isinstance(guid, bool):
        return None
    try:
        guid_int = int(guid)
    except Exception:
        return None
    return guid_int


def _extract_guid64s_from_mapping_keys(mapping):
    if not mapping:
        return []
    try:
        keys = list(mapping.keys())
    except Exception:
        return []
    out = []
    for key in keys:
        guid = _tuning_guid64(key)
        if guid is not None:
            out.append(guid)
    return sorted(set(out))


def _safe_signature_str(callable_obj):
    if callable_obj is None:
        return None
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return None
    return str(signature)


def _safe_call_with_sim_guess(fn, sim):
    if fn is None:
        return False, None, "missing callable"
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        signature = None
    required = 0
    positional = 0
    has_varargs = False
    if signature is not None:
        for param in signature.parameters.values():
            if param.kind == param.VAR_POSITIONAL:
                has_varargs = True
                continue
            if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD):
                positional += 1
                if param.default is param.empty:
                    required += 1
    candidates = [(), (sim,), (sim, None)]
    for args in candidates:
        if signature is not None and not has_varargs:
            if len(args) > positional:
                continue
            if len(args) < required:
                continue
        try:
            return True, fn(*args), None
        except Exception as exc:
            last_exc = exc
            continue
    err = None
    if "last_exc" in locals():
        err = repr(last_exc)
    return False, None, err


def _walk_for_guid64s(value, max_nodes=200, max_depth=4):
    seen = set()
    found = set()
    stack = [(value, 0)]
    nodes = 0

    while stack and nodes < max_nodes:
        current, depth = stack.pop()
        if current is None:
            continue
        obj_id = id(current)
        if obj_id in seen:
            continue
        seen.add(obj_id)
        nodes += 1

        guid = _tuning_guid64(current)
        if guid is not None:
            found.add(guid)

        if depth >= max_depth:
            continue

        if isinstance(current, dict):
            for key, val in current.items():
                stack.append((key, depth + 1))
                stack.append((val, depth + 1))
            continue
        if isinstance(current, (list, tuple, set)):
            for item in current:
                stack.append((item, depth + 1))
            continue

        for attr in (
            "skill",
            "skills",
            "skill_type",
            "statistic",
            "statistics",
            "commodity",
            "commodities",
        ):
            if hasattr(current, attr):
                try:
                    stack.append((getattr(current, attr), depth + 1))
                except Exception:
                    continue

    return found


def _extract_skill_gain_guid_candidates(affordance):
    """
    Returns (gain_guid_candidates_set, evidence_tokens_list)
    Only examines tuning areas that plausibly APPLY skill gain:
      - skill_loot_data / _skill_loot_data / get_skill_loot_data()
      - loot / loots / loot_actions / outcome_actions / basic_extras
      - commodity/statistic changes if present on the affordance
    Must be fully safe: never throw.
    """
    gain = set()
    evidence = []

    def _record_token(token):
        if token and token not in evidence:
            evidence.append(token)

    def _add_guid(guid, token=None):
        if guid is None:
            return
        try:
            gain.add(int(guid))
        except Exception:
            return
        if token:
            _record_token(token)

    def _delta_items(value):
        if value is None:
            return []
        if isinstance(value, dict):
            try:
                return list(value.items())
            except Exception:
                return []
        try:
            return list(value)
        except Exception:
            return []

    for attr in (
        "commodity_changes",
        "_commodity_changes",
        "statistic_changes",
        "_statistic_changes",
    ):
        value = _safe_get(affordance, attr)
        items = _delta_items(value)
        if not items:
            continue
        any_added = False
        for item in items:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            stat, delta = item[0], item[1]
            try:
                delta_value = float(delta)
            except Exception:
                continue
            if delta_value <= 0:
                continue
            guid = _tuning_guid64(stat)
            if guid is None:
                continue
            _add_guid(guid)
            any_added = True
        if any_added:
            _record_token("delta_change")

    for attr in ("skill_loot_data", "_skill_loot_data"):
        value = _safe_get(affordance, attr)
        if value is None:
            continue
        before = len(gain)
        gain.update(_walk_for_guid64s(value))
        if len(gain) > before:
            _record_token("skill_loot_data")

    getter = _safe_get(affordance, "get_skill_loot_data")
    if callable(getter):
        ok_call, loot_result, _err = _safe_call_with_sim_guess(getter, None)
        if ok_call and loot_result is not None:
            before = len(gain)
            gain.update(_walk_for_guid64s(loot_result))
            if len(gain) > before:
                _record_token("skill_loot_data")

    for attr in (
        "loot",
        "loots",
        "loot_actions",
        "outcome_actions",
        "basic_extras",
        "_basic_extras",
    ):
        value = _safe_get(affordance, attr)
        if value is None:
            continue
        before = len(gain)
        gain.update(_walk_for_guid64s(value))
        if len(gain) > before:
            _record_token("loot_list")

    return gain, evidence


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


def _is_sim_object(obj):
    if obj is None:
        return False
    if _safe_bool(obj, "is_sim", default=False):
        return True
    sim_info = _safe_get(obj, "sim_info")
    if sim_info is not None:
        return True
    class_name = _safe_get(_safe_get(obj, "__class__"), "__name__") or ""
    return class_name.lower() == "sim"


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


def _resolve_object(obj):
    if obj is None:
        return None
    if any(
        hasattr(obj, attr)
        for attr in (
            "super_affordances",
            "_super_affordances",
            "get_super_affordances",
            "get_target_super_affordances",
        )
    ):
        return obj
    services = __import__("services")
    try:
        oid = int(obj)
    except Exception:
        oid = None
    if oid:
        try:
            om = services.object_manager()
            if om is not None:
                return om.get(oid)
        except Exception:
            return None
    for key in ("id", "object_id"):
        if hasattr(obj, key):
            try:
                oid2 = int(getattr(obj, key))
                om = services.object_manager()
                if om is not None:
                    return om.get(oid2)
            except Exception:
                pass
    return None


def scan_zone_catalog(
    sim_info,
    *,
    max_objects=None,
    max_affordances_per_object=None,
    include_sims=None,
    include_non_autonomous=None,
    filename: str = None,
):
    import simulation_mode.push_utils as push_utils

    notes = []
    try:
        if include_sims is None:
            include_sims = sm_settings.get_bool("catalog_include_sims", False)
        if include_non_autonomous is None:
            include_non_autonomous = sm_settings.get_bool(
                "catalog_include_non_autonomous", False
            )
        if max_objects is None:
            max_objects = sm_settings.get_int("catalog_max_objects", 0)
        if max_affordances_per_object is None:
            max_affordances_per_object = sm_settings.get_int(
                "catalog_max_affordances_per_object", 0
            )
        max_records = sm_settings.get_int("catalog_max_records", 0)
    except Exception as exc:
        include_sims = False if include_sims is None else include_sims
        include_non_autonomous = (
            False if include_non_autonomous is None else include_non_autonomous
        )
        max_objects = 0 if max_objects is None else max_objects
        max_affordances_per_object = (
            0 if max_affordances_per_object is None else max_affordances_per_object
        )
        max_records = 0
        notes.append(f"settings_read_error err={exc!r}")
    max_objects = _normalize_limit(max_objects)
    max_affordances_per_object = _normalize_limit(max_affordances_per_object)
    max_records = _normalize_limit(max_records)
    if max_records is None and max_objects is None and max_affordances_per_object is None:
        notes.append("unlimited caps enabled")
    sample = []
    scanned_objects = 0
    unresolved_objects = 0
    scanned_affordances = 0
    record_lines_written = 0
    records_with_loot_ref_guids = 0
    records_with_skill_guids = 0
    skill_guids_total = 0
    truncated = False
    skipped_sims = 0
    filtered_flags = 0
    filtered_non_autonomy = 0
    zone_id = _zone_id()
    ok = True
    write_ok = False
    write_error = ""
    handle = None
    record_limit_hit = False
    write_failed = False
    path = get_catalog_log_path(filename)

    skill_guid_filter_no_instance_managers = False
    skill_guids_all_filtered_out = False

    def _filter_skill_guids(guids):
        nonlocal skill_guid_filter_no_instance_managers
        nonlocal skill_guids_all_filtered_out
        if not guids:
            return []
        managers = []
        try:
            Types = sims4.resources.Types
            if hasattr(Types, "SKILL"):
                manager = services.get_instance_manager(Types.SKILL)
                if manager is not None:
                    managers.append(("Types.SKILL", manager))
            stat_mgr = services.get_instance_manager(Types.STATISTIC)
            if stat_mgr is not None:
                managers.append(("Types.STATISTIC", stat_mgr))
        except Exception:
            managers = []
        if not managers:
            skill_guid_filter_no_instance_managers = True
            if guids:
                skill_guids_all_filtered_out = True
            return []

        def _tuning_is_skill(tuning):
            if tuning is None:
                return False
            attr = _safe_get(tuning, "is_skill")
            if attr is not None:
                try:
                    if bool(attr() if callable(attr) else attr):
                        return True
                except Exception:
                    pass
            module = (
                _safe_get(tuning, "__module__")
                or _safe_get(_safe_get(tuning, "__class__"), "__module__")
                or ""
            ).lower()
            name = (
                _safe_get(tuning, "__name__")
                or _safe_get(_safe_get(tuning, "__class__"), "__name__")
                or ""
            ).lower()
            if ("skill" in module or "skill" in name) and not (
                "commodity" in module or "commodity" in name
            ):
                return True
            for attr_name in ("skill_value", "get_skill_value", "max_level", "get_max_level"):
                if hasattr(tuning, attr_name):
                    return True
            return False

        filtered = []
        for guid in guids:
            if guid is None:
                continue
            tuning = None
            for _source_name, manager in managers:
                try:
                    tuning = manager.get(guid)
                except Exception:
                    tuning = None
                if tuning is not None:
                    break
            if tuning is None:
                continue
            if _tuning_is_skill(tuning):
                try:
                    filtered.append(int(guid))
                except Exception:
                    continue
        filtered = sorted(set(filtered))
        if guids and not filtered:
            skill_guids_all_filtered_out = True
        return filtered

    try:
        if path:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                handle = open(path, "w+b")
                placeholder = {
                    "type": "meta",
                    "generated_ts": time.time(),
                    "zone_id": zone_id,
                    "notes": ["pending_scan"],
                }
                handle.write(_build_padded_meta_line(placeholder))
            except Exception as exc:
                ok = False
                write_error = repr(exc)
                handle = None

        sim = None
        try:
            sim = sim_info.get_sim_instance()
        except Exception:
            sim = None

        for raw in push_utils.iter_objects():
            if record_limit_hit or write_failed:
                break
            obj = _resolve_object(raw)
            if len(sample) < 10:
                sample_target = obj or raw
                sample.append(
                    {
                        "raw_type": type(raw).__name__,
                        "resolved_type": type(obj).__name__ if obj is not None else None,
                        "resolved": obj is not None,
                        "has_get_super_affordances": bool(
                            hasattr(sample_target, "get_super_affordances")
                        ),
                        "has_get_target_super_affordances": bool(
                            hasattr(sample_target, "get_target_super_affordances")
                        ),
                        "has_super_affordances_attr": bool(
                            hasattr(sample_target, "super_affordances")
                        ),
                        "has__super_affordances_attr": bool(
                            hasattr(sample_target, "_super_affordances")
                        ),
                    }
                )
            if obj is None:
                unresolved_objects += 1
                continue
            if getattr(obj, "is_sim", False) is True:
                skipped_sims += 1
                continue
            if not include_sims and _is_sim_object(obj):
                skipped_sims += 1
                continue
            if max_objects is not None and scanned_objects >= max_objects:
                truncated = True
                notes.append("max_objects cap reached")
                break
            scanned_objects += 1

            try:
                affordance_list = list(push_utils.iter_super_affordances(obj, sim=sim))
            except Exception as exc:
                obj_id = _safe_get(obj, "id")
                notes.append(
                    f"iter_super_affordances_error obj_id={obj_id} err={exc!r}"
                )
                continue

            scanned_affordances += len(affordance_list)

            if not affordance_list:
                continue

            if max_affordances_per_object is not None and len(affordance_list) > max_affordances_per_object:
                truncated = True
                notes.append(
                    f"affordance cap applied obj_id={_safe_get(obj, 'id')} count={len(affordance_list)}"
                )

            capped_affordances = (
                affordance_list
                if max_affordances_per_object is None
                else affordance_list[:max_affordances_per_object]
            )

            for aff in capped_affordances:
                if record_limit_hit or write_failed:
                    break
                safe_ok, safe_reason = push_utils.is_safe_for_script_push(aff)
                allow_user_directed = _bool_attr_or_call(
                    _safe_get(aff, "allow_user_directed", False)
                )
                allow_autonomous = _bool_attr_or_call(
                    _safe_get(aff, "allow_autonomous", False)
                )
                is_cheat = _safe_bool(aff, "cheat", default=False)
                is_debug = _safe_bool(aff, "debug", default=False)
                is_picker_like = _is_picker_like(aff)
                is_staging_like = _is_staging_like(aff)

                autonomy_ad_guids = []
                ad_map = _safe_get(aff, "_autonomy_ads")
                if isinstance(ad_map, dict):
                    autonomy_ad_guids = _extract_guid64s_from_mapping_keys(ad_map)
                commodity_flag_guids = []
                flags = _safe_get(aff, "_commodity_flags")
                if flags:
                    try:
                        flags_list = list(flags)
                    except Exception:
                        flags_list = []
                    commodity_flag_guids = sorted(
                        {guid for guid in (_tuning_guid64(item) for item in flags_list) if guid is not None}
                    )

                loot_ref_guid_candidates = set()
                skill_guid_candidates = set()
                skill_gain_guid_candidates = set()
                skill_gain_evidence = []
                skill_loot_sig = None
                skill_loot_call_ok = False
                skill_loot_err = None

                def _add_skill_guids(value):
                    if value is None:
                        return
                    skill_guid_candidates.update(_walk_for_guid64s(value))

                if hasattr(aff, "skill_loot_data"):
                    _add_skill_guids(_safe_get(aff, "skill_loot_data"))
                if hasattr(aff, "_skill_loot_data"):
                    _add_skill_guids(_safe_get(aff, "_skill_loot_data"))
                if hasattr(aff, "_get_skill_loot_data"):
                    getter = _safe_get(aff, "_get_skill_loot_data")
                    skill_loot_sig = _safe_signature_str(getter)
                    ok_call, loot_result, err = _safe_call_with_sim_guess(getter, sim)
                    skill_loot_call_ok = ok_call
                    if not ok_call and err:
                        skill_loot_err = err[:180]
                    if ok_call:
                        loot_ref_guid_candidates.update(_walk_for_guid64s(loot_result))
                        skill_guid_candidates.update(loot_ref_guid_candidates)
                getter = _safe_get(aff, "get_skill_loot_data")
                if callable(getter):
                    ok_call, loot_result, err = _safe_call_with_sim_guess(getter, sim)
                    if ok_call:
                        skill_guid_candidates.update(_walk_for_guid64s(loot_result))
                        loot_ref_guid_candidates.update(_walk_for_guid64s(loot_result))
                    elif err:
                        if skill_loot_err:
                            skill_loot_err = f"{skill_loot_err}; get_skill_loot_data={err[:180]}"
                        else:
                            skill_loot_err = f"get_skill_loot_data={err[:180]}"
                for attr_name in ("loot", "loots", "loot_actions"):
                    if hasattr(aff, attr_name):
                        value = _safe_get(aff, attr_name)
                        _add_skill_guids(value)
                        loot_ref_guid_candidates.update(_walk_for_guid64s(value))
                gain_candidates, gain_evidence = _extract_skill_gain_guid_candidates(aff)
                if gain_candidates:
                    skill_gain_guid_candidates.update(gain_candidates)
                if gain_evidence:
                    skill_gain_evidence.extend(gain_evidence)
                loot_ref_guids = sorted(loot_ref_guid_candidates)
                try:
                    Types = sims4.resources.Types
                    action_mgr = services.get_instance_manager(getattr(Types, "ACTION"))
                except Exception:
                    action_mgr = None
                if action_mgr is not None:
                    for loot_guid in loot_ref_guids:
                        try:
                            tuning = action_mgr.get(loot_guid)
                        except Exception:
                            tuning = None
                        if tuning is not None:
                            skill_guid_candidates.update(_walk_for_guid64s(tuning))

                if is_cheat or is_debug or is_picker_like or is_staging_like:
                    filtered_flags += 1
                    continue
                if not include_non_autonomous and not (
                    allow_user_directed or allow_autonomous
                ):
                    filtered_non_autonomy += 1
                    continue

                if max_records is not None and record_lines_written >= max_records:
                    truncated = True
                    notes.append("max_records cap reached")
                    record_limit_hit = True
                    break

                skill_guids = _filter_skill_guids(skill_guid_candidates)
                skill_gain_guids = _filter_skill_guids(skill_gain_guid_candidates)
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
                    "safe_push": bool(safe_ok),
                    "safe_push_reason": _trim_repr(safe_reason, limit=120)
                    if safe_reason is not None
                    else None,
                    "is_picker_like": is_picker_like,
                    "is_staging_like": is_staging_like,
                    "is_cheat": is_cheat,
                    "is_debug": is_debug,
                    "has_tests": _has_tests(aff),
                    "advertisement_hint": _advertisement_hint(aff),
                    "autonomy_ad_guids": autonomy_ad_guids,
                    "commodity_flag_guids": commodity_flag_guids,
                    "loot_ref_guids": loot_ref_guids,
                    "skill_guids": skill_guids,
                    "skill_gain_guids": skill_gain_guids,
                    "skill_gain_evidence": skill_gain_evidence,
                    "skill_loot_sig": skill_loot_sig,
                    "skill_loot_call_ok": skill_loot_call_ok,
                    "skill_loot_err": skill_loot_err,
                }
                if handle is not None:
                    try:
                        line = json.dumps(
                            record, ensure_ascii=False, separators=(",", ":")
                        ).encode("utf-8")
                        handle.write(line + b"\n")
                    except Exception as exc:
                        ok = False
                        write_error = repr(exc)
                        write_failed = True
                        notes.append(f"write_error err={write_error}")
                        break
                if loot_ref_guids:
                    records_with_loot_ref_guids += 1
                if skill_guids:
                    records_with_skill_guids += 1
                    skill_guids_total += len(skill_guids)
                record_lines_written += 1
    except Exception as exc:
        ok = False
        notes.append(f"scan error: {exc}")

    if scanned_affordances == 0 and scanned_objects > 0:
        notes.append(
            "ZERO_AFFORDANCES: iter_super_affordances returned empty for all scanned objects; "
            "check iter_objects source or sim resolution"
        )
    if scanned_affordances > 0 and record_lines_written == 0:
        notes.append(
            "all_affordances_filtered_out; check allow_ud/allow_auto extraction and picker/debug flags"
        )
    if skill_guid_filter_no_instance_managers:
        notes.append("skill_guid_filter_no_instance_managers")
    if skill_guids_all_filtered_out:
        notes.append("skill_guids_all_filtered_out")
    notes = [note for note in notes if note][:10]
    written_records = record_lines_written + (1 if handle is not None else 0)
    meta_record = {
        "type": "meta",
        "generated_ts": time.time(),
        "zone_id": zone_id,
        "records_total": record_lines_written,
        "scanned_objects": scanned_objects,
        "unresolved_objects": unresolved_objects,
        "scanned_affordances": scanned_affordances,
        "written_records": written_records,
        "truncated": bool(truncated),
        "skipped_sims": skipped_sims,
        "filtered_flags": filtered_flags,
        "filtered_non_autonomy": filtered_non_autonomy,
        "records_with_loot_ref_guids": records_with_loot_ref_guids,
        "records_with_skill_guids": records_with_skill_guids,
        "skill_guids_total": skill_guids_total,
        "notes": notes,
    }
    if sample:
        meta_record["sample"] = sample
    if handle is not None:
        try:
            handle.seek(0)
            handle.write(_build_padded_meta_line(meta_record))
            handle.flush()
            write_ok = True if not write_failed else False
        except Exception as exc:
            ok = False
            write_error = repr(exc)
        try:
            handle.close()
        except Exception:
            pass

    file_exists = bool(path and os.path.exists(path))
    file_bytes = os.path.getsize(path) if file_exists else 0

    return {
        "ok": ok,
        "path": path,
        "catalog_path": path,
        "write_ok": write_ok,
        "write_error": write_error,
        "file_exists": file_exists,
        "file_bytes": file_bytes,
        "scanned_objects": scanned_objects,
        "unresolved_objects": unresolved_objects,
        "scanned_affordances": scanned_affordances,
        "written_records": written_records,
        "truncated": truncated,
        "skipped_sims": skipped_sims,
        "filtered_flags": filtered_flags,
        "filtered_non_autonomy": filtered_non_autonomy,
        "notes": notes,
    }
