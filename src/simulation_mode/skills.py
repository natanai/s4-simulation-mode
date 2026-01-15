import re

from simulation_mode.push_utils import (
    affordance_name,
    find_affordance_candidates,
    is_picker_affordance,
    iter_objects,
)

SKILL_RULES = {
    "programming": {
        "object_keywords": ["computer"],
        "affordance_keywords": [
            "practice programming",
            "program",
            "hack",
            "freelance",
            "browse web",
            "web",
        ],
    },
    "video_gaming": {
        "object_keywords": ["computer", "console"],
        "affordance_keywords": ["play game", "play", "gaming"],
    },
    "writing": {
        "object_keywords": ["computer"],
        "affordance_keywords": ["write", "practice writing"],
    },
    "cooking": {
        "object_keywords": ["fridge", "refriger", "stove", "oven"],
        "affordance_keywords": ["cook", "have quick meal", "quick meal", "prepare"],
    },
    "fitness": {
        "object_keywords": [
            "treadmill",
            "punch",
            "weights",
            "workout",
            "basketball",
            "pullup",
            "exercise",
            "bike",
            "bicycle",
            "stationary",
        ],
        "affordance_keywords": [
            "workout",
            "practice",
            "train",
            "jog",
            "ride",
            "cycle",
            "spin",
            "cardio",
            "strength",
            "run",
        ],
    },
    "logic": {
        "object_keywords": ["chess", "telescope", "microscope"],
        "affordance_keywords": ["play chess", "use", "research", "practice logic"],
    },
    "painting": {
        "object_keywords": ["easel"],
        "affordance_keywords": ["paint", "practice painting"],
    },
    "guitar": {
        "object_keywords": ["guitar"],
        "affordance_keywords": ["practice", "play"],
    },
    "piano": {
        "object_keywords": ["piano", "keyboard"],
        "affordance_keywords": ["practice", "play"],
    },
    "violin": {
        "object_keywords": ["violin"],
        "affordance_keywords": ["practice", "play"],
    },
    "charisma": {
        "object_keywords": ["mirror"],
        "affordance_keywords": ["practice speech", "practice", "psych up", "pep talk"],
    },
    "mischief": {
        "object_keywords": ["computer"],
        "affordance_keywords": ["troll", "mischief", "prank"],
    },
    "acting": {
        "object_keywords": ["mirror", "computer"],
        "affordance_keywords": ["practice acting", "acting", "research acting", "practice"],
    },
}

_AFFORDANCE_BLOCK_TOKENS = (
    "offer",
    "ask",
    "invite",
    "mentor",
    "teach",
    "discuss",
    "chat",
    "social",
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _normalize_key(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return normalized or None


def skill_key_from_name(name):
    if not name:
        return None
    normalized = _norm(name)
    for key in SKILL_RULES:
        if _norm(key) in normalized:
            return key
    return _normalize_key(name)


def _get_object_label(obj):
    parts = [getattr(obj.__class__, "__name__", None)]
    definition = getattr(obj, "definition", None)
    if definition is not None:
        name = getattr(definition, "name", None)
        if name:
            parts.append(name)
    parts.append(str(obj))
    return " ".join(part for part in parts if part).lower()


def _value_mentions_target_sim(value):
    if value is None:
        return False
    text = repr(value).lower()
    return "targetsim" in text or "target_sim" in text or "participanttype.targetsim" in text


def _affordance_requires_target_sim(affordance):
    for attr in (
        "target_type",
        "target_types",
        "target_sim_type",
        "participant_type",
        "participant_types",
        "participants",
    ):
        value = getattr(affordance, attr, None)
        if value is None:
            continue
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if _value_mentions_target_sim(value):
            return True
        if isinstance(value, (list, tuple, set)):
            if any(_value_mentions_target_sim(item) for item in value):
                return True
    return False


def skill_affordance_block_reason(affordance):
    if affordance is None:
        return "none"
    if is_picker_affordance(affordance):
        return "picker_affordance"
    name = affordance_name(affordance)
    class_name = getattr(type(affordance), "__name__", str(affordance)).lower()
    if any(token in name for token in _AFFORDANCE_BLOCK_TOKENS):
        return "blocked_name_token"
    if any(token in class_name for token in _AFFORDANCE_BLOCK_TOKENS):
        return "blocked_class_token"
    if _affordance_requires_target_sim(affordance):
        return "requires_target_sim"
    return None


def _iter_matching_objects(objects, object_keywords):
    if not object_keywords:
        return
    for obj in objects:
        try:
            in_inventory = getattr(obj, "is_in_inventory", None)
            if in_inventory is True:
                continue
            if callable(in_inventory) and in_inventory():
                continue
            hidden = getattr(obj, "is_hidden", None)
            if hidden is True:
                continue
            if callable(hidden) and hidden():
                continue
            if getattr(obj, "is_deleted", False):
                continue
            label = _get_object_label(obj)
            norm_label = _norm(label)
            if not any(_norm(keyword) in norm_label for keyword in object_keywords):
                continue
        except Exception:
            continue
        yield obj


def _sort_affordances(affordances, target_obj, rule):
    object_def_name = ""
    definition = getattr(target_obj, "definition", None)
    if definition is not None:
        object_def_name = getattr(definition, "name", "") or ""
    object_def_name = object_def_name.lower()
    tokens = [
        token.lower()
        for token in rule.get("object_keywords", []) + rule.get("affordance_keywords", [])
        if token
    ]

    def _score(aff):
        name = affordance_name(aff)
        score = 0
        for token in tokens:
            if token in name or (object_def_name and token in object_def_name):
                score += 1
        return score

    return sorted(affordances, key=_score, reverse=True)


def resolve_skill_action(sim, skill_key):
    rule = SKILL_RULES.get(skill_key)
    if rule is None:
        return {
            "ok": False,
            "target_obj": None,
            "affordances": [],
            "reason": "no_rule",
        }
    objects = list(iter_objects())
    matched_objects = 0
    for obj in _iter_matching_objects(objects, rule.get("object_keywords", [])):
        matched_objects += 1
        try:
            candidates = find_affordance_candidates(
                obj, rule.get("affordance_keywords", []), sim=sim
            )
        except Exception:
            continue
        if not candidates:
            continue
        filtered = []
        for affordance in candidates:
            try:
                reason = skill_affordance_block_reason(affordance)
                if reason is not None:
                    continue
            except Exception:
                continue
            filtered.append(affordance)
        if not filtered:
            continue
        return {
            "ok": True,
            "target_obj": obj,
            "affordances": _sort_affordances(filtered, obj, rule),
            "reason": "ok",
        }
    if matched_objects == 0:
        reason = "no_object"
    else:
        reason = "no_affordance"
    return {
        "ok": False,
        "target_obj": None,
        "affordances": [],
        "reason": reason,
    }
