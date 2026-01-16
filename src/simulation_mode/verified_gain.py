import json
import os
import time


def default_data(now_ts):
    return {
        "schema_version": 1,
        "created_ts": now_ts,
        "updated_ts": now_ts,
        "verified": {},
        "invalidated": {},
    }


def load(path):
    now_ts = time.time()
    if not path or not os.path.exists(path):
        return default_data(now_ts)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return default_data(now_ts)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return default_data(now_ts)
    if "verified" not in data or "invalidated" not in data:
        return default_data(now_ts)
    return data


def save_atomic(path, data):
    if not path or not isinstance(data, dict):
        return False
    folder = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        pass
    now_ts = time.time()
    data["updated_ts"] = now_ts
    data.setdefault("created_ts", now_ts)
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False


def _nest(data, k1, k2, k3):
    if not isinstance(data, dict):
        return {}
    k1 = str(k1)
    k2 = str(k2)
    k3 = str(k3)
    level1 = data.setdefault(k1, {})
    level2 = level1.setdefault(k2, {})
    return level2.setdefault(k3, {})


def mark_verified(data, skill_guid, obj_def_id, aff_guid64, ts):
    if not isinstance(data, dict):
        return False
    verified = data.setdefault("verified", {})
    leaf = _nest(verified, skill_guid, obj_def_id, aff_guid64)
    wins = leaf.get("wins", 0)
    try:
        wins = int(wins)
    except Exception:
        wins = 0
    leaf["wins"] = wins + 1
    leaf["last_ts"] = ts
    return True


def mark_invalid(data, skill_guid, obj_def_id, aff_guid64, ts):
    if not isinstance(data, dict):
        return False
    invalidated = data.setdefault("invalidated", {})
    leaf = _nest(invalidated, skill_guid, obj_def_id, aff_guid64)
    fails = leaf.get("fails", 0)
    try:
        fails = int(fails)
    except Exception:
        fails = 0
    leaf["fails"] = fails + 1
    leaf["last_ts"] = ts
    return True


def get_entry(data, kind, skill_guid, obj_def_id, aff_guid64):
    if not isinstance(data, dict):
        return None
    container = data.get(kind)
    if not isinstance(container, dict):
        return None
    entry = container.get(str(skill_guid))
    if not isinstance(entry, dict):
        return None
    entry = entry.get(str(obj_def_id))
    if not isinstance(entry, dict):
        return None
    leaf = entry.get(str(aff_guid64))
    return leaf if isinstance(leaf, dict) else None


def get_status(data, skill_guid, obj_def_id, aff_guid64):
    if get_entry(data, "verified", skill_guid, obj_def_id, aff_guid64):
        return "verified"
    if get_entry(data, "invalidated", skill_guid, obj_def_id, aff_guid64):
        return "invalid"
    return "unknown"


def totals(data):
    verified_pairs_total = 0
    invalid_pairs_total = 0
    verified = data.get("verified") if isinstance(data, dict) else {}
    invalidated = data.get("invalidated") if isinstance(data, dict) else {}
    if isinstance(verified, dict):
        for skill_block in verified.values():
            if not isinstance(skill_block, dict):
                continue
            for obj_block in skill_block.values():
                if not isinstance(obj_block, dict):
                    continue
                for leaf in obj_block.values():
                    if isinstance(leaf, dict):
                        verified_pairs_total += 1
    if isinstance(invalidated, dict):
        for skill_block in invalidated.values():
            if not isinstance(skill_block, dict):
                continue
            for obj_block in skill_block.values():
                if not isinstance(obj_block, dict):
                    continue
                for leaf in obj_block.values():
                    if isinstance(leaf, dict):
                        invalid_pairs_total += 1
    return {
        "verified_pairs_total": verified_pairs_total,
        "invalid_pairs_total": invalid_pairs_total,
        "verified_skill_keys": len(verified) if isinstance(verified, dict) else 0,
        "invalid_skill_keys": len(invalidated) if isinstance(invalidated, dict) else 0,
    }
