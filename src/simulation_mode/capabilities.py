import json
import os
import time

from simulation_mode import settings as sm_settings


_CAP_CACHE = None
_CAP_CACHE_TS = None


def get_capabilities_path():
    filename = sm_settings.get_str("capabilities_filename", "")
    if not filename:
        filename = "simulation-mode-capabilities.json"
    settings_path = getattr(sm_settings.settings, "settings_path", None)
    if isinstance(settings_path, str) and settings_path.lower().endswith(".txt"):
        folder = os.path.dirname(os.path.abspath(settings_path))
        return os.path.join(folder, filename)
    cfg = sm_settings.get_config_path()
    folder = os.path.dirname(os.path.abspath(cfg))
    return os.path.join(folder, filename)


def load_capabilities():
    global _CAP_CACHE
    global _CAP_CACHE_TS
    path = get_capabilities_path()
    if not path or not os.path.exists(path):
        return None
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        mtime = None
    if _CAP_CACHE is not None and _CAP_CACHE_TS == mtime:
        return _CAP_CACHE
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None
    _CAP_CACHE = data
    _CAP_CACHE_TS = mtime
    return data


def _stringify_keys(value):
    if isinstance(value, dict):
        return {str(k): _stringify_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify_keys(item) for item in value]
    return value


def write_capabilities(data):
    if data is None:
        return False, "missing data"
    path = get_capabilities_path()
    if not path:
        return False, "missing path"
    payload = _stringify_keys(data)
    folder = os.path.dirname(path)
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception as exc:
        return False, repr(exc)
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
    except Exception as exc:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False, repr(exc)
    return True, None


def build_capabilities_from_catalog_jsonl(catalog_path: str):
    data = {
        "meta": None,
        "by_ad_guid": {},
        "by_loot_guid": {},
        "by_skill_guid": {},
        "generated_ts": time.time(),
    }
    if not catalog_path or not os.path.exists(catalog_path):
        return data
    observed_counts = {}

    def _add_to_index(index, guid, entry):
        key = str(guid)
        bucket = index.setdefault(key, [])
        bucket.append(entry)

    try:
        with open(catalog_path, "r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except Exception:
                    continue
                if record.get("type") == "meta":
                    data["meta"] = record
                    continue
                for guid in record.get("skill_guids") or []:
                    key = str(guid)
                    observed_counts[key] = observed_counts.get(key, 0) + 1
                safe_push = bool(record.get("safe_push"))
                if not safe_push:
                    continue
                if record.get("is_picker_like") or record.get("is_cheat") or record.get("is_debug") or record.get("is_staging_like"):
                    continue
                obj_def_id = record.get("obj_def_id")
                aff_guid64 = record.get("aff_guid64")
                if obj_def_id is None or aff_guid64 is None:
                    continue
                entry = {
                    "obj_def_id": int(obj_def_id),
                    "obj_name": record.get("obj_name"),
                    "aff_guid64": int(aff_guid64),
                    "aff_name": record.get("aff_name"),
                    "allow_autonomous": bool(record.get("allow_autonomous")),
                    "allow_user_directed": bool(record.get("allow_user_directed")),
                    "safe_push": True,
                }
                for guid in record.get("autonomy_ad_guids") or []:
                    _add_to_index(data["by_ad_guid"], guid, entry)
                for guid in record.get("loot_ref_guids") or []:
                    _add_to_index(data["by_loot_guid"], guid, entry)
                for guid in record.get("skill_guids") or []:
                    _add_to_index(data["by_skill_guid"], guid, entry)
    except Exception:
        return data
    meta = data.get("meta") or {}
    by_skill = data.get("by_skill_guid") or {}
    if isinstance(by_skill, dict):
        keys = list(by_skill.keys())
        meta["by_skill_guid_keys"] = len(keys)
        meta["by_skill_guid_entries_total"] = sum(
            len(by_skill.get(key, [])) for key in keys
        )
    else:
        meta["by_skill_guid_keys"] = 0
        meta["by_skill_guid_entries_total"] = 0
    meta["skill_guid_observed_counts"] = observed_counts
    data["meta"] = meta
    return data


def get_candidates_for_ad_guid(guid64: int, caps: dict):
    if not guid64 or not caps:
        return []
    index = caps.get("by_ad_guid") or {}
    return list(index.get(str(guid64)) or [])


def get_candidates_for_loot_guid(guid64: int, caps: dict):
    if not guid64 or not caps:
        return []
    index = caps.get("by_loot_guid") or {}
    return list(index.get(str(guid64)) or [])


def get_candidates_for_skill_guid(guid64: int, caps: dict):
    if not guid64 or not caps:
        return []
    index = caps.get("by_skill_guid") or {}
    return list(index.get(str(guid64)) or [])


def is_skill_kernel_valid(caps: dict):
    if caps is None:
        return False, "caps_missing"
    meta = caps.get("meta", {})
    if meta.get("truncated") is True:
        return False, "caps_truncated"
    by_skill = caps.get("by_skill_guid") or {}
    if not by_skill or len(by_skill) == 0:
        return False, "by_skill_guid_empty"
    return True, "ok"


def _current_zone_id():
    try:
        services = __import__("services")
        zone = getattr(services, "current_zone", None)
        if callable(zone):
            zone = zone()
        if zone is None:
            return None
        return getattr(zone, "id", None) or getattr(zone, "zone_id", None)
    except Exception:
        return None


def ensure_capabilities(sim_info=None, force_rebuild=False):
    caps = load_capabilities()
    if not force_rebuild and caps is not None:
        meta = caps.get("meta") if isinstance(caps, dict) else None
        if meta is not None:
            zone_id = meta.get("zone_id")
            if zone_id is None or zone_id == _current_zone_id():
                return caps
        elif caps:
            return caps
    if not sm_settings.get_bool("capabilities_auto_build_on_enable", True) and not force_rebuild:
        return caps
    try:
        object_catalog = __import__("simulation_mode.object_catalog", fromlist=["scan_zone_catalog"])
        result = object_catalog.scan_zone_catalog(
            sim_info,
            include_sims=None,
            include_non_autonomous=None,
            max_objects=None,
            max_affordances_per_object=None,
            filename=None,
        )
    except Exception:
        return caps
    if not result or not result.get("ok"):
        return caps
    if result.get("truncated"):
        return caps if caps is not None else None
    catalog_path = result.get("catalog_path") or result.get("path")
    built = build_capabilities_from_catalog_jsonl(catalog_path)
    ok, _err = write_capabilities(built)
    if not ok:
        return built
    return load_capabilities() or built


def ensure_full_capabilities(sim_info=None, force_rebuild=False):
    caps = load_capabilities()
    meta = caps.get("meta") if isinstance(caps, dict) else None
    current_zone = _current_zone_id()
    meta_zone = meta.get("zone_id") if meta else None
    meta_truncated = meta.get("truncated") if meta else None
    if not force_rebuild and caps is not None:
        if meta_zone == current_zone and meta_truncated is False:
            return caps
    ensure_capabilities(sim_info, force_rebuild=True)
    return load_capabilities()
